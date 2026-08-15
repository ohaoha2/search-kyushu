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
    api_key: str
):

    try:

        client = TavilyClient(
            api_key=api_key
        )

        response = client.search(
            query=query.strip().replace("`", ""),
            search_depth="basic",
            max_results=20
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
# 公式サイト候補スコア
#
# ※ここでは候補を完全に決定しない
# Geminiに判断材料を渡すためのもの
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
    company_lower = company.lower()

    score = 0

    # --------------------------------------
    # 入力会社名そのもの
    # --------------------------------------
    if company_lower in title_lower:
        score += 20

    if company_lower in snippet_lower:
        score += 10

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
    # 第三者サイトは強く減点
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

        domain = extract_domain(
            url
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

        candidates.append({
            "score": score,
            "domain": domain,
            "title": result.get(
                "title",
                ""
            ),
            "url": url,
            "snippet": result.get(
                "snippet",
                ""
            )
        })

    candidates.sort(
        key=lambda x: x["score"],
        reverse=True
    )

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
#
# 九州検索は一旦廃止
# ==========================================
def search_company(
    company: str,
    api_key: str
):

    # --------------------------------------
    # Q1：会社概要・公式サイト検索
    # --------------------------------------
    q1 = (
        f'"{company}" '
        f'会社概要 公式サイト'
    )

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
                    f"  URL: "
                    f"{r.get('url', '')}\n"
                    f"  内容: "
                    f"{r.get('snippet', '')}"
                )
                for r in item.get(
                    "q1_results",
                    []
                )[:20]
            ]
        )

        candidates_text = json.dumps(
            item.get(
                "official_candidates",
                []
            ),
            ensure_ascii=False,
            indent=2
        )

        prompt_targets += (

            f"\n=== 対象企業 {i + 1} ===\n"

            f"【入力会社名】\n"
            f"{item['company']}\n\n"

            f"【公式サイト候補】\n"
            f"{candidates_text}\n\n"

            f"【Q1検索結果】\n"
            f"{q1_text if q1_text else 'なし'}\n"
        )

    prompt = f"""
あなたは企業情報調査の専門家です。

提供された検索結果だけを使って判定してください。
情報を推測・補完してはいけません。


==================================================
【最重要：法人名の照合】
==================================================

入力された会社名と、
検索結果から確認できる法人名を厳密に照合してください。

特に株式会社の位置を絶対に間違えないでください。

例：

入力：
株式会社ニデック

法人名：
株式会社ニデック
→ 〇 一致

法人名：
ニデック株式会社
→ ✕ 不一致

この2社を同じ会社として扱ってはいけません。

入力会社名と正式法人名の「株式会社」の位置が異なる場合、
必ず「✕ 不一致」です。


==================================================
【official_url】
==================================================

official_urlには、
「入力された会社名の対象企業自身の公式サイトにある
会社概要ページ」を記載してください。


最優先するページ：

- 会社概要
- 会社情報
- 企業情報
- 企業概要
- Corporate Profile
- Company Profile
- About Us
- About
- Profile
- Outline


重要：

official_urlは、検索結果に実際に存在するURLだけを使用してください。

URLを推測して作ってはいけません。


==================================================
【第三者サイト禁止】
==================================================

以下のサイトは対象企業の公式サイトではありません。

- Wikipedia
- 日本製薬工業協会などの業界団体
- Baseconnect
- Compalyze
- 求人サイト
- 転職サイト
- ニュースサイト
- 企業情報まとめサイト
- その他第三者サイト


例えば、

アステラス製薬株式会社について

https://www.jpma.or.jp/...

が検索結果にあっても、
これはアステラス製薬の公式サイトではありません。

official_urlに使用してはいけません。


==================================================
【会社概要ページの選び方】
==================================================

検索結果に対象企業自身の公式ドメインから

「会社概要」

「会社情報」

「企業情報」

などのページがある場合、
そのページを優先してください。

公式トップページしか確認できない場合のみ、
公式トップページを使用してください。

対象企業自身の公式サイトを確認できない場合は
nullとしてください。


==================================================
【会社概要URLと法人名を同時に確認】
==================================================

公式URLを選ぶ際には、

1. URLが対象企業自身のドメインである
2. そのページが会社概要等のページである
3. ページ上の法人名が入力会社名と一致する

の3点を確認してください。


特に、

株式会社ニデック

と

ニデック株式会社

のようなケースでは、
URLのドメインが似ているだけでは採用してはいけません。


==================================================
【部署別IT提案】
==================================================

対象企業の事業内容を踏まえ、
IT営業で提案できる部署を最大4つ作ってください。

1部署につき3～4個。

単なる事業内容ではなく、
その部署に対して何のITを提案できるかを書いてください。


==================================================
【特記事項】
==================================================

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


==================================================
【JSON】
==================================================

必ず以下の形式のJSON配列だけを返してください。

[
  {{
    "company": "入力会社名",
    "official_url": "https://...",
    "company_match": "〇 一致",
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

company_matchは必ず：

「〇 一致」
「✕ 不一致」
「⚠️確認できず」

のいずれか。
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

        st.exception(e)

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
            "会社概要を検索中..."
        )

        fetched_data = []

        for i, company in enumerate(
            company_list
        ):

            search_data = search_company(
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
            "AIによる会社概要・社名照合中..."
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
                    "official_candidates": []
                }

            result = company_map.get(
                company,
                {}
            )

            # --------------------------------
            # 会社概要URL
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
                    "一旦調査対象外",

                "部署別IT提案":
                    department_text,

                "特記事項":
                    notes_text,

                # 内部データ
                "_raw_keywords":
                    department_keywords,

                "_raw_notes":
                    notes_text,

                "_q1_results":
                    fetched_item[
                        "q1_results"
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
        file_name="corporate_search_results.csv",
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
            # 九州
            # --------------------------------
            st.info(
                "九州拠点の調査は一旦保留しています。"
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
            # デバッグ：Q1
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
            # デバッグ：公式候補
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
