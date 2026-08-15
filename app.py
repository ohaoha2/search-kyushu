import streamlit as st
import json
import os
import re
import pandas as pd
from urllib.parse import urlparse
from tavily import TavilyClient
from google import genai
from google.genai import types


# ==========================================
# Streamlit設定
# ==========================================
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
# 明らかな第三者サイトを除外
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
        x in lower_domain
        for x in [
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
# 会社名正規化
# 前株・後株の位置は維持
# ==========================================
def normalize_company_name(name: str):

    if not name:
        return ""

    return (
        str(name)
        .strip()
        .replace(" ", "")
        .replace("　", "")
        .replace("（株）", "株式会社")
        .replace("(株)", "株式会社")
    )


# ==========================================
# 前株・後株の位置取得
# ==========================================
def parse_company_position(
    company_name: str
):

    name = normalize_company_name(
        company_name
    )

    if name.startswith("株式会社"):

        return {
            "core": name[len("株式会社"):],
            "position": "front"
        }

    if name.endswith("株式会社"):

        return {
            "core": name[:-len("株式会社")],
            "position": "back"
        }

    return {
        "core": name,
        "position": "unknown"
    }


# ==========================================
# 前株・後株の厳密比較
#
# True  = 明確に一致
# False = 明確に不一致
# None  = 判定材料不足
# ==========================================
def exact_company_name_match(
    input_name: str,
    found_name: str
):

    input_name = normalize_company_name(
        input_name
    )

    found_name = normalize_company_name(
        found_name
    )

    if not input_name or not found_name:
        return None

    if input_name == found_name:
        return True

    input_info = parse_company_position(
        input_name
    )

    found_info = parse_company_position(
        found_name
    )

    # 両方とも会社名として解析でき、
    # 株式会社の位置が違えば別法人扱い
    if (
        input_info["position"] in [
            "front",
            "back"
        ]
        and found_info["position"] in [
            "front",
            "back"
        ]
    ):

        if (
            input_info["core"]
            == found_info["core"]
            and
            input_info["position"]
            != found_info["position"]
        ):
            return False

    return None


# ==========================================
# 検索結果から会社名一致を確認
# ==========================================
def company_name_in_result(
    company_name: str,
    result: dict
):

    target = normalize_company_name(
        company_name
    )

    title = normalize_company_name(
        result.get(
            "title",
            ""
        )
    )

    snippet = normalize_company_name(
        result.get(
            "snippet",
            ""
        )
    )

    # 入力会社名そのものがあれば一致
    if target in title:
        return True

    if target in snippet:
        return True

    # 前株・後株の逆表記
    info = parse_company_position(
        company_name
    )

    core = info["core"]

    wrong_name = ""

    if info["position"] == "front":
        wrong_name = (
            core + "株式会社"
        )

    elif info["position"] == "back":
        wrong_name = (
            "株式会社" + core
        )

    wrong_name = normalize_company_name(
        wrong_name
    )

    if wrong_name:

        if (
            wrong_name in title
            or wrong_name in snippet
        ):
            return False

    return None


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

    except Exception:
        return []


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

    score = 0

    # --------------------------------------
    # 会社名
    # --------------------------------------
    name_match = company_name_in_result(
        company,
        result
    )

    if name_match is False:

        return -1000

    if name_match is True:

        score += 30

    # --------------------------------------
    # 公式会社情報らしいタイトル
    # --------------------------------------
    title_lower = title.lower()

    official_title_words = [
        "会社概要",
        "会社情報",
        "企業情報",
        "企業概要",
        "公式",
        "corporate",
        "company",
        "about",
        "profile",
        "outline"
    ]

    for word in official_title_words:

        if word.lower() in title_lower:

            score += 10

    # --------------------------------------
    # URLパス
    # --------------------------------------
    url_lower = url.lower()

    official_path_words = [
        "/company",
        "/company/",
        "/corporate",
        "/corporate/",
        "/about",
        "/about/",
        "/about-us",
        "/about_us",
        "/profile",
        "/outline"
    ]

    for word in official_path_words:

        if word in url_lower:

            score += 3

    # --------------------------------------
    # 明らかな第三者サイト
    # --------------------------------------
    domain = extract_domain(
        url
    )

    if is_excluded_domain(domain):

        return -1000

    return score


# ==========================================
# 公式サイト候補取得
# ==========================================
def find_official_candidates(
    company: str,
    results: list
):

    candidates = []

    for result in results:

        domain = extract_domain(
            result.get(
                "url",
                ""
            )
        )

        if not domain:
            continue

        if is_excluded_domain(
            domain
        ):
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
            "title": result.get(
                "title",
                ""
            ),
            "url": result.get(
                "url",
                ""
            ),
            "snippet": result.get(
                "snippet",
                ""
            )
        })

    candidates.sort(
        key=lambda x: x["score"],
        reverse=True
    )

    # ドメイン重複削除
    unique_candidates = []

    seen_domains = set()

    for candidate in candidates:

        domain = candidate[
            "domain"
        ]

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
# 会社概要URL候補を選ぶ
#
# Geminiのofficial_urlを補助する。
# URLが検索結果に存在する場合のみ採用。
# ==========================================
def select_company_profile_url(
    company: str,
    gemini_url: str,
    q1_results: list
):

    candidates = []

    gemini_domain = extract_domain(
        gemini_url or ""
    )

    for result in q1_results:

        url = result.get(
            "url",
            ""
        )

        if not url:
            continue

        domain = extract_domain(
            url
        )

        if not domain:
            continue

        if is_excluded_domain(
            domain
        ):
            continue

        # Geminiが選んだURLがある場合、
        # 同一ドメインを優先
        same_domain = (
            gemini_domain is not None
            and domain == gemini_domain
        )

        score = 0

        if same_domain:
            score += 20

        title = result.get(
            "title",
            ""
        )

        snippet = result.get(
            "snippet",
            ""
        )

        text = (
            title
            + " "
            + snippet
        ).lower()

        # --------------------------------------
        # 会社概要系ワードを強く優先
        # --------------------------------------
        profile_words = [
            "会社概要",
            "会社情報",
            "企業情報",
            "企業概要",
            "company profile",
            "corporate profile",
            "about us",
            "about-us",
            "corporate information",
            "company information"
        ]

        for word in profile_words:

            if word.lower() in text:

                score += 40

        # --------------------------------------
        # URLパス
        # --------------------------------------
        url_lower = url.lower()

        profile_paths = [
            "/company",
            "/company/",
            "/corporate",
            "/corporate/",
            "/about",
            "/about/",
            "/about-us",
            "/about_us",
            "/profile",
            "/outline"
        ]

        for path in profile_paths:

            if path in url_lower:

                score += 10

        # --------------------------------------
        # 会社名
        # --------------------------------------
        name_match = company_name_in_result(
            company,
            result
        )

        if name_match is True:

            score += 20

        elif name_match is False:

            continue

        candidates.append({
            "score": score,
            "url": url
        })

    if not candidates:

        # Gemini URLが検索結果に存在する場合
        # それをそのまま使用
        if gemini_url:

            for result in q1_results:

                if (
                    result.get("url")
                    == gemini_url
                ):

                    return gemini_url

        return None

    candidates.sort(
        key=lambda x: x["score"],
        reverse=True
    )

    return candidates[0]["url"]


# ==========================================
# 会社検索
# ==========================================
def search_multi_queries(
    company: str,
    api_key: str
):

    # ======================================
    # Q1：公式サイト・会社概要
    # ======================================
    q1 = (
        f'"{company}" '
        f'会社概要 会社情報 企業情報 '
        f'企業概要 公式サイト'
    )

    res1 = fetch_tavily_results(
        q1,
        api_key
    )

    # ======================================
    # 公式候補
    # ======================================
    official_candidates = (
        find_official_candidates(
            company,
            res1
        )
    )

    official_domains = []

    if official_candidates:

        official_domains = [
            official_candidates[0][
                "domain"
            ]
        ]

    # ======================================
    # Q2：
    # 「福岡」「大野城市」などを優遇しない
    #
    # 公式拠点一覧を中心に検索
    # ======================================
    res2 = []

    if official_domains:

        domain = official_domains[0]

        q2_queries = [

            # --------------------------------
            # ① 公式拠点一覧
            # --------------------------------
            (
                f'site:{domain} '
                f'拠点一覧 事業所一覧 営業所一覧 '
                f'支店一覧 営業拠点 国内拠点'
            ),

            # --------------------------------
            # ② 会社情報＋拠点
            # --------------------------------
            (
                f'site:{domain} '
                f'会社情報 拠点 事業所 '
                f'支店 支社 営業所 '
                f'所在地 住所'
            ),

            # --------------------------------
            # ③ 営業・事業部系
            # --------------------------------
            (
                f'site:{domain} '
                f'営業部 営業所 営業拠点 '
                f'法人営業 法人事業 '
                f'法人＆リフォーム '
                f'リフォーム事業部 事業部'
            ),

            # --------------------------------
            # ④ 九州全体
            # --------------------------------
            (
                f'site:{domain} '
                f'九州 福岡 佐賀 長崎 '
                f'熊本 大分 宮崎 鹿児島'
            )
        ]

        seen_urls = set()

        for query in q2_queries:

            current_results = (
                fetch_tavily_results(
                    query,
                    api_key,
                    include_domains=[
                        domain
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
                    and url in seen_urls
                ):
                    continue

                if url:

                    seen_urls.add(
                        url
                    )

                res2.append(
                    result
                )

    return {
        "q1_results": res1,
        "q2_results": res2,
        "official_candidates":
            official_candidates,
        "official_domains":
            official_domains
    }


# ==========================================
# JSON安全パース
# ==========================================
def safe_parse_json(
    text
):

    try:

        return json.loads(
            text
        )

    except json.JSONDecodeError:

        cleaned = re.sub(
            r"```json|```",
            "",
            text
        ).strip()

        match = re.search(
            r"\[.*\]|\{.*\}",
            cleaned,
            re.DOTALL
        )

        if match:

            return json.loads(
                match.group(0)
            )

        raise


# ==========================================
# Gemini一括分析
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
                )[:10]
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
                )[:20]
            ]
        )

        official_candidates_text = json.dumps(
            item.get(
                "official_candidates",
                []
            ),
            ensure_ascii=False
        )

        prompt_targets += (

            f"\n=== 対象企業 "
            f"{i + 1} ===\n"

            f"【入力会社名】\n"
            f"{item['company']}\n"

            f"【公式サイト候補】\n"
            f"{official_candidates_text}\n"

            f"【Q1：会社概要・公式サイト検索】\n"
            f"{q1_text if q1_text else 'なし'}\n"

            f"【Q2：公式サイト内の拠点検索】\n"
            f"{q2_text if q2_text else 'なし'}\n"
        )

    prompt = f"""
あなたは企業情報調査とDX営業提案の専門家です。

検索結果に存在しない情報を推測してはいけません。


【1. 会社概要URL】

入力された会社名の対象企業自身の公式サイトを特定してください。

「会社概要」
「会社情報」
「企業情報」
「企業概要」
「Corporate Profile」
「About Us」
など、会社そのものを確認できるページを最優先します。

単なるトップページしか検索結果に存在しない場合は、
公式トップページを使用して構いません。

求人サイト、Wikipedia、ニュースサイト、
企業情報まとめサイトなどは使用しないでください。

検索結果に存在するURLだけを使用してください。
URLを推測して作成してはいけません。


【2. 社名判定】

"company_match" は以下のいずれかです。

"〇 一致"
"✕ 不一致"
"⚠️確認できず"

入力会社名と会社概要等に記載された法人名を比較してください。

前株・後株を厳密に区別してください。

例えば、

入力：
株式会社ニデック

会社概要：
株式会社ニデック

→ 〇 一致

入力：
株式会社ニデック

会社概要：
ニデック株式会社

→ ✕ 不一致

「株式会社ニデック」と「ニデック株式会社」は
別法人として扱ってください。

ただし、会社概要に日本語正式名称が直接書かれておらず、
英語社名等しか確認できない場合は、
無理に不一致とせず「⚠️確認できず」としてください。


【3. 九州拠点】

対象企業自身の九州内の具体的な拠点を抽出してください。

対象：

- 本社
- 支店
- 支社
- 営業所
- 事業所
- 営業部
- 営業本部
- 法人営業部
- 法人事業部
- 法人＆リフォーム事業部
- リフォーム事業部
- 営業拠点
- Hub
- 恒常的な営業・事業拠点

除外：

- 子会社
- 関連会社
- グループ会社
- 別法人
- 代理店
- 販売店
- パートナー
- 協力会社
- 顧客先
- 施工現場
- 納入先
- プロジェクト現場

「九州エリア」
「九州各県」
「福岡エリア」
など具体的な拠点名・住所がないものは
detailsに入れないでください。

具体的な名称と住所が確認できたものだけ記載してください。

福岡を特別扱いする必要はありません。
九州7県を同じ条件で扱ってください。


【4. 小規模企業・HPなし】

小規模企業や個人事業主など、
公式ホームページが存在しない場合があります。

その場合は、

official_url = null

company_match = "⚠️確認できず"

としてください。

HPがないことを理由に
「九州拠点なし」と推測してはいけません。


【5. 部署別IT提案】

単なる会社の事業キーワードではなく、
部署ごとに「何のITを提案できるか」を返してください。

最大4部署。

各部署につき3つ程度。

例：

[
    {{
        "department": "営業部",
        "keywords": [
            "SFA導入",
            "顧客管理DX",
            "商談進捗管理"
        ]
    }}
]

検索結果から確認できる企業の事業内容・業務内容を踏まえ、
現実的なIT営業提案にしてください。


【6. 特記事項】

2023年8月14日以降に確認できる重要事項のみ。

- 社名変更
- 拠点新設
- 拠点移転
- M&A
- 組織再編
- 新規事業
- 大規模設備投資

なければ[]。


{prompt_targets}


JSON配列だけを返してください。

[
    {{
        "company": "入力会社名",
        "official_url": "https://...",
        "company_match": "〇 一致",
        "details": [
            {{
                "name": "拠点名",
                "address": "住所",
                "url": "公式URL"
            }}
        ],
        "department_keywords": [
            {{
                "department": "営業部",
                "keywords": [
                    "SFA導入",
                    "顧客管理DX",
                    "商談進捗管理"
                ]
            }}
        ],
        "notes": []
    }}
]
"""

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
# 入力フォーム
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
            "TAVILY_API_KEY または GEMINI_API_KEY "
            "が設定されていません。"
        )

    else:

        st.session_state.pop(
            "batch_results",
            None
        )

        # キャッシュは使わない
        st.session_state.result_cache = {}

        lines = raw_input.strip().split(
            "\n"
        )

        company_list = []

        for line in lines:

            parts = line.split(
                "\t"
            )

            company = parts[0].strip()

            if (
                company
                and company not in company_list
            ):

                company_list.append(
                    company
                )

        progress = st.progress(
            0
        )

        status = st.empty()

        # ==================================
        # Tavily
        # ==================================
        status.text(
            "検索中..."
        )

        fetched_data = []

        for i, company in enumerate(
            company_list
        ):

            search_data = search_multi_queries(
                company,
                tavily_api_key
            )

            fetched_data.append({
                "company":
                    company,

                "q1_results":
                    search_data[
                        "q1_results"
                    ],

                "q2_results":
                    search_data[
                        "q2_results"
                    ],

                "official_candidates":
                    search_data[
                        "official_candidates"
                    ]
            })

            progress.progress(
                (
                    (i + 1)
                    / max(
                        len(company_list),
                        1
                    )
                ) * 0.5
            )

        # ==================================
        # Gemini
        # ==================================
        status.text(
            "AIによる分析中..."
        )

        company_map = {}

        # 429対策
        chunk_size = 5

        for start in range(
            0,
            len(fetched_data),
            chunk_size
        ):

            chunk = fetched_data[
                start:start + chunk_size
            ]

            response_list = (
                analyze_companies_batch(
                    chunk,
                    gemini_key
                )
            )

            if isinstance(
                response_list,
                list
            ):

                for result in response_list:

                    company = result.get(
                        "company"
                    )

                    if company:

                        company_map[
                            company
                        ] = result

            progress.progress(
                0.5
                + (
                    (
                        start + len(chunk)
                    )
                    / max(
                        len(fetched_data),
                        1
                    )
                ) * 0.5
            )

        # ==================================
        # 最終整形
        # ==================================
        batch_results = []

        for company in company_list:

            result = company_map.get(
                company,
                {}
            )

            fetched_item = next(
                (
                    x
                    for x in fetched_data
                    if x["company"] == company
                ),
                None
            )

            if fetched_item is None:

                fetched_item = {
                    "q1_results": [],
                    "q2_results": [],
                    "official_candidates": []
                }

            q1_results = fetched_item[
                "q1_results"
            ]

            # --------------------------------
            # 会社概要URL
            # --------------------------------
            gemini_official_url = (
                result.get(
                    "official_url"
                )
            )

            if (
                not gemini_official_url
                or gemini_official_url
                in [
                    "null",
                    ""
                ]
            ):

                gemini_official_url = None

            official_url = (
                select_company_profile_url(
                    company,
                    gemini_official_url,
                    q1_results
                )
            )

            # --------------------------------
            # 社名判定
            # --------------------------------
            company_match = result.get(
                "company_match",
                "⚠️確認できず"
            )

            if company_match not in [
                "〇 一致",
                "✕ 不一致",
                "⚠️確認できず"
            ]:

                company_match = (
                    "⚠️確認できず"
                )

            # Python側でも再確認
            if official_url:

                selected_result = None

                for q1 in q1_results:

                    if (
                        q1.get("url")
                        == official_url
                    ):

                        selected_result = q1
                        break

                if selected_result:

                    python_match = (
                        company_name_in_result(
                            company,
                            selected_result
                        )
                    )

                    if python_match is False:

                        company_match = (
                            "✕ 不一致"
                        )

                    elif python_match is True:

                        # Geminiが確認できずでも
                        # 検索結果から明確なら一致
                        if company_match == (
                            "⚠️確認できず"
                        ):

                            company_match = (
                                "〇 一致"
                            )

            # --------------------------------
            # 九州拠点
            # --------------------------------
            raw_details = result.get(
                "details",
                []
            )

            if not isinstance(
                raw_details,
                list
            ):

                raw_details = []

            valid_details = []

            for detail in raw_details:

                if not isinstance(
                    detail,
                    dict
                ):
                    continue

                name = str(
                    detail.get(
                        "name",
                        ""
                    )
                ).strip()

                address = str(
                    detail.get(
                        "address",
                        ""
                    )
                ).strip()

                url = str(
                    detail.get(
                        "url",
                        ""
                    )
                ).strip()

                if not name or not address:
                    continue

                # 九州住所
                if not any(
                    pref in address
                    for pref in kyushu_prefectures
                ):
                    continue

                # 曖昧名称除外
                vague_names = [
                    "九州エリア",
                    "九州各県",
                    "九州エリア店舗",
                    "九州エリア店舗・事業所",
                    "福岡エリア",
                    "九州拠点",
                    "九州の拠点"
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

            # --------------------------------
            # 拠点重複削除
            # --------------------------------
            unique_details = []

            seen_details = set()

            for detail in valid_details:

                key = (
                    detail[
                        "name"
                    ],
                    detail[
                        "address"
                    ],
                    detail[
                        "url"
                    ]
                )

                if key in seen_details:
                    continue

                seen_details.add(
                    key
                )

                unique_details.append(
                    detail
                )

            valid_details = unique_details

            # --------------------------------
            # 部署別IT提案
            # --------------------------------
            department_keywords = (
                result.get(
                    "department_keywords",
                    []
                )
            )

            if not isinstance(
                department_keywords,
                list
            ):

                department_keywords = []

            department_summary = []

            for item in department_keywords:

                if not isinstance(
                    item,
                    dict
                ):

                    continue

                department = str(
                    item.get(
                        "department",
                        ""
                    )
                ).strip()

                keywords = item.get(
                    "keywords",
                    []
                )

                if not department:
                    continue

                if not isinstance(
                    keywords,
                    list
                ):

                    keywords = []

                keywords = [
                    str(x)
                    for x in keywords
                    if str(x).strip()
                ]

                if not keywords:
                    continue

                department_summary.append(
                    f"【{department}】 "
                    + " / ".join(
                        keywords
                    )
                )

            department_text = (
                "\n".join(
                    department_summary
                )
            )

            # --------------------------------
            # 特記事項
            # --------------------------------
            notes = result.get(
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

            # --------------------------------
            # 保存
            # --------------------------------
            batch_results.append({

                "会社名":
                    company,

                "社名判定":
                    company_match,

                "会社概要URL":
                    official_url,

                "九州拠点":
                    (
                        ", ".join(
                            f"{detail['name']} "
                            f"({detail['address']})"
                            for detail
                            in valid_details
                        )
                        if valid_details
                        else "なし"
                    ),

                "部署別IT提案":
                    department_text,

                "特記事項":
                    notes_text,

                "_raw_details":
                    valid_details,

                "_raw_keywords":
                    department_keywords,

                "_raw_notes":
                    notes_text,

                "_q1_results":
                    q1_results,

                "_q2_results":
                    fetched_item[
                        "q2_results"
                    ],

                "_official_candidates":
                    fetched_item[
                        "official_candidates"
                    ]
            })

        progress.progress(
            1.0
        )

        status.text(
            "すべての処理が完了しました。"
        )

        st.session_state[
            "batch_results"
        ] = batch_results


# ==========================================
# 結果表示
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

    # 「判定」は不要
    expected_columns = [
        "会社名",
        "社名判定",
        "会社概要URL",
        "九州拠点",
        "部署別IT提案",
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
            "会社概要URL":
                st.column_config.LinkColumn(
                    "会社概要URL",
                    help="会社概要ページを開きます"
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
            "下のテキストをコピーして、"
            "スプレッドシートにそのまま貼り付けできます。"
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
    # 各社詳細
    # ======================================
    st.divider()

    st.subheader(
        "各社詳細・カード表示"
    )

    for row in results:

        with st.expander(
            f"{row['会社名']} ── 【 {row['社名判定']} 】"
        ):

            # --------------------------------
            # 社名判定
            # --------------------------------
            if row[
                "社名判定"
            ] == "〇 一致":

                st.success(
                    "社名判定: 〇 一致"
                )

            elif row[
                "社名判定"
            ] == "✕ 不一致":

                st.error(
                    "社名判定: ✕ 不一致"
                )

            else:

                st.warning(
                    "社名判定: ⚠️確認できず"
                )

            # --------------------------------
            # 会社概要URL
            # --------------------------------
            if row.get(
                "会社概要URL"
            ):

                st.markdown(
                    f"**会社概要URL:** "
                    f"[{row['会社概要URL']}]"
                    f"({row['会社概要URL']})"
                )

            else:

                st.write(
                    "**会社概要URL:** 確認できず"
                )

            # --------------------------------
            # 九州拠点
            # --------------------------------
            if row.get(
                "_raw_details"
            ):

                st.markdown(
                    "**拠点詳細:**"
                )

                for detail in row[
                    "_raw_details"
                ]:

                    with st.container(
                        border=True
                    ):

                        st.markdown(
                            f"**{detail['name']}**"
                        )

                        st.write(
                            f"住所: "
                            f"{detail['address']}"
                        )

                        if detail.get(
                            "url"
                        ):

                            st.markdown(
                                f"[詳細リンク]"
                                f"({detail['url']})"
                            )

            # --------------------------------
            # 部署別IT提案
            # --------------------------------
            if row.get(
                "_raw_keywords"
            ):

                st.markdown(
                    "**部署別IT提案:**"
                )

                for item in row[
                    "_raw_keywords"
                ]:

                    if not isinstance(
                        item,
                        dict
                    ):
                        continue

                    department = item.get(
                        "department",
                        ""
                    )

                    keywords = item.get(
                        "keywords",
                        []
                    )

                    if not department:
                        continue

                    st.markdown(
                        f"**【{department}】**"
                    )

                    for keyword in keywords:

                        st.markdown(
                            f"- {keyword}"
                        )

            # --------------------------------
            # 特記事項
            # --------------------------------
            if row.get(
                "_raw_notes"
            ):

                st.info(
                    f"**特記事項:** "
                    f"{row['_raw_notes']}"
                )

            # --------------------------------
            # Q1検索結果
            # --------------------------------
            if row.get(
                "_q1_results"
            ):

                with st.expander(
                    "会社概要・公式サイト検索結果を確認"
                ):

                    for search_result in row[
                        "_q1_results"
                    ]:

                        st.markdown(
                            f"**{search_result.get('title', '')}**"
                        )

                        if search_result.get(
                            "snippet"
                        ):

                            st.write(
                                search_result[
                                    "snippet"
                                ]
                            )

                        if search_result.get(
                            "url"
                        ):

                            st.markdown(
                                f"[URL]"
                                f"({search_result['url']})"
                            )

                        st.divider()

            # --------------------------------
            # Q2検索結果
            # --------------------------------
            if row.get(
                "_q2_results"
            ):

                with st.expander(
                    "公式サイト内の拠点検索結果を確認"
                ):

                    for search_result in row[
                        "_q2_results"
                    ]:

                        st.markdown(
                            f"**{search_result.get('title', '')}**"
                        )

                        if search_result.get(
                            "snippet"
                        ):

                            st.write(
                                search_result[
                                    "snippet"
                                ]
                            )

                        if search_result.get(
                            "url"
                        ):

                            st.markdown(
                                f"[URL]"
                                f"({search_result['url']})"
                            )

                        st.divider()

            # --------------------------------
            # 公式サイト候補
            # --------------------------------
            if row.get(
                "_official_candidates"
            ):

                with st.expander(
                    "公式サイト候補を確認"
                ):

                    for candidate in row[
                        "_official_candidates"
                    ]:

                        st.write(
                            f"スコア: "
                            f"{candidate.get('score')}"
                        )

                        st.write(
                            f"ドメイン: "
                            f"{candidate.get('domain')}"
                        )

                        st.write(
                            f"タイトル: "
                            f"{candidate.get('title')}"
                        )

                        if candidate.get(
                            "url"
                        ):

                            st.markdown(
                                f"[URL]"
                                f"({candidate.get('url')})"
                            )

                        st.divider()
