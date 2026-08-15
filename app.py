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
# 設定
# ==========================================
tavily_api_key = (
    os.getenv("TAVILY_API_KEY")
    or st.secrets.get("TAVILY_API_KEY", "")
)

gemini_key = (
    os.getenv("GEMINI_API_KEY")
    or st.secrets.get("GEMINI_API_KEY", "")
)

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
# セッションステート
# ==========================================
if "search_history" not in st.session_state:
    st.session_state.search_history = []

if "result_cache" not in st.session_state:
    st.session_state.result_cache = {}


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
# 前株・後株を解析
#
# 株式会社ニデック
#   → front
#
# ニデック株式会社
#   → back
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
# False = 明確に別法人
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

    # ------------------------------------------
    # 入力された正式名称そのもの
    # ------------------------------------------
    if original in text:
        return True

    # ------------------------------------------
    # 前株
    #
    # 入力：
    # 株式会社ニデック
    #
    # 検索結果：
    # ニデック株式会社
    #
    # → 明確に別法人候補なので除外
    # ------------------------------------------
    if position == "front":

        wrong_name = (
            core + "株式会社"
        )

        if wrong_name in text:
            return False

    # ------------------------------------------
    # 後株
    #
    # 入力：
    # ニデック株式会社
    #
    # 検索結果：
    # 株式会社ニデック
    #
    # → 明確に別法人候補なので除外
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
    # 前株・後株チェック
    # ------------------------------------------
    match_title = company_name_matches(
        company,
        title
    )

    match_snippet = company_name_matches(
        company,
        snippet
    )

    if match_title is False:
        return -1000

    if match_snippet is False:
        score -= 100

    if match_title is True:
        score += 20

    if match_snippet is True:
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
    # 公式サイトでよく使うパス
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
# 公式ドメイン候補取得
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
        # 明確に前株・後株が反対なら除外
        # --------------------------------------
        name_match = company_name_matches(
            company,
            combined_text
        )

        if name_match is False:
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

        if score < 0:
            continue

        candidates.append({
            "domain": domain,
            "score": score,
            "title": title,
            "url": result.get(
                "url",
                ""
            )
        })

    # --------------------------------------
    # スコア順
    # --------------------------------------
    candidates.sort(
        key=lambda x: x["score"],
        reverse=True
    )

    # --------------------------------------
    # 重複ドメイン除去
    # --------------------------------------
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
            "query": query,
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

    # ------------------------------------------
    # Q1
    # ------------------------------------------
    q1_query = (
        f'"{clean_name}" '
        f'公式サイト 会社概要 企業情報'
    )

    q1_results = fetch_tavily_results(
        q1_query,
        api_key
    )

    # ------------------------------------------
    # 公式候補
    # ------------------------------------------
    official_candidates = (
        find_official_domains(
            clean_name,
            q1_results
        )
    )

    official_domain = None

    if official_candidates:

        # 最上位候補
        official_domain = (
            official_candidates[0][
                "domain"
            ]
        )

    # ------------------------------------------
    # Q1公式ドメイン内の会社概要検索
    # ------------------------------------------
    q1_filtered = []

    if official_domain:

        target_query = (
            f'site:{official_domain} '
            f'会社概要 OR 企業情報 OR '
            f'about OR outline'
        )

        target_results = fetch_tavily_results(
            target_query,
            api_key,
            include_domains=[
                official_domain
            ]
        )

        q1_filtered = (
            target_results
            if target_results
            else q1_results
        )

    else:

        q1_filtered = q1_results

    # ------------------------------------------
    # Q2 九州拠点検索
    # ------------------------------------------
    q2_results = []

    if official_domain:

        q2_queries = [

            (
                f'site:{official_domain} '
                f'九州 福岡 佐賀 長崎 '
                f'熊本 大分 宮崎 鹿児島 '
                f'支店 支社 営業所 事業所'
            ),

            (
                f'site:{official_domain} '
                f'福岡 佐賀 長崎 熊本 '
                f'大分 宮崎 鹿児島 '
                f'法人営業 法人事業 '
                f'営業部 事業部 営業拠点'
            ),

            (
                f'site:{official_domain} '
                f'会社情報 拠点 所在地 住所'
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

                if (
                    url
                    and url not in seen_q2_urls
                ):

                    seen_q2_urls.add(
                        url
                    )

                    q2_results.append(
                        result
                    )

    return {
        "official_domain":
            official_domain,

        "official_candidates":
            official_candidates,

        "q1_results":
            q1_filtered,

        "q2_results":
            q2_results
    }


# ==========================================
# 2. AI分析
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
                )[:5]
            ]
        )

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
                )[:10]
            ]
        )

        prompt_targets += (

            f"\n=== 対象企業 "
            f"{i + 1} ===\n"

            f"【入力された会社名】\n"
            f"{item['company_name']}\n"

            f"【前株・後株】\n"
            f"{parse_company_name(item['company_name'])['position']}\n"

            f"【抽出された公式ドメイン】\n"
            f"{item.get('official_domain') or '不明'}\n"

            f"【会社概要ページ候補】\n"
            f"{q1_text if q1_text else '情報なし'}\n"

            f"【拠点・店舗ページ候補】\n"
            f"{q2_text if q2_text else '情報なし'}\n"
        )

    template = """
あなたは企業の所在調査とIT提案のプロフェッショナルです。
ハルシネーションを厳禁とします。

以下の複数企業について調査し、
必ずJSON配列で返してください。


【最重要ルール：前株・後株を厳密に区別】

入力された会社名の「株式会社」の位置を絶対に変更してはいけません。

例えば、

「株式会社ニデック」

と

「ニデック株式会社」

は別法人候補です。

入力が「株式会社ニデック」の場合、
「ニデック株式会社」の情報を対象企業として採用してはいけません。

入力が「ニデック株式会社」の場合、
「株式会社ニデック」の情報を対象企業として採用してはいけません。

会社名の知名度や検索結果の件数を理由に、
似た会社名の法人へ置き換えてはいけません。


{prompt_targets}


1. "input_company"

入力された会社名をそのまま格納してください。


2. "correct_company_name"

検索結果から確認できる正式名称。

ただし、入力会社名の前株・後株構造と一致するものだけを採用してください。

前株・後株が異なる場合は別法人として扱ってください。


3. "profile_url"

対象企業自身の公式サイト内の会社概要・企業情報ページURL。

別法人、求人サイト、ニュースサイト、Wikipedia等は使用禁止。


4. "details"

対象企業自身の九州内の拠点。

対象：

- 本社
- 支店
- 支社
- 営業所
- 事業所
- 営業部
- 法人営業部
- 法人事業部
- 営業拠点
- リフォーム事業部
- 恒常的な事業部拠点
- Hub
- 店舗

ただし、対象企業自身が運営することが確認できるものだけ。

以下は除外：

- 子会社
- 関連会社
- グループ会社
- 別法人
- 代理店
- 販売店
- パートナー
- 顧客先
- 施工現場
- 納入先
- プロジェクト現場


5. "reason"

九州拠点に関する判定理由を1～2文。


6. "department_keywords"

対象企業の主要部署を最大4つ。

部署ごとにIT提案用のフックキーワードを3つ。


7. "notes"

2023年8月14日以降の重要事項。

なければ[]。


必ず以下の形式：

[
    {
        "input_company": "入力された会社名",
        "correct_company_name": "正式名称",
        "profile_url": "https://...",
        "reason": "判定理由",
        "details": [
            {
                "name": "拠点名",
                "address": "住所",
                "url": "公式URL"
            }
        ],
        "department_keywords": [
            {
                "department": "営業部",
                "keywords": [
                    "IT提案1",
                    "IT提案2",
                    "IT提案3"
                ]
            }
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

        text = re.sub(
            r"```json|```",
            "",
            response.text
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

        return json.loads(text)

    except Exception as e:

        st.error(
            f"AI分析エラー: {str(e)}"
        )

        return []


# ==========================================
# 3. UI
# ==========================================
with st.form(
    key="batch_search_form"
):

    st.markdown(
        "**会社名リストを入力（1行に1社）**"
    )

    raw_input = st.text_area(
        "",
        placeholder=(
            "株式会社ニデック\n"
            "ニデック株式会社\n"
            "株式会社ニトリ\n"
            "アステラス製薬株式会社"
        ),
        height=180
    )

    submit_button = st.form_submit_button(
        "一括検索・分析を実行",
        type="primary"
    )


# ==========================================
# 4. 実行
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

        st.session_state.result_cache = {}

        lines = raw_input.strip().split(
            "\n"
        )

        company_list = []

        for line in lines:

            parts = line.split("\t")

            comp = parts[0].strip()

            if (
                comp
                and comp not in company_list
            ):

                company_list.append(
                    comp
                )

        progress_bar = st.progress(0)
        status_text = st.empty()

        # ======================================
        # Tavily検索
        # ======================================
        status_text.text(
            "検索中..."
        )

        fetched_data = []

        for i, comp_name in enumerate(
            company_list
        ):

            search_data = (
                search_company_info(
                    comp_name,
                    tavily_api_key
                )
            )

            fetched_data.append({

                "company_name":
                    comp_name,

                "official_domain":
                    search_data.get(
                        "official_domain"
                    ),

                "official_candidates":
                    search_data.get(
                        "official_candidates",
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
            "AI分析中..."
        )

        company_map = {}

        # 429対策
        chunk_size = 5

        for i in range(
            0,
            len(fetched_data),
            chunk_size
        ):

            chunk = fetched_data[
                i:i + chunk_size
            ]

            res_list = (
                analyze_companies_batch(
                    chunk,
                    gemini_key
                )
            )

            if isinstance(
                res_list,
                list
            ):

                for r in res_list:

                    input_company = (
                        r.get(
                            "input_company"
                        )
                    )

                    if input_company:

                        company_map[
                            input_company
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

        for comp_name in company_list:

            res = company_map.get(
                comp_name,
                {}
            )

            # ----------------------------------
            # 正式名称
            # ----------------------------------
            correct_name = (
                res.get(
                    "correct_company_name",
                    comp_name
                )
            )

            # ----------------------------------
            # 前株・後株の最終チェック
            # ----------------------------------
            name_match = (
                company_name_matches(
                    comp_name,
                    correct_name
                )
            )

            if name_match is False:

                name_judgement = (
                    f"✕ ({correct_name})"
                )

            else:

                name_judgement = "〇"

            # ----------------------------------
            # URL
            # ----------------------------------
            profile_url = (
                res.get(
                    "profile_url",
                    ""
                )
            )

            if (
                not profile_url
                or profile_url == "null"
            ):

                profile_url = ""

            # ----------------------------------
            # 拠点
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

                valid_details.append({
                    "name": name,
                    "address": address,
                    "url": url
                })

            # ----------------------------------
            # 重複削除
            # ----------------------------------
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

            valid_details = (
                unique_details
            )

            # ----------------------------------
            # 詳細文字列
            # ----------------------------------
            details_summary = ", ".join(
                (
                    f"{d['name']} "
                    f"({d['address']})"
                )
                for d in valid_details
            )

            # ----------------------------------
            # 理由
            # ----------------------------------
            reason = res.get(
                "reason",
                ""
            )

            # ----------------------------------
            # 部署キーワード
            # ----------------------------------
            dept_kws = res.get(
                "department_keywords",
                []
            )

            if not isinstance(
                dept_kws,
                list
            ):

                dept_kws = []

            kw_summary = "\n".join(
                (
                    f"【{dk.get('department', '')}】 "
                    + " / ".join(
                        dk.get(
                            "keywords",
                            []
                        )
                    )
                )

                for dk in dept_kws

                if isinstance(
                    dk,
                    dict
                )
                and dk.get(
                    "department"
                )
                and dk.get(
                    "keywords"
                )
            )

            # ----------------------------------
            # 特記事項
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

                "入力会社名":
                    comp_name,

                "正式名称判定":
                    name_judgement,

                "会社概要URL":
                    profile_url,

                "九州拠点":
                    (
                        details_summary
                        if details_summary
                        else "なし"
                    ),

                "部署別IT提案":
                    kw_summary,

                "特記事項":
                    notes_text,

                "_reason":
                    reason,

                "_raw_details":
                    valid_details,

                "_raw_keywords":
                    dept_kws,

                "_correct_name":
                    correct_name
            })

        progress_bar.progress(1.0)

        status_text.text(
            "完了しました。"
        )

        st.session_state[
            "batch_results"
        ] = batch_results


# ==========================================
# 5. 表示
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

    df_table = pd.DataFrame(
        results
    )

    df_table = df_table[
        [
            "入力会社名",
            "正式名称判定",
            "会社概要URL",
            "九州拠点",
            "部署別IT提案",
            "特記事項"
        ]
    ]

    st.dataframe(
        df_table,
        column_config={
            "会社概要URL":
                st.column_config.LinkColumn(
                    "会社概要URL"
                )
        },
        use_container_width=True
    )

    # ======================================
    # TSV
    # ======================================
    with st.expander(
        "スプレッドシート用コピー"
    ):

        st.text_area(
            "テキストをコピー",
            df_table.to_csv(
                sep="\t",
                index=False
            ),
            height=250
        )

    # ======================================
    # 詳細カード
    # ======================================
    st.divider()

    st.subheader(
        "各社詳細・IT提案カンペ"
    )

    for row in results:

        with st.container():

            st.markdown(
                f"### "
                f"{row['_correct_name']} "
                f"（入力: "
                f"{row['入力会社名']}）"
            )

            st.markdown(
                f"**会社概要URL:** "
                f"{row['会社概要URL'] or '確認できず'}"
            )

            # ----------------------------------
            # 正式名称判定
            # ----------------------------------
            if (
                row["正式名称判定"]
                == "〇"
            ):

                st.success(
                    "正式名称・前株/後株一致"
                )

            else:

                st.error(
                    f"正式名称不一致: "
                    f"{row['正式名称判定']}"
                )

            # ----------------------------------
            # 九州拠点
            # ----------------------------------
            st.info(
                f"**九州拠点の状況:** "
                f"{row['_reason'] or '判定理由なし'}"
            )

            if row["_raw_details"]:

                st.markdown(
                    "**拠点詳細:**"
                )

                for d in row[
                    "_raw_details"
                ]:

                    st.markdown(
                        f"- **{d['name']}** "
                        f"（{d['address']}）"
                    )

                    if d.get("url"):

                        st.markdown(
                            f"  [詳細リンク]"
                            f"({d['url']})"
                        )

            # ----------------------------------
            # 部署別IT提案
            # ----------------------------------
            st.markdown(
                "**💡 部署別 IT提案キーワード:**"
            )

            if row["_raw_keywords"]:

                for dk in row[
                    "_raw_keywords"
                ]:

                    if not isinstance(
                        dk,
                        dict
                    ):
                        continue

                    department = dk.get(
                        "department",
                        "不明"
                    )

                    keywords = dk.get(
                        "keywords",
                        []
                    )

                    st.markdown(
                        f"**【{department}】**"
                    )

                    for kw in keywords:

                        st.markdown(
                            f"- {kw}"
                        )

            else:

                st.markdown(
                    "- キーワード取得不可"
                )

            # ----------------------------------
            # 特記事項
            # ----------------------------------
            if row[
                "特記事項"
            ]:

                st.markdown(
                    f"**特記事項:** "
                    f"{row['特記事項']}"
                )

            st.markdown("---")
