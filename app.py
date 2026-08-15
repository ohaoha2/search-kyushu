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
# 会社名の正規化
#
# 前株・後株の位置は絶対に保持する
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
# 前株・後株を含めた厳密な会社名比較
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

    # 明確な前株・後株違い
    input_front = input_name.startswith(
        "株式会社"
    )

    input_back = input_name.endswith(
        "株式会社"
    )

    found_front = found_name.startswith(
        "株式会社"
    )

    found_back = found_name.endswith(
        "株式会社"
    )

    if (
        input_front
        and found_back
        and input_name != found_name
    ):
        return False

    if (
        input_back
        and found_front
        and input_name != found_name
    ):
        return False

    return None


# ==========================================
# 検索結果内に入力会社名があるか
# ==========================================
def company_name_in_result(
    company_name: str,
    result: dict
):

    target = normalize_company_name(
        company_name
    )

    title = normalize_company_name(
        result.get("title", "")
    )

    snippet = normalize_company_name(
        result.get("snippet", "")
    )

    # 完全一致
    if target in title:
        return True

    if target in snippet:
        return True

    # 前株・後株が明確に逆ならFalse
    info = parse_company_position(
        company_name
    )

    core = info["core"]

    wrong_name = ""

    if info["position"] == "front":
        wrong_name = core + "株式会社"

    elif info["position"] == "back":
        wrong_name = "株式会社" + core

    if wrong_name:

        wrong_name = normalize_company_name(
            wrong_name
        )

        if (
            wrong_name in title
            or wrong_name in snippet
        ):

            return False

    return None


# ==========================================
# 前株・後株位置取得
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
# Tavily
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

        kwargs = {
            "query": query.strip().replace("`", ""),
            "search_depth": "basic",
            "max_results": 20
        }

        if include_domains:
            kwargs[
                "include_domains"
            ] = include_domains

        response = client.search(
            **kwargs
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
# 公式サイト候補スコア
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
    # 会社概要系タイトル
    # --------------------------------------
    title_lower = title.lower()
    url_lower = url.lower()

    official_words = [
        "会社概要",
        "会社情報",
        "企業情報",
        "企業概要",
        "corporate",
        "company",
        "about",
        "profile"
    ]

    for word in official_words:

        if word.lower() in title_lower:
            score += 10

    # --------------------------------------
    # 会社概要系URL
    # --------------------------------------
    path_words = [
        "/company",
        "/corporate",
        "/about",
        "/about_us",
        "/profile"
    ]

    for word in path_words:

        if word in url_lower:
            score += 3

    # --------------------------------------
    # 第三者サイト
    # --------------------------------------
    domain = extract_domain(
        url
    )

    if is_excluded_domain(
        domain
    ):
        return -1000

    return score


# ==========================================
# 公式サイト候補
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
            "title":
                result.get(
                    "title",
                    ""
                ),
            "url":
                result.get(
                    "url",
                    ""
                ),
            "snippet":
                result.get(
                    "snippet",
                    ""
                )
        })

    candidates.sort(
        key=lambda x: x["score"],
        reverse=True
    )

    # ドメイン重複除去
    unique = []

    seen = set()

    for candidate in candidates:

        domain = candidate["domain"]

        if domain in seen:
            continue

        seen.add(domain)

        unique.append(
            candidate
        )

    return unique[:3]


# ==========================================
# 会社検索
# ==========================================
def search_multi_queries(
    company: str,
    api_key: str
):

    # ======================================
    # Q1
    # ======================================
    q1 = (
        f'"{company}" '
        f'会社概要 会社情報 企業情報 公式サイト'
    )

    res1 = fetch_tavily_results(
        q1,
        api_key
    )

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
    # Q2
    # ======================================
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
                f'九州 福岡 '
                f'法人営業 法人事業 '
                f'法人＆リフォーム '
                f'リフォーム事業部 '
                f'営業部 事業部 '
                f'営業拠点 拠点一覧 事業所一覧'
            ),

            (
                f'site:{domain} '
                f'福岡 支店 営業所 '
                f'所在地 住所 拠点'
            )
        ]

        seen = set()

        for query in q2_queries:

            current = fetch_tavily_results(
                query,
                api_key,
                include_domains=[
                    domain
                ]
            )

            for result in current:

                url = result.get(
                    "url",
                    ""
                )

                if (
                    url
                    and url in seen
                ):
                    continue

                if url:
                    seen.add(url)

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
# Gemini
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
                for r in item[
                    "q1_results"
                ][:10]
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
                for r in item[
                    "q2_results"
                ][:20]
            ]
        )

        prompt_targets += (

            f"\n=== 対象企業 "
            f"{i + 1} ===\n"

            f"【入力会社名】\n"
            f"{item['company']}\n"

            f"【公式サイト候補】\n"
            f"{json.dumps(item['official_candidates'], ensure_ascii=False)}\n"

            f"【Q1：会社概要検索】\n"
            f"{q1_text if q1_text else 'なし'}\n"

            f"【Q2：公式サイト内拠点検索】\n"
            f"{q2_text if q2_text else 'なし'}\n"
        )

    prompt = f"""
あなたは企業情報調査とDX営業提案の専門家です。

検索結果に存在しない情報を推測してはいけません。


【最重要：会社名】

入力された会社名を対象企業としてください。

特に前株・後株を厳密に区別してください。

「株式会社ニデック」
「ニデック株式会社」

は別法人です。


【会社概要URL】

会社概要・会社情報・企業情報・企業概要など、
対象企業自身を確認できるページを最優先してください。

単なるトップページしか確認できない場合は
公式トップページを使用しても構いません。

求人サイト、Wikipedia、ニュースサイト、
企業情報まとめサイトは不可。

検索結果に存在するURLだけを使用してください。


【社名判定】

入力会社名と、会社概要等から確認できる法人名を比較してください。

以下の3つだけです。

「〇 一致」
「✕ 不一致」
「⚠️確認できず」

前株・後株が違えば不一致です。

ただし、会社概要に英語社名しかなく、
日本語法人名そのものを確認できない場合は、
無理に不一致にせず「⚠️確認できず」としてください。


【九州拠点】

九州内の対象企業自身の具体的な拠点だけを抽出してください。

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
- 恒常的な事業拠点

別法人は除外してください。

子会社、関連会社、グループ会社、代理店、
販売店、パートナー、協力会社、顧客先、
施工現場、納入先は除外。

「九州エリア」「九州各県」など、
具体性のないものも除外。

具体的な拠点名と住所が検索結果から確認できる場合だけ
detailsに入れてください。

九州拠点が見つからない場合でも、
「九州拠点なし」という判定をする必要はありません。
単にdetailsを空にしてください。


【小規模企業・HPなし】

小規模会社や個人事業主など、
公式HP・会社概要が存在しない場合があります。

その場合は無理にURLを作らず、
official_urlをnull、
company_matchを「⚠️確認できず」
としてください。


【部署別IT提案】

会社全体の事業キーワードではなく、
「どの部署に、何のITを提案するか」を返してください。

最大4部署。

1部署につき3つ。

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

検索結果から企業の事業・業務内容を踏まえて、
実際の営業で使えるIT提案にしてください。


【特記事項】

2023年8月14日以降の以下のみ。

- 社名変更
- 拠点新設
- 拠点移転
- M&A
- 組織再編
- 新規事業
- 大規模設備投資

なければ[]。


{prompt_targets}


必ずJSON配列だけを返してください。

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
            "TAVILY_API_KEY または GEMINI_API_KEY が設定されていません。"
        )

    else:

        st.session_state.pop(
            "batch_results",
            None
        )

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

        # 無料枠対策
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

            if not fetched_item:

                fetched_item = {
                    "q1_results": [],
                    "q2_results": [],
                    "official_candidates": []
                }

            # --------------------------------
            # 公式URL
            # --------------------------------
            official_url = result.get(
                "official_url"
            )

            if official_url in [
                "",
                "null"
            ]:
                official_url = None

            # --------------------------------
            # URLの最終確認
            #
            # Geminiが選んだURLがQ1に存在するか
            # --------------------------------
            q1_results = fetched_item[
                "q1_results"
            ]

            if official_url:

                matching_q1 = None

                for q1 in q1_results:

                    if q1.get(
                        "url"
                    ) == official_url:

                        matching_q1 = q1
                        break

                # 検索結果にないURLは使わない
                if matching_q1 is None:

                    official_url = None

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

            # --------------------------------
            # Python側で再チェック
            #
            # 公式URLに対応するQ1結果から
            # 前株・後株の明確な逆を検出
            # --------------------------------
            if official_url:

                selected_result = None

                for q1 in q1_results:

                    if q1.get(
                        "url"
                    ) == official_url:

                        selected_result = q1
                        break

                if selected_result:

                    match = company_name_in_result(
                        company,
                        selected_result
                    )

                    if match is False:

                        company_match = (
                            "✕ 不一致"
                        )

                    elif match is True:

                        # 検索結果でも会社名を
                        # 確認できた場合
                        if company_match == "⚠️確認できず":
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

            # 重複削除
            unique_details = []

            seen = set()

            for detail in valid_details:

                key = (
                    detail["name"],
                    detail["address"],
                    detail["url"]
                )

                if key in seen:
                    continue

                seen.add(
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

            department_text = "\n".join(
                department_summary
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
                            f"{d['name']} "
                            f"({d['address']})"
                            for d in valid_details
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

    # ★「判定」は完全に削除
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
    # 各社カード
    # ======================================
    st.divider()

    st.subheader(
        "各社詳細・カード表示"
    )

    for row in results:

        with st.expander(
            f"{row['会社名']}"
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
            # 会社概要
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
            # 検索結果確認
            # --------------------------------
            if row.get(
                "_q1_results"
            ):

                with st.expander(
                    "会社概要・公式サイト検索結果を確認"
                ):

                    for result in row[
                        "_q1_results"
                    ]:

                        st.markdown(
                            f"**{result.get('title', '')}**"
                        )

                        if result.get(
                            "snippet"
                        ):

                            st.write(
                                result[
                                    "snippet"
                                ]
                            )

                        if result.get(
                            "url"
                        ):

                            st.markdown(
                                f"[URL]"
                                f"({result['url']})"
                            )

                        st.divider()

            if row.get(
                "_q2_results"
            ):

                with st.expander(
                    "公式サイト内の拠点検索結果を確認"
                ):

                    for result in row[
                        "_q2_results"
                    ]:

                        st.markdown(
                            f"**{result.get('title', '')}**"
                        )

                        if result.get(
                            "snippet"
                        ):

                            st.write(
                                result[
                                    "snippet"
                                ]
                            )

                        if result.get(
                            "url"
                        ):

                            st.markdown(
                                f"[URL]"
                                f"({result['url']})"
                            )

                        st.divider()
