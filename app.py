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
# 九州都道府県
# ==========================================
KYUSHU_PREFECTURES = [
    "福岡",
    "佐賀",
    "長崎",
    "熊本",
    "大分",
    "宮崎",
    "鹿児島"
]


# ==========================================
# 明らかな第三者サイト
# ==========================================
EXCLUDED_DOMAINS = [
    "wikipedia.org",
    "yahoo.co.jp",
    "baseconnect.in",
    "metoree.com",
    "alarmbox.jp",
    "bigcompany.jp",
    "navitime.co.jp",
    "mynavi.jp",
    "rikunabi.com",
    "indeed.com",
    "wantedly.com",
    "instagram.com",
    "facebook.com",
    "linkedin.com",
    "x.com"
]


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
# 第三者ドメイン判定
# ==========================================
def is_excluded_domain(domain: str):

    if not domain:
        return True

    return any(
        domain == excluded
        or domain.endswith("." + excluded)
        for excluded in EXCLUDED_DOMAINS
    )


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

        kwargs = {
            "query": query.strip().replace("`", ""),
            "search_depth": "basic",
            "max_results": 5
        }

        if include_domains:
            kwargs["include_domains"] = include_domains

        response = client.search(
            **kwargs
        )

        results = []

        for item in response.get("results", []):

            results.append({
                "title": item.get("title", ""),
                "url": item.get("url", ""),
                "snippet": item.get("content", "")
            })

        return results

    except Exception:
        return []


# ==========================================
# q1：公式サイト検索
# ==========================================
def search_official_site(
    company: str,
    api_key: str
):

    q1 = (
        f'"{company}" '
        f'会社概要 公式 コーポレートサイト'
    )

    results = fetch_tavily_results(
        q1,
        api_key
    )

    if not results:
        return None, None, results

    # ------------------------------------------
    # 公式サイト候補を単純に選ぶ
    # 「公式URLをGeminiに選ばせない」
    # ------------------------------------------

    # 第1優先：
    # タイトル・内容に会社名＋公式系キーワードがある
    candidates = []

    company_name = company.lower()

    for result in results:

        url = result.get("url", "")
        title = result.get("title", "")
        snippet = result.get("snippet", "")

        domain = extract_domain(url)

        if not domain:
            continue

        if is_excluded_domain(domain):
            continue

        score = 0

        title_lower = title.lower()
        snippet_lower = snippet.lower()

        # 会社名一致
        if company_name in title_lower:
            score += 30

        if company_name in snippet_lower:
            score += 10

        # 公式系ページ
        official_words = [
            "公式",
            "会社概要",
            "会社情報",
            "企業情報",
            "コーポレート",
            "corporate",
            "company"
        ]

        for word in official_words:

            if word.lower() in title_lower:
                score += 10

        # URL自体に会社名の文字列が含まれる
        company_clean = (
            company
            .replace("株式会社", "")
            .replace("合同会社", "")
            .replace("有限会社", "")
            .replace(" ", "")
            .replace("　", "")
            .lower()
        )

        domain_clean = domain.replace(
            ".",
            ""
        )

        if (
            company_clean
            and company_clean in domain_clean
        ):
            score += 20

        candidates.append({
            "score": score,
            "url": url,
            "domain": domain
        })

    if not candidates:
        return None, None, results

    candidates.sort(
        key=lambda x: x["score"],
        reverse=True
    )

    best = candidates[0]

    return (
        best["url"],
        best["domain"],
        results
    )


# ==========================================
# q2：取得した公式ドメイン内を検索
# ==========================================
def search_official_domain(
    company: str,
    official_domain: str,
    api_key: str
):

    query = (
        f'site:{official_domain} '
        f'九州 福岡 佐賀 長崎 熊本 大分 宮崎 鹿児島 '
        f'支店 支社 営業所 事業所 事業部 '
        f'法人営業 法人事業 営業拠点 拠点 '
        f'リフォーム事業部 Hub'
    )

    return fetch_tavily_results(
        query,
        api_key,
        include_domains=[
            official_domain
        ]
    )


# ==========================================
# 1社分の検索
# ==========================================
def search_company(
    company: str,
    api_key: str
):

    # ------------------------------------------
    # q1
    # ------------------------------------------
    official_url, official_domain, q1_results = (
        search_official_site(
            company,
            api_key
        )
    )

    # ------------------------------------------
    # q2
    # ------------------------------------------
    q2_results = []

    if official_domain:

        q2_results = search_official_domain(
            company,
            official_domain,
            api_key
        )

    return {
        "company": company,
        "official_url": official_url,
        "official_domain": official_domain,
        "q1_results": q1_results,
        "q2_results": q2_results
    }


# ==========================================
# JSON安全パース
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
# Gemini：公式検索結果から拠点抽出
# ==========================================
def extract_locations(
    batch_data,
    gemini_key
):

    client = genai.Client(
        api_key=gemini_key
    )

    targets = ""

    for i, item in enumerate(batch_data):

        q2_results = item.get(
            "q2_results",
            []
        )

        q2_text = "\n".join(
            [
                (
                    f"- タイトル: {r.get('title', '')}\n"
                    f"  内容: {r.get('snippet', '')}\n"
                    f"  URL: {r.get('url', '')}"
                )
                for r in q2_results
            ]
        )

        targets += (
            f"\n=== 対象企業 {i + 1}: "
            f"{item['company']} ===\n"
            f"公式ドメイン: "
            f"{item.get('official_domain', '')}\n"
            f"【公式サイト内検索結果】\n"
            f"{q2_text if q2_text else 'なし'}\n"
        )

    prompt = f"""
あなたは企業拠点情報の抽出担当です。

以下の検索結果は、対象企業自身の公式ドメイン内を検索した結果です。

検索結果から、九州内にある対象企業自身の現在の具体的な拠点だけを抽出してください。

判定そのものはしません。
推測・補完もしません。

【対象とする拠点】

・本社
・支店
・支社
・営業所
・事業所
・事業部
・営業部
・法人営業部
・法人事業部
・営業拠点
・リフォーム事業部
・Hub
・その他の恒常的な営業・事業拠点

物流センター、配送センター、DC、倉庫も
対象企業自身の公式サイト上で事業拠点であることが明確なら抽出候補です。

【除外】

・子会社
・関連会社
・グループ会社
・別法人
・代理店
・販売店
・パートナー企業
・協力会社
・施工現場
・顧客先
・納入先

例えば、

対象企業：
ニデック株式会社

検索結果：
ニデックテクノモータ株式会社 九州事業所

これは別法人なので除外してください。

【重要】

検索結果に実際に記載されている名称・住所だけを使用してください。

「九州エリア」
「九州各県」
「福岡エリア」
「九州エリア店舗・事業所」
などの一般的な表現は拠点として扱わないでください。

拠点名を勝手に作ってはいけません。

例えば検索結果に、

九州支店
〒810-0001 福岡市中央区天神1-14-18

とあれば、

"name": "九州支店"
"address": "福岡市中央区天神1-14-18"

と、そのまま記載してください。

JSONのみ返してください。

[
    {{
        "company": "会社名",
        "details": [
            {{
                "name": "拠点名",
                "address": "住所",
                "url": "公式ページURL"
            }}
        ]
    }}
]

検索結果：

{targets}
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

    except Exception:

        return []


# ==========================================
# Gemini：キーワード・特記事項
# ==========================================
def analyze_metadata(
    batch_data,
    gemini_key
):

    client = genai.Client(
        api_key=gemini_key
    )

    targets = ""

    for item in batch_data:

        q1_text = "\n".join(
            [
                (
                    f"- {r.get('title', '')}\n"
                    f"  {r.get('snippet', '')}\n"
                    f"  {r.get('url', '')}"
                )
                for r in item.get(
                    "q1_results",
                    []
                )[:5]
            ]
        )

        targets += (
            f"\n=== {item['company']} ===\n"
            f"{q1_text}\n"
        )

    prompt = f"""
以下の企業について、
フックキーワード10個と特記事項を作成してください。

拠点判定はしません。

【フックキーワード】

DX営業代行で使える、
企業の事業内容に関連したキーワードを10個。

単なる「DX」だけではなく、
その企業固有の事業内容を優先してください。

【特記事項】

2023年8月14日以降の以下の重要トピックのみ。

・社名変更
・拠点新設
・拠点移転
・拠点拡張
・M&A
・グループ再編
・組織変更
・新規事業
・大規模設備投資

明確な根拠がない場合は[]。

JSONのみ返してください。

[
    {{
        "company": "会社名",
        "sales_keywords": [],
        "notes": []
    }}
]

検索結果：

{targets}
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

    except Exception:

        return []


# ==========================================
# UI
# ==========================================
with st.form(
    key="batch_search_form"
):

    raw_input = st.text_area(
        "会社名リストを入力（スプレッドシートからそのまま貼り付け可能）",
        placeholder="株式会社〇〇\n株式会社△△",
        height=150
    )

    submit_button = st.form_submit_button(
        "一括検索・分析を実行",
        type="primary"
    )


# ==========================================
# 実行
# ==========================================
if submit_button:

    # 古い結果を削除
    st.session_state.pop(
        "batch_results",
        None
    )

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

        lines = raw_input.strip().split(
            "\n"
        )

        company_list = []

        for line in lines:

            parts = line.split("\t")

            company = parts[0].strip()

            if (
                company
                and company not in company_list
            ):

                company_list.append(
                    company
                )

        progress_bar = st.progress(
            0
        )

        status_text = st.empty()

        fetched_data = []

        # ======================================
        # q1/q2
        # ======================================
        status_text.text(
            "公式サイト検索・公式サイト内検索中..."
        )

        for i, company in enumerate(
            company_list
        ):

            data = search_company(
                company,
                tavily_api_key
            )

            fetched_data.append(
                data
            )

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
        # 拠点抽出
        # ======================================
        status_text.text(
            "公式サイトから拠点情報を抽出中..."
        )

        detail_map = {}

        chunk_size = 10

        for i in range(
            0,
            len(fetched_data),
            chunk_size
        ):

            chunk = fetched_data[
                i:i + chunk_size
            ]

            extracted = extract_locations(
                chunk,
                gemini_key
            )

            if isinstance(
                extracted,
                list
            ):

                for item in extracted:

                    detail_map[
                        item.get("company")
                    ] = item.get(
                        "details",
                        []
                    )

        # ======================================
        # メタデータ
        # ======================================
        status_text.text(
            "企業情報を整理中..."
        )

        metadata_map = {}

        for i in range(
            0,
            len(fetched_data),
            chunk_size
        ):

            chunk = fetched_data[
                i:i + chunk_size
            ]

            metadata = analyze_metadata(
                chunk,
                gemini_key
            )

            if isinstance(
                metadata,
                list
            ):

                for item in metadata:

                    metadata_map[
                        item.get("company")
                    ] = item

        # ======================================
        # 最終結果
        # ======================================
        results = []

        for company in company_list:

            search_data = next(
                (
                    x
                    for x in fetched_data
                    if x["company"] == company
                ),
                None
            )

            if not search_data:

                search_data = {
                    "official_url": None,
                    "official_domain": None,
                    "q1_results": [],
                    "q2_results": []
                }

            official_url = search_data.get(
                "official_url"
            )

            official_domain = search_data.get(
                "official_domain"
            )

            q2_results = search_data.get(
                "q2_results",
                []
            )

            # ----------------------------------
            # 拠点
            # ----------------------------------
            raw_details = detail_map.get(
                company,
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
                    for pref in KYUSHU_PREFECTURES
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

                # 拠点URLが第三者サイトなら除外
                if url:

                    detail_domain = extract_domain(
                        url
                    )

                    if (
                        detail_domain
                        and official_domain
                        and detail_domain != official_domain
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

            seen = set()

            for d in valid_details:

                key = (
                    d["name"],
                    d["address"],
                    d["url"]
                )

                if key in seen:
                    continue

                seen.add(key)

                unique_details.append(
                    d
                )

            valid_details = unique_details

            # ----------------------------------
            # 判定
            # ----------------------------------
            if not official_domain:

                status = "❓判定不明"

                reason = (
                    "対象企業自身の公式サイトを"
                    "確認できませんでした"
                )

            elif valid_details:

                status = "⭕️九州拠点あり"

                reason = (
                    "対象企業の公式サイト内で、"
                    "具体的な九州内拠点を確認"
                )

            elif q2_results:

                status = "❌九州拠点なし"

                reason = (
                    "対象企業の公式サイト内を検索したが、"
                    "具体的な九州内の自社拠点を確認できない"
                )

            else:

                status = "❓判定不明"

                reason = (
                    "公式サイトは確認できたが、"
                    "公式サイト内の九州拠点検索結果を取得できない"
                )

            # ----------------------------------
            # キーワード
            # ----------------------------------
            metadata = metadata_map.get(
                company,
                {}
            )

            keywords = metadata.get(
                "sales_keywords",
                []
            )

            if not isinstance(
                keywords,
                list
            ):
                keywords = []

            notes = metadata.get(
                "notes",
                []
            )

            if not isinstance(
                notes,
                list
            ):
                notes = []

            details_summary = ", ".join(
                [
                    f"{d['name']} ({d['address']})"
                    for d in valid_details
                ]
            )

            keywords_summary = ", ".join(
                str(x)
                for x in keywords
            )

            notes_summary = ", ".join(
                str(x)
                for x in notes
            )

            results.append({
                "会社名": company,
                "公式サイト": official_url,
                "判定": status,
                "九州拠点": (
                    details_summary
                    if details_summary
                    else "なし"
                ),
                "フックキーワード":
                    keywords_summary,
                "特記事項":
                    notes_summary,

                "_raw_details":
                    valid_details,
                "_raw_keywords":
                    keywords,
                "_raw_notes":
                    notes_summary,
                "_reason":
                    reason,
                "_official_domain":
                    official_domain,
                "_q1_results":
                    search_data.get(
                        "q1_results",
                        []
                    ),
                "_q2_results":
                    q2_results
            })

        progress_bar.progress(
            1.0
        )

        status_text.text(
            "すべての処理が完了しました。"
        )

        st.session_state[
            "batch_results"
        ] = results


# ==========================================
# 結果表示
# ==========================================
if (
    "batch_results" in st.session_state
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
        mime="csv",
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
            if r.get("公式サイト"):

                st.markdown(
                    f"**公式サイト:** "
                    f"[{r['公式サイト']}]"
                    f"({r['公式サイト']})"
                )

            # ----------------------------------
            # 判定根拠
            # ----------------------------------
            if r.get("_reason"):

                st.info(
                    f"**判定根拠:** "
                    f"{r['_reason']}"
                )

            # ----------------------------------
            # 特記事項
            # ----------------------------------
            if r.get("_raw_notes"):

                st.info(
                    f"**特記事項:** "
                    f"{r['_raw_notes']}"
                )

            # ----------------------------------
            # キーワード
            # ----------------------------------
            if r.get("_raw_keywords"):

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
            # 拠点
            # ----------------------------------
            if r.get("_raw_details"):

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

                        if d.get("url"):

                            st.markdown(
                                f"[詳細リンク]"
                                f"({d.get('url')})"
                            )

            # ----------------------------------
            # q1
            # ----------------------------------
            if r.get("_q1_results"):

                with st.expander(
                    "公式サイト候補を確認"
                ):

                    for result in r[
                        "_q1_results"
                    ]:

                        st.markdown(
                            f"**{result.get('title', '')}**"
                        )

                        if result.get("snippet"):

                            st.write(
                                result["snippet"]
                            )

                        if result.get("url"):

                            st.markdown(
                                f"[URL]"
                                f"({result['url']})"
                            )

                        st.divider()

            # ----------------------------------
            # q2
            # ----------------------------------
            if r.get("_q2_results"):

                with st.expander(
                    "公式サイト内の拠点検索結果を確認"
                ):

                    for result in r[
                        "_q2_results"
                    ]:

                        st.markdown(
                            f"**{result.get('title', '')}**"
                        )

                        if result.get("snippet"):

                            st.write(
                                result["snippet"]
                            )

                        if result.get("url"):

                            st.markdown(
                                f"[URL]"
                                f"({result['url']})"
                            )

                        st.divider()
