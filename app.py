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
# 九州都道府県
# ==========================================
kyushu_prefectures = [
    "福岡",
    "佐賀",
    "長崎",
    "熊本",
    "大分",
    "宮崎",
    "鹿児島"
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
        "navitime.co.jp"
    ]

    if any(
        domain == excluded
        or domain.endswith("." + excluded)
        for excluded in excluded_domains
    ):
        return True

    lower_domain = domain.lower()

    if any(
        ng in lower_domain
        for ng in [
            "shukatsu",
            "tenshoku",
            "career",
            "job"
        ]
    ):
        return True

    return False


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
# 前株・後株解析
# ==========================================
def parse_company_name(company_name: str):

    name = company_name.strip()

    if name.startswith("株式会社"):
        return {
            "original": name,
            "core": name[len("株式会社"):],
            "position": "front"
        }

    if name.endswith("株式会社"):
        return {
            "original": name,
            "core": name[:-len("株式会社")],
            "position": "back"
        }

    return {
        "original": name,
        "core": name,
        "position": "unknown"
    }


# ==========================================
# 前株・後株一致判定
#
# True  = 一致
# False = 明確に逆
# None  = 判断不能
# ==========================================
def company_name_matches(
    input_company: str,
    text: str
):

    info = parse_company_name(
        input_company
    )

    original = info["original"]
    core = info["core"]
    position = info["position"]

    text = text or ""

    # 正式名称そのもの
    if original in text:
        return True

    # ------------------------------------------
    # 前株
    # 株式会社ニデック
    # ↓
    # ニデック株式会社
    # ------------------------------------------
    if position == "front":

        wrong_name = (
            core + "株式会社"
        )

        if wrong_name in text:
            return False

    # ------------------------------------------
    # 後株
    # ニデック株式会社
    # ↓
    # 株式会社ニデック
    # ------------------------------------------
    elif position == "back":

        wrong_name = (
            "株式会社" + core
        )

        if wrong_name in text:
            return False

    return None


# ==========================================
# 公式サイト候補スコアリング
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

    # ------------------------------------------
    # 前株・後株
    # ------------------------------------------
    title_match = company_name_matches(
        company,
        title
    )

    snippet_match = company_name_matches(
        company,
        snippet
    )

    # タイトルが明確に逆法人なら候補から除外
    if title_match is False:
        return -1000

    # 本文で明確に逆法人なら減点
    if snippet_match is False:
        score -= 100

    # 正式名称一致
    if title_match is True:
        score += 20

    if snippet_match is True:
        score += 10

    # ------------------------------------------
    # 会社名がタイトルにある
    # ------------------------------------------
    if company.lower() in title_lower:
        score += 10

    # ------------------------------------------
    # 会社名が本文にある
    # ------------------------------------------
    if company.lower() in snippet_lower:
        score += 5

    # ------------------------------------------
    # 公式らしいタイトル
    # ------------------------------------------
    official_title_words = [
        "公式",
        "会社概要",
        "会社情報",
        "企業情報",
        "コーポレート",
        "corporate",
        "company"
    ]

    for word in official_title_words:

        if word.lower() in title_lower:
            score += 5

    # ------------------------------------------
    # URLから会社名を推測
    # ------------------------------------------
    company_clean = (
        company
        .replace("株式会社", "")
        .replace("有限会社", "")
        .replace("合同会社", "")
        .replace("ホールディングス", "")
        .replace("ホールディング", "")
        .replace("HD", "")
        .replace(" ", "")
        .replace("　", "")
        .lower()
    )

    if company_clean:
        if company_clean in url_lower:
            score += 10

    # ------------------------------------------
    # 公式サイトらしいパス
    # ------------------------------------------
    official_path_words = [
        "/about",
        "/about_us",
        "/company",
        "/corporate",
        "/profile"
    ]

    for word in official_path_words:

        if word in url_lower:
            score += 2

    # ------------------------------------------
    # 第三者サイト
    # ------------------------------------------
    domain = extract_domain(url)

    if is_excluded_domain(domain):
        score -= 50

    return score


# ==========================================
# 公式ドメイン候補
# ==========================================
def find_official_domains(
    company: str,
    results: list
):

    candidates = []

    for result in results:

        title = result.get(
            "title",
            ""
        )

        snippet = result.get(
            "snippet",
            ""
        )

        combined_text = (
            title
            + "\n"
            + snippet
        )

        # --------------------------------------
        # 明確に前株・後株が逆なら除外
        # --------------------------------------
        if company_name_matches(
            company,
            combined_text
        ) is False:
            continue

        domain = extract_domain(
            result.get(
                "url",
                ""
            )
        )

        if not domain:
            continue

        if is_excluded_domain(domain):
            continue

        score = score_official_candidate(
            company,
            result
        )

        # ★ ここでは score < 0 でも捨てない
        #   正しい公式サイト候補を残す
        candidates.append({
            "domain": domain,
            "score": score,
            "title": title,
            "url": result.get(
                "url",
                ""
            )
        })

    candidates.sort(
        key=lambda x: x["score"],
        reverse=True
    )

    unique_candidates = []

    seen_domains = set()

    for candidate in candidates:

        domain = candidate["domain"]

        if domain in seen_domains:
            continue

        seen_domains.add(
            domain
        )

        unique_candidates.append(
            candidate
        )

    return unique_candidates[:3]


# ==========================================
# Tavily検索
# ==========================================
def fetch_tavily_results(
    query: str,
    api_key: str,
    include_domains=None
):

    try:

        client = TavilyClient(
            api_key=api_key
        )

        search_kwargs = {
            "query": query.strip().replace("`", ""),
            "search_depth": "basic",
            "max_results": 20
        }

        if include_domains:
            search_kwargs[
                "include_domains"
            ] = include_domains

        response = client.search(
            **search_kwargs
        )

        results = []

        for item in response.get(
            "results",
            []
        ):

            results.append({
                "title":
                    item.get(
                        "title",
                        ""
                    ),

                "url":
                    item.get(
                        "url",
                        ""
                    ),

                "snippet":
                    item.get(
                        "content",
                        ""
                    )
            })

        return results

    except Exception:
        return []


# ==========================================
# 1社分の検索
# ==========================================
def search_company_info(
    company_name: str,
    api_key: str
):

    clean_name = company_name.strip()

    # ======================================
    # Q1：会社概要・公式サイト
    # ======================================
    q1_query = (
        f'"{clean_name}" '
        f'会社概要 公式サイト'
    )

    res1 = fetch_tavily_results(
        q1_query,
        api_key
    )

    # ======================================
    # 公式ドメイン候補
    # ======================================
    official_candidates = (
        find_official_domains(
            clean_name,
            res1
        )
    )

    official_domains = []

    for candidate in official_candidates:

        # 最低限、公式候補らしいものだけ
        if candidate["score"] >= 10:

            official_domains.append(
                candidate["domain"]
            )

    # score >= 10 がない場合でも、
    # 候補自体があるなら最上位を使用する
    if (
        not official_domains
        and official_candidates
    ):

        official_domains = [
            official_candidates[0][
                "domain"
            ]
        ]

    # ======================================
    # Q1'：公式ドメイン内の会社概要検索
    # ======================================
    q1_official_results = []

    if official_domains:

        official_domain = (
            official_domains[0]
        )

        q1_official_query = (
            f'site:{official_domain} '
            f'会社概要 OR 企業情報 OR '
            f'会社情報 OR about OR outline'
        )

        q1_official_results = (
            fetch_tavily_results(
                q1_official_query,
                api_key,
                include_domains=[
                    official_domain
                ]
            )
        )

    # ======================================
    # ★ Q1は元検索結果＋公式ドメイン検索結果
    #    を両方残す
    # ======================================
    q1_results = []

    seen_q1_urls = set()

    for result in (
        res1
        + q1_official_results
    ):

        url = result.get(
            "url",
            ""
        )

        # URLがあり、重複していたら除外
        if url:

            if url in seen_q1_urls:
                continue

            seen_q1_urls.add(
                url
            )

        q1_results.append(
            result
        )

    # ======================================
    # Q2：公式サイト内 九州拠点検索
    # ======================================
    q2_results = []

    if official_domains:

        official_domain = (
            official_domains[0]
        )

        q2_queries = [

            # --------------------------------
            # 九州・都道府県＋一般拠点名
            # --------------------------------
            (
                f'site:{official_domain} '
                f'九州 福岡 佐賀 長崎 '
                f'熊本 大分 宮崎 鹿児島 '
                f'支店 支社 営業所 事業所'
            ),

            # --------------------------------
            # 営業・法人系
            # --------------------------------
            (
                f'site:{official_domain} '
                f'福岡 佐賀 長崎 熊本 '
                f'大分 宮崎 鹿児島 '
                f'法人営業 法人事業 '
                f'営業部 事業部 営業拠点'
            ),

            # --------------------------------
            # 会社情報系
            # --------------------------------
            (
                f'site:{official_domain} '
                f'会社情報 拠点 所在地 住所'
            ),

            # --------------------------------
            # 九州支店などの直接検索
            # --------------------------------
            (
                f'site:{official_domain} '
                f'福岡支店 九州支店 '
                f'九州営業所 九州事業所 '
                f'九州営業部'
            ),

            # --------------------------------
            # ニトリ等の部門拠点
            # --------------------------------
            (
                f'site:{official_domain} '
                f'福岡事業部 福岡営業部 '
                f'法人事業部 福岡 '
                f'法人営業部 福岡 '
                f'リフォーム事業部 福岡'
            )
        ]

        seen_q2_urls = set()

        for q2_query in q2_queries:

            current_results = (
                fetch_tavily_results(
                    q2_query,
                    api_key,
                    include_domains=[
                        official_domain
                    ]
                )
            )

            for result in current_results:

                url = result.get(
                    "url",
                    ""
                )

                if url:

                    if url in seen_q2_urls:
                        continue

                    seen_q2_urls.add(
                        url
                    )

                q2_results.append(
                    result
                )

    return {
        "official_candidates":
            official_candidates,

        "official_domains":
            official_domains,

        "q1_results":
            q1_results,

        "q2_results":
            q2_results
    }


# ==========================================
# JSONパース
# ==========================================
def safe_parse_json(text):

    try:
        return json.loads(text)

    except json.JSONDecodeError:

        text = re.sub(
            r"```json|```",
            "",
            text
        ).strip()

        match = re.search(
            r"\[.*\]|\{.*\}",
            text,
            re.DOTALL
        )

        if match:

            return json.loads(
                match.group(0)
            )

        raise


# ==========================================
# Gemini分析
# ==========================================
def analyze_companies_batch(
    batch_data,
    gemini_key
):

    client = genai.Client(
        api_key=gemini_key
    )

    prompt_targets = ""

    for i, item in enumerate(
        batch_data
    ):

        # --------------------------------------
        # Q1
        # 元検索＋公式ドメイン内検索を
        # 両方Geminiに渡す
        # --------------------------------------
        q1_text = "\n".join(
            [
                (
                    f"- タイトル: "
                    f"{r.get('title', '')}\n"
                    f"  内容: "
                    f"{r.get('snippet', '')}\n"
                    f"  URL: "
                    f"{r.get('url', '')}"
                )

                for r in item.get(
                    "q1_results",
                    []
                )[:20]
            ]
        )

        # --------------------------------------
        # Q2
        # --------------------------------------
        q2_text = "\n".join(
            [
                (
                    f"- タイトル: "
                    f"{r.get('title', '')}\n"
                    f"  内容: "
                    f"{r.get('snippet', '')}\n"
                    f"  URL: "
                    f"{r.get('url', '')}"
                )

                for r in item.get(
                    "q2_results",
                    []
                )[:20]
            ]
        )

        official_domain = ""

        if item.get(
            "official_domains"
        ):

            official_domain = (
                item[
                    "official_domains"
                ][0]
            )

        prompt_targets += (

            f"\n=== 対象企業 "
            f"{i + 1} ===\n"

            f"【入力された会社名】\n"
            f"{item['company']}\n"

            f"【前株・後株】\n"
            f"{parse_company_name(item['company'])['position']}\n"

            f"【公式ドメイン候補】\n"
            f"{official_domain or 'なし'}\n"

            f"【Q1：会社概要・公式サイト検索】\n"
            f"{q1_text if q1_text else 'なし'}\n"

            f"【Q2：公式サイト内の九州拠点検索】\n"
            f"{q2_text if q2_text else 'なし'}\n"
        )

    template = """
あなたは企業情報・拠点調査の専門家です。
ハルシネーションを厳禁とします。

以下の企業について、
提供された検索結果だけを根拠として厳密に判断してください。


【最重要：前株・後株】

入力された会社名の「株式会社」の位置を厳密に維持してください。

「株式会社ニデック」
と
「ニデック株式会社」
は別法人です。

入力が「株式会社ニデック」の場合、
「ニデック株式会社」の公式サイトを採用してはいけません。

入力が「ニデック株式会社」の場合、
「株式会社ニデック」の公式サイトを採用してはいけません。

検索結果に似た法人があっても、
入力会社名と前株・後株の構造が一致しない場合は除外してください。


【公式URL】

Q1の検索結果から、
対象企業自身の公式サイトを選んでください。

重要：

- Q1の元検索結果と公式ドメイン内検索結果の両方を確認する
- 会社概要
- 会社情報
- 企業情報
- 企業概要
- コーポレートサイト

など、対象企業自身を確認できる公式ページを優先する。

会社名がタイトルに完全一致しない場合でも、
公式ドメイン・ページ内容・会社名表記から
対象企業自身の公式サイトであることが確認できれば採用する。

ただし、前株・後株が逆の法人は絶対に採用しない。

URLを推測して新しく作ってはいけない。

Q1に対象企業自身の公式URLがある場合、
profile_urlを空欄やnullにせず、
検索結果から最も適切なものを1つ選択する。


【九州拠点】

Q2を中心に判断する。

対象企業自身の現在の九州内拠点のみ対象。

対象：

- 支店
- 支社
- 営業所
- 事業所
- 営業部
- 法人営業部
- 法人事業部
- 法人＆リフォーム事業部
- リフォーム事業部
- 営業拠点
- Hub
- 店舗
- 恒常的な事業活動拠点

除外：

- 子会社
- 関連会社
- グループ会社
- 別法人
- 代理店
- 販売店
- パートナー
- 協力会社
- 顧客
- 施工現場
- 納入先
- プロジェクト現場

例えば、

対象：
ニデック株式会社

検索結果：
ニデックテクノモータ株式会社 九州事業所

これは別法人なので除外する。


【details】

具体的な拠点名と住所が確認できる場合のみ記載。

住所は検索結果からそのまま使用し、
推測・補完しない。

曖昧な、

「九州エリア」
「九州各県」
「九州店舗」
「九州拠点」
「福岡エリア」

などはdetailsに入れない。


【判定】

以下の3つのいずれか：

"⭕️九州拠点あり"
"❌九州拠点なし"
"❓判定不明"

公式サイト内に現在の具体的な九州拠点が確認できれば⭕️。

公式サイトを十分確認しても九州拠点の有無を確定できない場合は❓。

九州拠点がないことが公式情報から明確に確認できる場合は❌。

「見つからない」というだけで❌にしない。


【reason】

判定理由を1～2個、簡潔に。

【sales_keywords】

DX営業代行で使える企業固有のフックキーワードを10個。

【notes】

2023年8月14日以降の重要事項のみ。


{prompt_targets}


必ず以下のJSON配列だけを返してください。

[
    {
        "company": "会社名",
        "official_url": "https://...",
        "is_found": "⭕️九州拠点あり",
        "reason": [
            "判定理由"
        ],
        "details": [
            {
                "name": "拠点名称",
                "address": "住所",
                "url": "公式URL"
            }
        ],
        "sales_keywords": [
            "キーワード1",
            "キーワード2",
            "キーワード3",
            "キーワード4",
            "キーワード5",
            "キーワード6",
            "キーワード7",
            "キーワード8",
            "キーワード9",
            "キーワード10"
        ],
        "notes": []
    }
]
"""

    prompt = template.replace(
        "{prompt_targets}",
        prompt_targets
    )

    try:

        response = client.models.generate_content(
            model="gemini-3.5-flash-lite",
            contents=prompt,
            config=types.GenerateContentConfig(
                response_mime_type="application/json"
            )
        )

        return safe_parse_json(
            response.text.strip()
        )

    except Exception as e:

        st.error(
            f"AI分析バッチ処理エラー: {str(e)}"
        )

        return []


# ==========================================
# UI
# ==========================================
with st.form(
    key="batch_search_form"
):

    raw_input = st.text_area(
        "会社名リストを入力（スプレッドシートからそのまま貼り付け可能）",
        placeholder=(
            "株式会社ニデック\n"
            "ニデック株式会社\n"
            "アステラス製薬株式会社\n"
            "株式会社ニトリ"
        ),
        height=180
    )

    submit_button = st.form_submit_button(
        "一括検索・分析を実行",
        type="primary"
    )


# ==========================================
# 実行
# ==========================================
if submit_button:

    if not raw_input.strip():

        st.warning(
            "会社名を入力してください。"
        )

    elif (
        not tavily_api_key
        or not gemini_key
    ):

        st.error(
            "Streamlitの Secrets に "
            "TAVILY_API_KEY または "
            "GEMINI_API_KEY が設定されていません。"
        )

    else:

        # キャッシュを使わない
        st.session_state.result_cache = {}

        st.session_state.pop(
            "batch_results",
            None
        )

        lines = raw_input.strip().split(
            "\n"
        )

        company_list = []

        for line in lines:

            parts = line.split(
                "\t"
            )

            comp = parts[0].strip()

            if (
                comp
                and comp not in company_list
            ):

                company_list.append(
                    comp
                )

        progress_bar = st.progress(
            0
        )

        status_text = st.empty()

        # ======================================
        # Tavily
        # ======================================
        status_text.text(
            "検索中..."
        )

        fetched_data = []

        for i, comp in enumerate(
            company_list
        ):

            search_data = (
                search_company_info(
                    comp,
                    tavily_api_key
                )
            )

            fetched_data.append({
                "company":
                    comp,

                "official_candidates":
                    search_data.get(
                        "official_candidates",
                        []
                    ),

                "official_domains":
                    search_data.get(
                        "official_domains",
                        []
                    ),

                "q1_results":
                    search_data.get(
                        "q1_results",
                        []
                    ),

                "q2_results":
                    search_data.get(
                        "q2_results",
                        []
                    )
            })

            progress_bar.progress(
                (
                    (i + 1)
                    / max(
                        len(company_list),
                        1
                    )
                ) * 0.5
            )

        # ======================================
        # Gemini
        # ======================================
        status_text.text(
            "AIによる一括分析を実行中..."
        )

        company_map = {}

        # 429対策として5社ずつ
        chunk_size = 5

        for i in range(
            0,
            len(fetched_data),
            chunk_size
        ):

            chunk = fetched_data[
                i:i + chunk_size
            ]

            gemini_chunk = []

            for item in chunk:

                gemini_chunk.append({
                    "company":
                        item["company"],

                    "official_domains":
                        item["official_domains"],

                    "q1_results":
                        item["q1_results"],

                    "q2_results":
                        item["q2_results"]
                })

            res_list = (
                analyze_companies_batch(
                    gemini_chunk,
                    gemini_key
                )
            )

            if isinstance(
                res_list,
                list
            ):

                for r in res_list:

                    comp_name = r.get(
                        "company"
                    )

                    if not comp_name:
                        continue

                    if r.get(
                        "is_found"
                    ) not in [
                        "⭕️九州拠点あり",
                        "❌九州拠点なし",
                        "❓判定不明"
                    ]:

                        r["is_found"] = (
                            "❓判定不明"
                        )

                    if not isinstance(
                        r.get("details"),
                        list
                    ):

                        r["details"] = []

                    if not isinstance(
                        r.get("sales_keywords"),
                        list
                    ):

                        r["sales_keywords"] = []

                    if not isinstance(
                        r.get("reason"),
                        list
                    ):

                        r["reason"] = []

                    if not isinstance(
                        r.get("notes"),
                        list
                    ):

                        r["notes"] = []

                    company_map[
                        comp_name
                    ] = r

            progress_bar.progress(
                0.5
                + (
                    (
                        i + len(chunk)
                    )
                    / max(
                        len(fetched_data),
                        1
                    )
                ) * 0.5
            )

        # ======================================
        # 最終整形
        # ======================================
        batch_results = []

        for comp in company_list:

            res = company_map.get(
                comp,
                {
                    "official_url": None,
                    "is_found":
                        "❓判定不明",
                    "reason": [],
                    "details": [],
                    "sales_keywords": [],
                    "notes": []
                }
            )

            # ----------------------------------
            # 前株・後株の最終確認
            # ----------------------------------
            correct_name = res.get(
                "company",
                comp
            )

            name_match = (
                company_name_matches(
                    comp,
                    correct_name
                )
            )

            # 最終確認でも逆法人なら
            # 正式名称判定を明確にする
            if name_match is False:
                correct_name = comp

            # ----------------------------------
            # details
            # ----------------------------------
            raw_details = res.get(
                "details",
                []
            )

            if not isinstance(
                raw_details,
                list
            ):
                raw_details = []

            valid_details = []

            for d in raw_details:

                if not isinstance(
                    d,
                    dict
                ):
                    continue

                name = str(
                    d.get(
                        "name",
                        ""
                    )
                ).strip()

                address = str(
                    d.get(
                        "address",
                        ""
                    )
                ).strip()

                url = str(
                    d.get(
                        "url",
                        ""
                    )
                ).strip()

                if not name or not address:
                    continue

                if not any(
                    pref in address
                    for pref in kyushu_prefectures
                ):
                    continue

                vague_names = [
                    "九州エリア",
                    "九州各県",
                    "九州エリア店舗",
                    "九州エリア店舗・事業所",
                    "福岡エリア",
                    "九州の拠点",
                    "九州各地",
                    "九州拠点"
                ]

                if any(
                    vague in name
                    for vague in vague_names
                ):
                    continue

                valid_details.append({
                    "name": name,
                    "address": address,
                    "url": url
                })

            # 重複除去
            unique_details = []

            seen_details = set()

            for d in valid_details:

                key = (
                    d["name"],
                    d["address"],
                    d["url"]
                )

                if key in seen_details:
                    continue

                seen_details.add(
                    key
                )

                unique_details.append(
                    d
                )

            valid_details = unique_details

            # ----------------------------------
            # 判定
            # ----------------------------------
            is_found = res.get(
                "is_found",
                "❓判定不明"
            )

            reason = res.get(
                "reason",
                []
            )

            if not isinstance(
                reason,
                list
            ):
                reason = [
                    str(reason)
                ]

            # ⭕️なのに具体的詳細なし
            if (
                is_found
                == "⭕️九州拠点あり"
                and not valid_details
            ):

                is_found = (
                    "❓判定不明"
                )

                reason = [
                    "九州拠点ありと判定されたが、具体的な九州拠点の名称・住所を確認できない"
                ]

            # ----------------------------------
            # 公式URL
            #
            # ★ Geminiの判定をそのまま使用
            # ----------------------------------
            official_url = res.get(
                "official_url"
            )

            if (
                not official_url
                or official_url in [
                    "null",
                    ""
                ]
            ):

                official_url = None

            # ----------------------------------
            # details表示
            # ----------------------------------
            details_summary = ", ".join(
                (
                    f"{d['name']} "
                    f"({d['address']})"
                )
                for d in valid_details
            )

            # ----------------------------------
            # キーワード
            # ----------------------------------
            keywords = res.get(
                "sales_keywords",
                []
            )

            if not isinstance(
                keywords,
                list
            ):
                keywords = []

            keywords_summary = ", ".join(
                str(x)
                for x in keywords
            )

            # ----------------------------------
            # notes
            # ----------------------------------
            notes = res.get(
                "notes",
                []
            )

            if not isinstance(
                notes,
                list
            ):
                notes = []

            notes_text = ", ".join(
                str(x)
                for x in notes
            )

            # ----------------------------------
            # 保存
            # ----------------------------------
            batch_results.append({

                "会社名":
                    comp,

                "公式サイト":
                    official_url,

                "判定":
                    is_found,

                "九州拠点":
                    (
                        details_summary
                        if details_summary
                        else "なし"
                    ),

                "フックキーワード":
                    keywords_summary,

                "特記事項":
                    notes_text,

                "_raw_details":
                    valid_details,

                "_raw_keywords":
                    keywords,

                "_raw_reason":
                    " / ".join(
                        str(x)
                        for x in reason
                        if str(x).strip()
                    ),

                "_raw_notes":
                    notes_text,

                "_q1_results":
                    fetched_data[
                        company_list.index(comp)
                    ][
                        "q1_results"
                    ],

                "_q2_results":
                    fetched_data[
                        company_list.index(comp)
                    ][
                        "q2_results"
                    ],

                "_official_candidates":
                    fetched_data[
                        company_list.index(comp)
                    ][
                        "official_candidates"
                    ]
            })

        progress_bar.progress(
            1.0
        )

        status_text.text(
            "すべての処理が完了しました。"
        )

        st.session_state[
            "batch_results"
        ] = batch_results


# ==========================================
# 一覧表示
# ==========================================
if (
    "batch_results"
    in st.session_state
    and st.session_state[
        "batch_results"
    ]
):

    results = st.session_state[
        "batch_results"
    ]

    st.divider()

    st.subheader(
        "検索・分析結果一覧"
    )

    df_display = pd.DataFrame(
        results
    )

    expected_columns = [
        "会社名",
        "公式サイト",
        "判定",
        "九州拠点",
        "フックキーワード",
        "特記事項"
    ]

    for col in expected_columns:

        if col not in df_display.columns:
            df_display[col] = ""

    df_display = df_display[
        expected_columns
    ]

    st.dataframe(
        df_display,
        column_config={
            "公式サイト":
                st.column_config.LinkColumn(
                    "公式サイト",
                    help="クリックすると公式HPが開きます"
                )
        },
        use_container_width=True
    )

    # ======================================
    # TSV
    # ======================================
    tsv_text = df_display.to_csv(
        sep="\t",
        index=False
    )

    with st.expander(
        "スプレッドシート用の一括コピー（タブ区切りテキスト）"
    ):

        st.markdown(
            "下の枠内のテキストをコピーして、"
            "スプレッドシートにそのまま貼り付けることができます。"
        )

        st.code(
            tsv_text,
            language="text"
        )

    # ======================================
    # CSV
    # ======================================
    csv_data = df_display.to_csv(
        index=False
    ).encode(
        "utf-8-sig"
    )

    st.download_button(
        label="結果をCSVでダウンロード",
        data=csv_data,
        file_name="kyushu_corporate_search_results.csv",
        mime="text/csv",
        type="primary"
    )

    # ======================================
    # カード
    # ======================================
    st.divider()

    st.subheader(
        "各社詳細・カード表示"
    )

    for r in results:

        with st.expander(
            f"{r['会社名']} ── 【 {r['判定']} 】"
        ):

            # ----------------------------------
            # 公式サイト
            # ----------------------------------
            if r.get(
                "公式サイト"
            ):

                st.markdown(
                    f"**公式サイト:** "
                    f"[{r['公式サイト']}]"
                    f"({r['公式サイト']})"
                )

            # ----------------------------------
            # 判定根拠
            # ----------------------------------
            if r.get(
                "_raw_reason"
            ):

                st.info(
                    f"**判定根拠:** "
                    f"{r['_raw_reason']}"
                )

            # ----------------------------------
            # 特記事項
            # ----------------------------------
            if r.get(
                "_raw_notes"
            ):

                st.info(
                    f"**特記事項:** "
                    f"{r['_raw_notes']}"
                )

            # ----------------------------------
            # キーワード
            # ----------------------------------
            if r.get(
                "_raw_keywords"
            ):

                st.markdown(
                    "**フックキーワード:**"
                )

                st.markdown(
                    " ".join(
                        f"`{kw}`"
                        for kw in r[
                            "_raw_keywords"
                        ]
                    )
                )

            # ----------------------------------
            # 九州拠点
            # ----------------------------------
            if r.get(
                "_raw_details"
            ):

                st.markdown(
                    "**拠点詳細:**"
                )

                for d in r[
                    "_raw_details"
                ]:

                    with st.container(
                        border=True
                    ):

                        st.markdown(
                            f"**{d.get('name')}**"
                        )

                        st.write(
                            f"住所: "
                            f"{d.get('address')}"
                        )

                        if (
                            d.get("url")
                            and d.get("url")
                            != "null"
                        ):

                            st.markdown(
                                f"[詳細リンク]"
                                f"({d.get('url')})"
                            )

            # ----------------------------------
            # Q1確認
            # ----------------------------------
            if r.get(
                "_q1_results"
            ):

                with st.expander(
                    "公式サイト候補を確認"
                ):

                    for result in r[
                        "_q1_results"
                    ]:

                        st.markdown(
                            f"**{result.get('title', '')}**"
                        )

                        if result.get(
                            "snippet"
                        ):

                            st.write(
                                result.get(
                                    "snippet"
                                )
                            )

                        if result.get(
                            "url"
                        ):

                            st.markdown(
                                f"[URL]"
                                f"({result.get('url')})"
                            )

                        st.divider()

            # ----------------------------------
            # Q2確認
            # ----------------------------------
            if r.get(
                "_q2_results"
            ):

                with st.expander(
                    "公式サイト内の拠点検索結果を確認"
                ):

                    for result in r[
                        "_q2_results"
                    ]:

                        st.markdown(
                            f"**{result.get('title', '')}**"
                        )

                        if result.get(
                            "snippet"
                        ):

                            st.write(
                                result.get(
                                    "snippet"
                                )
                            )

                        if result.get(
                            "url"
                        ):

                            st.markdown(
                                f"[URL]"
                                f"({result.get('url')})"
                            )

                        st.divider()
