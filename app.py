import streamlit as st
import json
import os
import re
import pandas as pd
from urllib.parse import urlparse
from tavily import TavilyClient
from google import genai
from google.genai import types

st.set_page_config(
    page_title="企業情報一括検索ツール",
    layout="wide"
)

st.title("企業情報一括検索ツール")

# ==========================================
# APIキー
# ==========================================
tavily_api_key = (
    os.getenv("TAVILY_API_KEY")
    or st.secrets.get("TAVILY_API_KEY", "")
)

gemini_key = (
    os.getenv("GEMINI_API_KEY")
    or st.secrets.get("GEMINI_API_KEY", "")
)

# ==========================================
# セッションステート
# ==========================================
if "search_history" not in st.session_state:
    st.session_state.search_history = []

if "result_cache" not in st.session_state:
    st.session_state.result_cache = {}

# ==========================================
# 法人格一覧
#
# 株式会社だけでなく法人格全般を扱う
# ==========================================
LEGAL_FORMS = [
    "株式会社",
    "有限会社",
    "合同会社",
    "合資会社",
    "合名会社",
    "一般社団法人",
    "一般財団法人",
    "公益社団法人",
    "公益財団法人",
    "学校法人",
    "医療法人",
    "社会福祉法人",
    "宗教法人",
    "特定非営利活動法人",
    "NPO法人",
    "独立行政法人",
    "国立大学法人",
    "地方独立行政法人",
    "相互会社",
    "信用金庫",
    "信用組合",
]

# ==========================================
# 第三者サイト除外
# ==========================================
def is_excluded_domain(domain: str):

    if not domain:
        return True

    excluded_domains = [
        "wikipedia.org",
        "yahoo.co.jp",
        "news.yahoo.co.jp",
        "nikkei.com",
        "toyokeizai.net",
        "mynavi.jp",
        "rikunabi.com",
        "en-japan.com",
        "wantedly.com",
        "indeed.com",
        "onecareer.jp",
        "doda.jp",
        "bizreach.jp",
        "green-japan.com",
        "metoree.com",
        "navitime.co.jp",
        "irbank.net",
        "xn--pckua2a7gp15o89zb.com",
        "pr.mono.ipros.com", 
        "ipros.com",
        "atengineer.com",
        "baseconnect.in",
        "houjin.jp",
        "prtimes.jp"
    ]

    return any(
        domain == excluded
        or domain.endswith("." + excluded)
        for excluded in excluded_domains
    )

# ==========================================
# URL → ドメイン
# ==========================================
def extract_domain(url: str):

    try:

        parsed = urlparse(url)

        if not parsed.netloc:
            return None

        domain = parsed.netloc.lower()

        if domain.startswith("www."):
            domain = domain[4:]

        return domain

    except Exception:
        return None

# ==========================================
# 会社名正規化
# ==========================================
def normalize_company_name(name: str):

    if not name:
        return ""

    name = str(name)

    # 全角・半角スペース除去
    name = re.sub(
        r"[\s ]+",
        "",
        name
    )

    # 全角括弧等は極力そのまま維持
    return name.strip()

# ==========================================
# 法人格情報を抽出
#
# 例：
# 株式会社ニデック
# → core = ニデック
# → legal_form = 株式会社
# → position = front
#
# ニデック株式会社
# → core = ニデック
# → legal_form = 株式会社
# → position = back
# ==========================================
def parse_legal_entity(name: str):

    normalized = normalize_company_name(
        name
    )

    if not normalized:
        return {
            "original": "",
            "core": "",
            "legal_form": None,
            "position": "unknown"
        }

    # 長い法人格から先に確認
    sorted_forms = sorted(
        LEGAL_FORMS,
        key=len,
        reverse=True
    )

    for form in sorted_forms:

        if normalized.startswith(form):

            core = normalized[len(form):]

            if core:

                return {
                    "original": normalized,
                    "core": core,
                    "legal_form": form,
                    "position": "front"
                }

        if normalized.endswith(form):

            core = normalized[:-len(form)]

            if core:

                return {
                    "original": normalized,
                    "core": core,
                    "legal_form": form,
                    "position": "back"
                }

    return {
        "original": normalized,
        "core": normalized,
        "legal_form": None,
        "position": "unknown"
    }

# ==========================================
# 候補テキストから法人名候補を判定
#
# 「株式会社」だけではなく、
# 法人格＋位置＋法人名全体を見る
# ==========================================
def candidate_entity_relation(
    input_company: str,
    candidate_text: str
):
    """
    戻り値：

    "match"
        入力会社名と同一と考えられる

    "mismatch"
        同じコア名だが法人格・位置などが異なる

    "unknown"
        候補テキストから確定できない
    """

    input_info = parse_legal_entity(
        input_company
    )

    if not input_info["core"]:
        return "unknown"

    text = normalize_company_name(
        candidate_text
    )

    input_original = input_info["original"]
    input_core = input_info["core"]
    input_form = input_info["legal_form"]

    # --------------------------------------
    # 【重要】前株・後株の逆パターンを厳密排除
    # --------------------------------------
    if input_core and input_form:
        opposite_company = ""
        if input_info["position"] == "front":
            opposite_company = f"{input_core}{input_form}"
        elif input_info["position"] == "back":
            opposite_company = f"{input_form}{input_core}"
        
        # 候補テキストの中に「逆位置の社名」があり、かつ「正しい社名」が無い場合は確定で別法人
        if opposite_company and (opposite_company in text) and (input_original not in text):
            return "mismatch"

    # --------------------------------------
    # 入力会社名そのものが書かれている
    # --------------------------------------
    if input_original and input_original in text:
        return "match"

    if not input_core:
        return "unknown"

    # --------------------------------------
    # 候補テキスト中に
    # 同じコア＋法人格が存在するか調べる
    # --------------------------------------
    for form in sorted(
        LEGAL_FORMS,
        key=len,
        reverse=True
    ):

        # 前株
        front_pattern = (
            re.escape(form)
            + re.escape(input_core)
        )

        # 後株
        back_pattern = (
            re.escape(input_core)
            + re.escape(form)
        )

        has_front = re.search(
            front_pattern,
            text
        )

        has_back = re.search(
            back_pattern,
            text
        )

        if not has_front and not has_back:
            continue

        # 入力会社と同一法人格・同一位置
        if (
            form == input_form
            and (
                (
                    input_info["position"] == "front"
                    and has_front
                )
                or
                (
                    input_info["position"] == "back"
                    and has_back
                )
            )
        ):
            return "match"

        # 同じコア名だが、
        # 法人格または位置が異なる
        return "mismatch"

    return "unknown"

# ==========================================
# Tavily検索
# ==========================================
def fetch_tavily_results(
    query: str,
    api_key: str
):

    try:

        client = TavilyClient(
            api_key=api_key
        )

        # APIレベルで除外するドメインリスト
        exclude_list = [
            "wikipedia.org", "yahoo.co.jp", "news.yahoo.co.jp", "nikkei.com",
            "toyokeizai.net", "mynavi.jp", "rikunabi.com", "en-japan.com",
            "wantedly.com", "indeed.com", "onecareer.jp", "doda.jp",
            "bizreach.jp", "green-japan.com", "metoree.com", "navitime.co.jp",
            "irbank.net", "xn--pckua2a7gp15o89zb.com", "pr.mono.ipros.com", 
            "ipros.com", "atengineer.com", "baseconnect.in", "houjin.jp", "prtimes.jp"
        ]

        response = client.search(
            query=query.strip().replace(
                "`",
                ""
            ),
            search_depth="basic",
            max_results=20,
            exclude_domains=exclude_list
        )

        results = []

        for item in response.get(
            "results",
            []
        ):

            results.append({
                "title": item.get(
                    "title",
                    ""
                ),
                "url": item.get(
                    "url",
                    ""
                ),
                "snippet": item.get(
                    "content",
                    ""
                )
            })

        return results

    except Exception as e:

        st.error(
            f"Tavily検索エラー: {str(e)}"
        )

        st.exception(e)

        return []

# ==========================================
# 公式候補スコア
# ==========================================
def score_official_candidate(
    company: str,
    result: dict
):

    title = result.get(
        "title",
        ""
    )

    snippet = result.get(
        "snippet",
        ""
    )

    url = result.get(
        "url",
        ""
    )

    title_lower = title.lower()
    snippet_lower = snippet.lower()
    url_lower = url.lower()

    score = 0

    # --------------------------------------
    # 入力会社名そのもの
    # --------------------------------------
    if normalize_company_name(
        company
    ).lower() in title_lower:
        score += 25

    if normalize_company_name(
        company
    ).lower() in snippet_lower:
        score += 15

    # --------------------------------------
    # 法人格を含む会社名候補
    # --------------------------------------
    relation = candidate_entity_relation(
        company,
        title + "\n" + snippet
    )

    if relation == "match":
        score += 30

    elif relation == "mismatch":
        score -= 100

    # --------------------------------------
    # 会社概要らしいタイトル
    # --------------------------------------
    official_words = [
        "会社概要",
        "会社情報",
        "企業情報",
        "企業概要",
        "company profile",
        "corporate profile",
        "about us",
        "about",
        "profile",
        "outline",
        "corporate",
        "company"
    ]

    for word in official_words:

        if word.lower() in title_lower:
            score += 5

    # --------------------------------------
    # 会社概要らしいURL
    # --------------------------------------
    official_paths = [
        "/company",
        "/corporate",
        "/about",
        "/about-us",
        "/about_us",
        "/profile",
        "/outline"
    ]

    for word in official_paths:

        if word in url_lower:
            score += 3

    # --------------------------------------
    # 明らかな第三者サイト
    # --------------------------------------
    domain = extract_domain(
        url
    )

    if is_excluded_domain(
        domain
    ):
        score -= 100

    return score

# ==========================================
# 公式候補取得
#
# ここで明らかな別法人を除外する
# ==========================================
def find_official_candidates(
    company: str,
    results: list
):

    candidates = []

    for result in results:

        url = result.get(
            "url",
            ""
        )

        title = result.get(
            "title",
            ""
        )

        snippet = result.get(
            "snippet",
            ""
        )

        domain = extract_domain(
            url
        )

        if not domain:
            continue

        if is_excluded_domain(
            domain
        ):
            continue

        candidate_text = (
            title
            + "\n"
            + snippet
        )

        relation = candidate_entity_relation(
            company,
            candidate_text
        )

        # ----------------------------------
        # 同じ法人名の逆位置・別法人格など
        # 明確な不一致は候補から除外
        # ----------------------------------
        if relation == "mismatch":
            continue

        score = score_official_candidate(
            company,
            result
        )

        candidates.append({
            "score": score,
            "domain": domain,
            "title": title,
            "url": url,
            "snippet": snippet,
            "entity_relation": relation
        })

    candidates.sort(
        key=lambda x: x["score"],
        reverse=True
    )

    # URL重複除去
    unique_candidates = []

    seen_urls = set()

    for candidate in candidates:

        url = candidate["url"]

        if url in seen_urls:
            continue

        seen_urls.add(url)

        unique_candidates.append(
            candidate
        )

    return unique_candidates[:10]

# ==========================================
# 会社検索
# ==========================================
def search_company(
    company: str,
    api_key: str
):
    
    info = parse_legal_entity(company)

    # --------------------------------------
    # Q1：会社概要
    # ※前株・後株を厳格にするため "{company}" を維持
    # --------------------------------------
    q1 = f'"{company}" 会社概要'

    if info["legal_form"] and info["core"]:
        if info["position"] == "front":
            opposite_company = f"{info['core']}{info['legal_form']}"
            q1 += f' -"{opposite_company}"'
        elif info["position"] == "back":
            opposite_company = f"{info['legal_form']}{info['core']}"
            q1 += f' -"{opposite_company}"'

    q1_results = fetch_tavily_results(
        q1,
        api_key
    )

    official_candidates = (
        find_official_candidates(
            company,
            q1_results
        )
    )

    return {
        "q1_results": q1_results,
        "official_candidates":
            official_candidates
    }

# ==========================================
# JSONパース
# ==========================================
def safe_parse_json(
    text
):

    try:

        return json.loads(
            text
        )

    except json.JSONDecodeError:

        text = text.replace("```json", "").replace("
