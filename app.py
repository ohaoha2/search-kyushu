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

        parsed = urlparse(
            url
        )

        if not parsed.netloc:
            return None

        domain = parsed.netloc.lower()

        if domain.startswith("www."):
            domain = domain[4:]

        return domain

    except Exception:

        return None


# ==========================================
# 第三者サイトか
# ==========================================
def is_excluded_domain(
    domain: str
):

    if not domain:
        return True

    return any(
        domain == excluded
        or domain.endswith(
            "." + excluded
        )
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
            "query": query.strip().replace(
                "`",
                ""
            ),
            "search_depth": "basic",
            "max_results": 5
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
# q1：公式サイト検索
# ==========================================
def search_official_candidates(
    company: str,
    api_key: str
):

    query = (
        f'"{company}" '
        f'会社概要 公式サイト'
    )

    results = fetch_tavily_results(
        query,
        api_key
    )

    return results


# ==========================================
# q1結果からGeminiで公式URL選定
# ==========================================
def select_official_site(
    company: str,
    q1_results: list,
    gemini_key: str
):

    if not q1_results:
        return None, None

    candidates_text = "\n".join(
        [
            (
                f"候補{i + 1}\n"
                f"タイトル: {r.get('title', '')}\n"
                f"URL: {r.get('url', '')}\n"
                f"内容: {r.get('snippet', '')}\n"
            )
            for i, r in enumerate(
                q1_results
            )
        ]
    )

    prompt = f"""
あなたは企業の公式サイトを特定する担当者です。

対象企業：
{company}

以下の検索結果から、
対象企業自身が運営している公式コーポレートサイトを
1つだけ選んでください。

【最重要】

・対象企業自身の公式サイトであること
・第三者企業情報サイトは禁止
・Wikipedia禁止
・Yahoo禁止
・求人サイト禁止
・SNS禁止
・ニュースサイト禁止
・自治体や官公庁の紹介ページ禁止
・企業情報データベース禁止
・別法人のサイト禁止
・子会社サイト禁止
・グループ会社サイト禁止

特に「会社概要」「会社情報」「企業情報」「コーポレート」
などが対象企業自身のドメインに存在する場合を優先してください。

URLは検索結果に実際に存在するものだけを使用してください。

対象企業と別会社が似た社名で存在する場合は、
必ず対象企業自身のサイトを選んでください。

該当する公式サイトが明確に確認できない場合は
nullを返してください。

以下のJSONのみ返してください。

{{
    "official_url": "https://...",
    "official_domain": "example.co.jp"
}}

検索結果：

{candidates_text}
"""

    try:

        client = genai.Client(
            api_key=gemini_key
        )

        response = client.models.generate_content(
            model="gemini-3.5-flash-lite",
            contents=prompt,
            config=types.GenerateContentConfig(
                response_mime_type="application/json"
            )
        )

        data = safe_parse_json(
            response.text.strip()
        )

        official_url = data.get(
            "official_url"
        )

        official_domain = data.get(
            "official_domain"
        )

        if (
            not official_url
            or official_url == "null"
        ):
            return None, None

        # --------------------------------------
        # Geminiが勝手にURLを作っていないか確認
        # 検索結果に実際に存在するURLのみ許可
        # --------------------------------------
        candidate_urls = [
            r.get(
                "url",
                ""
            )
            for r in q1_results
        ]

        matched_url = None

        for url in candidate_urls:

            if url == official_url:

                matched_url = url
                break

        # 完全一致しない場合はドメイン一致を確認
        if not matched_url:

            selected_domain = (
                extract_domain(
                    official_url
                )
            )

            for url in candidate_urls:

                domain = extract_domain(
                    url
                )

                if (
                    selected_domain
                    and domain
                    and selected_domain == domain
                ):

                    matched_url = url
                    break

        if not matched_url:
            return None, None

        matched_domain = extract_domain(
            matched_url
        )

        if (
            not matched_domain
            or is_excluded_domain(
                matched_domain
            )
        ):
            return None, None

        return (
            matched_url,
            matched_domain
        )

    except Exception:

        return None, None


# ==========================================
# q2：確定した公式ドメイン内を検索
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
    api_key: str,
    gemini_key: str
):

    # ------------------------------------------
    # q1
    # ------------------------------------------
    q1_results = search_official_candidates(
        company,
        api_key
    )

    # ------------------------------------------
    # Geminiで公式URL決定
    # ------------------------------------------
    official_url, official_domain = (
        select_official_site(
            company,
            q1_results,
            gemini_key
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
# Gemini：公式検索結果から拠点を抽出
# ==========================================
def extract_locations(
    batch_data,
    gemini_key
):

    client = genai.Client(
        api_key=gemini_key
    )

    targets = ""

    for i, item in enumerate(
        batch_data
    ):

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
            f"{item.get('official_domain')}\n"
            f"【公式サイト内検索結果】\n"
            f"{q2_text if q2_text else 'なし'}\n"
        )

    prompt = f"""
企業の公式サイト内検索結果から、
九州内の具体的な拠点を抽出してください。

判定はしません。
推測もしません。

検索結果に実際に書かれている情報だけを使ってください。

対象企業自身の拠点のみ。

除外：

・子会社
・関連会社
・グループ会社
・別法人
・代理店
・販売店
・協力会社
・顧客先

特に以下を優先：

・支店
・支社
・営業所
・事業所
・事業部
・法人営業部
・法人事業部
・営業拠点
・リフォーム事業部
・Hub

物流センター・配送センター・DCも
対象企業自身の公式拠点であれば抽出候補。

ただし営業・事業活動の拠点を優先。

「九州エリア」
「九州各県」
「九州エリア店舗・事業所」
などの曖昧な表現は拠点として使用しない。

拠点名・住所は原文どおり。

例えば検索結果に、

九州支店
〒810-0001 福岡市中央区天神1-14-18

とあれば、そのまま抽出。

JSONのみ返してください。

[
    {{
        "company": "会社名",
        "details": [
            {{
                "name": "拠点名",
                "address": "住所",
                "url": "公式URL"
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

        results_text = "\n".join(
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
            f"{results_text}\n"
        )

    prompt = f"""
以下の企業について、
sales_keywordsとnotesを作成してください。

拠点判定はしないでください。

sales_keywords：
企業の事業内容からDX営業代行で使える
フックキーワードを10個。

notes：
2023年8月14日以降の、
以下の重要トピックのみ。

・社名変更
・拠点新設
・拠点移転
・拠点拡張
・M&A
・グループ再編
・組織変更
・新規事業
・大規模設備投資

明確な根拠がなければ[]。

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

        progress_bar = st.progress(
            0
        )

        status_text = st.empty()

        fetched_data = []

        # ======================================
        # Tavily q1/q2
        # ======================================
        status_text.text(
            "公式サイト検索・公式サイト内検索中..."
        )

        for i, company in enumerate(
            company_list
        ):

            data = search_company(
                company,
                tavily_api_key,
                gemini_key
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
        # Gemini：拠点抽出
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
        # Gemini：metadata
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

                # URLがある場合、公式ドメイン以外を除外
                if url:

                    detail_domain = extract_domain(
                        url
                    )

                    if (
                        detail_domain
                        and official_domain
                        and detail_domain
                        != official_domain
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
            # metadata
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

            # ----------------------------------
            # 表示
            # ----------------------------------
            details_summary = ", ".join(
                [
                    f"{d['name']} "
                    f"({d['address']})"
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

            if r.get(
                "公式サイト"
            ):

                st.markdown(
                    f"**公式サイト:** "
                    f"[{r['公式サイト']}]"
                    f"({r['公式サイト']})"
                )

            if r.get(
                "_reason"
            ):

                st.info(
                    f"**判定根拠:** "
                    f"{r['_reason']}"
                )

            if r.get(
                "_raw_notes"
            ):

                st.info(
                    f"**特記事項:** "
                    f"{r['_raw_notes']}"
                )

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

                        if d.get(
                            "url"
                        ):

                            st.markdown(
                                f"[詳細リンク]"
                                f"({d.get('url')})"
                            )

            # ----------------------------------
            # 公式URL選定の確認
            # ----------------------------------
            if r.get(
                "_official_domain"
            ):

                with st.expander(
                    "確定した公式ドメインを確認"
                ):

                    st.write(
                        r[
                            "_official_domain"
                        ]
                    )

            # ----------------------------------
            # q1
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
                                result["snippet"]
                            )

                        if result.get(
                            "url"
                        ):

                            st.markdown(
                                f"[URL]"
                                f"({result['url']})"
                            )

                        st.divider()

            # ----------------------------------
            # q2
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
                                result["snippet"]
                            )

                        if result.get(
                            "url"
                        ):

                            st.markdown(
                                f"[URL]"
                                f"({result['url']})"
                            )

                        st.divider()
