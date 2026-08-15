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

    return any(
        domain == excluded
        or domain.endswith("." + excluded)
        for excluded in excluded_domains
    )


# ==========================================
# URLからドメイン抽出
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
# 前株・後株の明確な逆判定
#
# True  = 一致
# False = 明確に逆
# None  = 判断できない
# ==========================================
def company_name_matches(
    input_company: str,
    text: str
):

    info = parse_company_name(
        input_company
    )

    core = info["core"]
    position = info["position"]

    text = text or ""

    # 正式名称そのもの
    if input_company in text:
        return True

    if position == "front":

        wrong_name = (
            core + "株式会社"
        )

        if wrong_name in text:
            return False

    elif position == "back":

        wrong_name = (
            "株式会社" + core
        )

        if wrong_name in text:
            return False

    return None


# ==========================================
# 公式サイト候補のスコアリング
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

    # 明確に逆法人なら除外
    if title_match is False:
        return -1000

    if snippet_match is False:
        score -= 100

    if title_match is True:
        score += 20

    if snippet_match is True:
        score += 10

    # ------------------------------------------
    # 会社名がタイトルに含まれる
    # ------------------------------------------
    if company.lower() in title_lower:
        score += 10

    # ------------------------------------------
    # 会社名が本文に含まれる
    # ------------------------------------------
    if company.lower() in snippet_lower:
        score += 5

    # ------------------------------------------
    # 会社概要らしいタイトル
    # ------------------------------------------
    official_title_words = [
        "公式",
        "会社概要",
        "会社情報",
        "企業情報",
        "企業概要",
        "コーポレート",
        "corporate",
        "company",
        "about",
        "profile"
    ]

    for word in official_title_words:

        if word.lower() in title_lower:
            score += 5

    # ------------------------------------------
    # URLに会社名
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
    # 会社概要系URL
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
# 公式ドメイン候補を取得
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

        # 明確な前株・後株逆のみ除外
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
# 会社検索
# ==========================================
def search_multi_queries(
    keyword: str,
    api_key: str
):

    # ==========================================
    # Q1：会社概要・公式サイト
    # ==========================================
    q1 = (
        f'"{keyword}" '
        f'会社概要 会社情報 企業情報 公式サイト'
    )

    res1 = fetch_tavily_results(
        q1,
        api_key
    )

    # ==========================================
    # 公式ドメイン候補
    # ==========================================
    official_candidates = find_official_domains(
        keyword,
        res1
    )

    official_domains = []

    for candidate in official_candidates:

        if candidate["score"] >= 10:

            official_domains.append(
                candidate["domain"]
            )

    # ==========================================
    # 公式ドメイン内検索
    # ==========================================
    res2 = []

    if official_domains:

        domain = official_domains[0]

        q2_queries = [

            (
                f'site:{domain} '
                f'九州 福岡 佐賀 長崎 熊本 '
                f'大分 宮崎 鹿児島 '
                f'支店 支社 営業所 事業所'
            ),

            (
                f'site:{domain} '
                f'福岡 佐賀 長崎 熊本 '
                f'大分 宮崎 鹿児島 '
                f'法人営業 法人事業 '
                f'営業部 事業部 '
                f'営業拠点 拠点一覧 事業所一覧'
            ),

            (
                f'site:{domain} '
                f'会社情報 拠点 所在地 住所'
            )
        ]

        seen_urls = set()

        for q2 in q2_queries:

            current_results = fetch_tavily_results(
                q2,
                api_key,
                include_domains=[
                    domain
                ]
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

    # ==========================================
    # 検索結果
    # ==========================================
    return {
        "q1_results": res1,
        "q2_results": res2,
        "official_candidates": official_candidates,
        "official_domains": official_domains
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

            f"【入力会社名】\n"
            f"{item['company']}\n"

            f"【前株・後株】\n"
            f"{parse_company_name(item['company'])['position']}\n"

            f"【公式ドメイン候補】\n"
            f"{official_domain or 'なし'}\n"

            f"【Q1：会社概要・公式サイト検索】\n"
            f"{q1_text if q1_text else 'なし'}\n"

            f"【Q2：公式ドメイン内の九州拠点検索】\n"
            f"{q2_text if q2_text else 'なし'}\n"
        )

    template = """
あなたは企業情報調査とDX営業提案の専門家です。
ハルシネーションを厳禁とします。

提供された検索結果だけを根拠にしてください。
確認できないことを推測してはいけません。


【最重要：入力会社名】

入力された会社名を最優先してください。

「株式会社ニデック」と
「ニデック株式会社」は別法人です。

前株・後株の位置が異なる法人を対象企業として扱ってはいけません。


【1. 会社概要URL】

"official_url" には、
対象企業自身の「会社概要」「会社情報」「企業情報」
「企業概要」「Corporate Profile」「About Us」
などのページを優先して記載してください。

単なるトップページより会社概要ページを優先してください。

ただし、会社概要ページが検索結果から確認できず、
対象企業自身の公式サイトトップページしか確認できない場合は、
その公式トップページを記載して構いません。

求人サイト、Wikipedia、ニュースサイト、
企業情報まとめサイトなどは使用禁止です。

URLは検索結果に存在するものだけを使用してください。
URLを推測して作成してはいけません。


【2. 会社名照合】

"company_match" を以下のいずれかにしてください。

"〇 一致"
"✕ 不一致"
"⚠️確認できず"

会社概要ページ等に記載された会社名と、
入力された会社名を比較してください。

前株・後株も一致している必要があります。

例：

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

入力：
アステラス製薬株式会社

会社概要：
Astellas Pharma Inc.

のように日本語正式名称が直接確認できない場合でも、
その会社概要ページが入力企業自身の公式ページだと確認でき、
日本法人の正式名称が別途確認できない場合は、
無理に✕にしないで "⚠️確認できず" としてください。


【3. 九州拠点】

以下の3つのいずれか：

"⭕️九州拠点あり"
"❌九州拠点なし"
"❓判定不明"

⭕️：
対象企業自身の現在の九州内拠点が確認できる場合。

❌：
対象企業自身に現在の九州拠点がないことを
公式情報から明確に確認できる場合。

❓：
公式情報を確認しても判断できない場合。

重要：

「拠点が見つからない」
だけを理由に❌としてはいけません。


【4. 拠点details】

以下を対象とします。

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
- 恒常的な事業部拠点

現在の対象企業自身の九州内拠点だけを記載してください。

子会社、関連会社、グループ会社、代理店、
販売店、パートナー、協力会社、顧客先、
施工現場、納入先は除外。

「九州エリア」
「九州各県」
「九州店舗」
など曖昧な表現はdetailsに入れない。

具体的な拠点名・住所が確認できるものだけを記載してください。


【5. 小規模企業・個人事業主】

対象企業が小規模企業、個人事業主、
または公式ホームページを持っていない可能性があります。

その場合、

「公式サイトが見つからない」
＝
「九州拠点なし」

としてはいけません。

公式サイトや会社概要を確認できない場合は、
official_urlをnullまたは空文字、
company_matchを"⚠️確認できず"、
九州判定を"❓判定不明"
としてください。


【6. 判定根拠】

"reason" に1～2個の簡潔な根拠を記載してください。

⭕️：
公式の拠点一覧、事業所一覧等で確認。

❌：
対象企業の公式情報から九州拠点がないことを明確に確認。

❓：
公式情報だけでは現在の九州拠点の有無を確定できない。


【7. 部署別IT提案】

"supply_department_keywords" を作成してください。

これは会社の事業キーワードではありません。

「その会社のどの部署に、どのITを提案すると刺さるか」
を考えてください。

最大4部署。

各部署につき3つの提案キーワード。

例：

[
    {
        "department": "営業部",
        "keywords": [
            "SFA導入",
            "顧客管理DX",
            "商談進捗管理"
        ]
    },
    {
        "department": "管理部",
        "keywords": [
            "電子決裁",
            "勤怠管理",
            "経費精算DX"
        ]
    }
]

企業の検索結果から確認できる事業内容・業務内容を踏まえて、
現実的にIT提案につながるキーワードにしてください。


【8. 特記事項】

2023年8月14日以降の以下のみ。

- 社名変更
- 拠点移転
- 拠点新設
- M&A
- 組織再編
- 新規事業
- 大規模設備投資

なければ[]。


必ず以下のJSON配列だけを返してください。

[
    {
        "company": "入力された会社名",
        "official_url": "https://...",
        "company_match": "〇 一致",
        "is_found": "⭕️九州拠点あり",
        "reason": [
            "判定根拠"
        ],
        "details": [
            {
                "name": "拠点名",
                "address": "住所",
                "url": "公式URL"
            }
        ],
        "supply_department_keywords": [
            {
                "department": "営業部",
                "keywords": [
                    "SFA導入",
                    "顧客管理DX",
                    "商談進捗管理"
                ]
            }
        ],
        "notes": []
    }
]


{prompt_targets}
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
            "TAVILY_API_KEY または GEMINI_API_KEY が設定されていません。"
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

            search_data = search_multi_queries(
                comp,
                tavily_api_key
            )

            fetched_data.append({
                "company":
                    comp,

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
                    ],

                "official_domains":
                    search_data[
                        "official_domains"
                    ]
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

            res_list = analyze_companies_batch(
                chunk,
                gemini_key
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
                        r.get("supply_department_keywords"),
                        list
                    ):

                        r[
                            "supply_department_keywords"
                        ] = []

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

            fetched_item = next(
                (
                    x
                    for x in fetched_data
                    if x[
                        "company"
                    ] == comp
                ),
                None
            )

            if fetched_item is None:

                fetched_item = {
                    "q1_results": [],
                    "q2_results": [],
                    "official_candidates": []
                }

            res = company_map.get(
                comp,
                {
                    "official_url": None,
                    "company_match":
                        "⚠️確認できず",
                    "is_found":
                        "❓判定不明",
                    "reason": [],
                    "details": [],
                    "supply_department_keywords": [],
                    "notes": []
                }
            )

            # ----------------------------------
            # 公式URL
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
            # 会社名照合
            # ----------------------------------
            company_match = res.get(
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

            # ----------------------------------
            # 九州拠点
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

            # 重複削除
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

            # ----------------------------------
            # HP / 会社概要を確認できなかった場合
            # → 無理に拠点なしにしない
            # ----------------------------------
            if (
                not official_url
                or company_match
                == "⚠️確認できず"
            ):

                is_found = (
                    "❓判定不明"
                )

                if not reason:

                    reason = [
                        "対象企業自身の会社概要・公式情報を十分に確認できないため、九州拠点を確定できない"
                    ]

            # ----------------------------------
            # ⭕️なのに拠点詳細なし
            # ----------------------------------
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
            # 拠点
            # ----------------------------------
            details_summary = ", ".join(
                (
                    f"{d['name']} "
                    f"({d['address']})"
                )

                for d in valid_details
            )

            # ----------------------------------
            # 部署別IT提案
            # ----------------------------------
            dept_keywords = res.get(
                "supply_department_keywords",
                []
            )

            if not isinstance(
                dept_keywords,
                list
            ):

                dept_keywords = []

            dept_summary_parts = []

            for dk in dept_keywords:

                if not isinstance(
                    dk,
                    dict
                ):
                    continue

                department = str(
                    dk.get(
                        "department",
                        ""
                    )
                ).strip()

                keywords = dk.get(
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

                dept_summary_parts.append(
                    f"【{department}】 "
                    + " / ".join(
                        keywords
                    )
                )

            dept_summary = "\n".join(
                dept_summary_parts
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
            # 判定根拠
            # ----------------------------------
            reason_text = " / ".join(
                str(x)
                for x in reason
                if str(x).strip()
            )

            # ----------------------------------
            # 保存
            # ----------------------------------
            batch_results.append({

                "会社名":
                    comp,

                "会社名照合":
                    company_match,

                "会社概要URL":
                    official_url,

                "判定":
                    is_found,

                "九州拠点":
                    (
                        details_summary
                        if details_summary
                        else "なし"
                    ),

                "部署別IT提案":
                    dept_summary,

                "特記事項":
                    notes_text,

                "_raw_reason":
                    reason_text,

                "_raw_details":
                    valid_details,

                "_raw_keywords":
                    dept_keywords,

                "_raw_notes":
                    notes_text,

                "_q1_results":
                    fetched_item[
                        "q1_results"
                    ],

                "_q2_results":
                    fetched_item[
                        "q2_results"
                    ],

                "_official_candidates":
                    fetched_item[
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
        "会社名照合",
        "会社概要URL",
        "判定",
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
    # スプレッドシート用コピー
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

    for r in results:

        with st.expander(
            f"{r['会社名']} ── 【 {r['判定']} 】"
        ):

            # ----------------------------------
            # 会社名照合
            # ----------------------------------
            match = r.get(
                "会社名照合",
                "⚠️確認できず"
            )

            if match == "〇 一致":

                st.success(
                    f"**会社名照合:** {match}"
                )

            elif match == "✕ 不一致":

                st.error(
                    f"**会社名照合:** {match}"
                )

            else:

                st.warning(
                    f"**会社名照合:** {match}"
                )

            # ----------------------------------
            # 会社概要URL
            # ----------------------------------
            if r.get(
                "会社概要URL"
            ):

                st.markdown(
                    f"**会社概要URL:** "
                    f"[{r['会社概要URL']}]"
                    f"({r['会社概要URL']})"
                )

            else:

                st.write(
                    "**会社概要URL:** 確認できず"
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
            # 部署別IT提案
            # ----------------------------------
            if r.get(
                "_raw_keywords"
            ):

                st.markdown(
                    "**部署別IT提案:**"
                )

                for dk in r[
                    "_raw_keywords"
                ]:

                    if not isinstance(
                        dk,
                        dict
                    ):
                        continue

                    department = dk.get(
                        "department",
                        ""
                    )

                    keywords = dk.get(
                        "keywords",
                        []
                    )

                    if not department:
                        continue

                    st.markdown(
                        f"**【{department}】**"
                    )

                    for kw in keywords:

                        st.markdown(
                            f"- {kw}"
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
                    "公式サイト・会社概要の検索結果を確認"
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

            # ----------------------------------
            # 公式サイト候補
            # ----------------------------------
            if r.get(
                "_official_candidates"
            ):

                with st.expander(
                    "公式サイト候補を確認"
                ):

                    for candidate in r[
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
