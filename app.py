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
            search_kwargs["include_domains"] = include_domains

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

except Exception as e:

    st.error(
        "Gemini APIエラーが発生しました"
    )

    st.exception(e)

    return []


# ==========================================
# 会社名の前株・後株情報
# ※検索候補を落とす用途には使わない
# ==========================================
def parse_company_position(
    company_name: str
):

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
# 公式サイト候補スコア
# ※候補を落としすぎない
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
    # 会社名一致
    # --------------------------------------
    if company.lower() in title_lower:
        score += 10

    if company.lower() in snippet_lower:
        score += 5

    # --------------------------------------
    # 公式らしいタイトル
    # --------------------------------------
    official_words = [
        "公式",
        "会社概要",
        "会社情報",
        "企業情報",
        "企業概要",
        "コーポレート",
        "corporate",
        "company",
        "about",
        "profile",
        "outline"
    ]

    for word in official_words:

        if word.lower() in title_lower:
            score += 5

    # --------------------------------------
    # 公式らしいURL
    # --------------------------------------
    official_paths = [
        "/company",
        "/corporate",
        "/about",
        "/about_us",
        "/about-us",
        "/profile",
        "/outline"
    ]

    for word in official_paths:

        if word in url_lower:
            score += 2

    # --------------------------------------
    # 明らかな第三者サイト
    # --------------------------------------
    domain = extract_domain(url)

    if is_excluded_domain(domain):
        score -= 50

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

        if is_excluded_domain(domain):
            continue

        score = score_official_candidate(
            company,
            result
        )

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

        domain = candidate["domain"]

        if domain in seen_domains:
            continue

        seen_domains.add(domain)

        unique_candidates.append(
            candidate
        )

    return unique_candidates[:3]


# ==========================================
# 会社検索
# ==========================================
def search_multi_queries(
    company: str,
    api_key: str
):

    # ======================================
    # Q1：会社概要・公式サイト
    # ※「結構いい感じ」だった構成を維持
    # ======================================
    q1 = (
        f'"{company}" 会社概要 公式サイト'
    )

    res1 = fetch_tavily_results(
        q1,
        api_key
    )

    # ======================================
    # 公式ドメイン候補
    # ======================================
    official_candidates = (
        find_official_candidates(
            company,
            res1
        )
    )

    official_domains = []

    for candidate in official_candidates:

        if candidate["score"] >= 10:

            official_domains.append(
                candidate["domain"]
            )

    # ======================================
    # Q2：公式ドメイン内の拠点検索
    #
    # 福岡・大野城市などは優遇しない
    # ======================================
    res2 = []

    if official_domains:

        domain = official_domains[0]

        q2_queries = [

            # ① 拠点一覧
            (
                f'site:{domain} '
                f'拠点一覧 事業所一覧 営業所一覧 '
                f'支店一覧 営業拠点 国内拠点'
            ),

            # ② 会社情報・所在地
            (
                f'site:{domain} '
                f'会社情報 拠点 事業所 支店 支社 営業所 '
                f'所在地 住所'
            ),

            # ③ 法人営業・事業部
            (
                f'site:{domain} '
                f'営業部 営業所 営業拠点 '
                f'法人営業 法人事業 法人事業部 '
                f'法人＆リフォーム リフォーム事業部 '
                f'事業部'
            ),

            # ④ 九州全体
            (
                f'site:{domain} '
                f'九州 福岡 佐賀 長崎 熊本 '
                f'大分 宮崎 鹿児島'
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

                if url and url in seen_urls:
                    continue

                if url:
                    seen_urls.add(url)

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

        candidates_text = json.dumps(
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
            f"{candidates_text}\n"

            f"【Q1：会社概要・公式サイト検索】\n"
            f"{q1_text if q1_text else 'なし'}\n"

            f"【Q2：公式サイト内の拠点検索】\n"
            f"{q2_text if q2_text else 'なし'}\n"
        )

    prompt = f"""
あなたは企業情報調査とDX営業提案の専門家です。
提供された検索結果を厳密に確認してください。
確認できない情報を推測・補完してはいけません。


【1. official_url】

入力会社名に該当する対象企業自身の公式サイトを選択してください。

最優先するのは、

- 会社概要
- 会社情報
- 企業情報
- 企業概要
- Corporate Profile
- About Us
- Company

などの会社概要ページです。

単なるトップページしか確認できない場合のみ、
公式トップページを使用してください。

Wikipedia、求人サイト、ニュースサイト、
企業情報まとめサイト等は公式サイトとして使用しないでください。

検索結果に存在するURLだけを使用してください。
URLを推測してはいけません。


【2. company_match】

入力された会社名と、公式会社概要等から確認できる法人名を
照合してください。

以下の3つだけを使用してください。

"〇 一致"
"✕ 不一致"
"⚠️確認できず"

特に、

「株式会社ニデック」
と
「ニデック株式会社」

は別法人です。

前株・後株が異なる場合は「✕ 不一致」。

ただし、日本語の正式法人名を検索結果から確認できない場合は、
無理に不一致にせず「⚠️確認できず」。


【3. details】

Q2の検索結果から、対象企業自身が現在運営している
九州内の具体的な営業・事業拠点を抽出してください。

対象：

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
- その他の恒常的営業・事業拠点

「支店」「営業所」という名称がなくても、
対象企業自身の恒常的な営業・事業活動拠点であり、
九州内の具体的な住所が確認できれば対象としてください。

特に「法人事業部」「法人営業部」「リフォーム事業部」
などを見落とさないでください。

以下は除外：

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

「九州エリア」「九州各県」「福岡エリア」
など具体的住所のないものは除外。


【4. department_keywords】

会社の部署ごとに、IT営業で使える提案キーワードを返してください。

最大4部署。

1部署につき3～4個。

例えば、

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

単なる事業内容ではなく、
「どの部署に何を提案するか」にしてください。


【5. notes】

2023年8月14日以降の重要事項のみ。

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
        "url": "URL"
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

            fetched_item = next(
                (
                    item
                    for item in fetched_data
                    if item["company"] == company
                ),
                None
            )

            if fetched_item is None:

                fetched_item = {
                    "q1_results": [],
                    "q2_results": [],
                    "official_candidates": []
                }

            result = company_map.get(
                company,
                {}
            )

            # --------------------------------
            # 公式URL
            # Geminiが決定
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

            # 重複除去
            unique_details = []

            seen_details = set()

            for detail in valid_details:

                key = (
                    detail["name"],
                    detail["address"],
                    detail["url"]
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
            department_keywords = result.get(
                "department_keywords",
                []
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
            # 表示用九州拠点
            # --------------------------------
            details_summary = ", ".join(
                (
                    f"{detail['name']} "
                    f"({detail['address']})"
                )
                for detail in valid_details
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
                        details_summary
                        if details_summary
                        else "なし"
                    ),

                "部署別IT提案":
                    department_text,

                "特記事項":
                    notes_text,

                # 内部データ
                "_raw_details":
                    valid_details,

                "_raw_keywords":
                    department_keywords,

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
    # カード
    # ======================================
    st.divider()

    st.subheader(
        "各社詳細・カード表示"
    )

    for row in results:

        with st.expander(
            f"{row['会社名']} ── 【{row['社名判定']}】"
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
            # 公式URL
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

            # ==================================
            # デバッグ：Q1検索結果
            # ==================================
            with st.expander(
                "🔎 デバッグ：会社概要・公式サイト検索結果"
            ):

                q1_results = row.get(
                    "_q1_results",
                    []
                )

                if not q1_results:

                    st.write(
                        "検索結果なし"
                    )

                else:

                    for idx, result in enumerate(
                        q1_results,
                        start=1
                    ):

                        st.markdown(
                            f"### Q1-{idx}"
                        )

                        st.write(
                            f"**タイトル:** "
                            f"{result.get('title', '')}"
                        )

                        st.write(
                            f"**URL:** "
                            f"{result.get('url', '')}"
                        )

                        st.write(
                            f"**内容:** "
                            f"{result.get('snippet', '')}"
                        )

                        st.divider()

            # ==================================
            # デバッグ：Q2検索結果
            # ==================================
            with st.expander(
                "🔎 デバッグ：公式サイト内の拠点検索結果"
            ):

                q2_results = row.get(
                    "_q2_results",
                    []
                )

                if not q2_results:

                    st.write(
                        "検索結果なし"
                    )

                else:

                    for idx, result in enumerate(
                        q2_results,
                        start=1
                    ):

                        st.markdown(
                            f"### Q2-{idx}"
                        )

                        st.write(
                            f"**タイトル:** "
                            f"{result.get('title', '')}"
                        )

                        st.write(
                            f"**URL:** "
                            f"{result.get('url', '')}"
                        )

                        st.write(
                            f"**内容:** "
                            f"{result.get('snippet', '')}"
                        )

                        st.divider()

            # ==================================
            # デバッグ：公式サイト候補
            # ==================================
            with st.expander(
                "🔎 デバッグ：公式サイト候補"
            ):

                candidates = row.get(
                    "_official_candidates",
                    []
                )

                if not candidates:

                    st.write(
                        "公式サイト候補なし"
                    )

                else:

                    for candidate in candidates:

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

                        st.write(
                            f"URL: "
                            f"{candidate.get('url')}"
                        )

                        st.divider()
