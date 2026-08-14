import streamlit as st
import json
import os
import re
import pandas as pd
from urllib.parse import urlparse
from tavily import TavilyClient
from google import genai
from google.genai import types


# =========================================================
# Streamlit
# =========================================================
st.set_page_config(
    page_title="企業情報一括検索ツール",
    layout="wide"
)

st.title("企業情報一括検索ツール")


# =========================================================
# APIキー
# =========================================================
tavily_api_key = (
    os.getenv("TAVILY_API_KEY")
    or st.secrets.get("TAVILY_API_KEY", "")
)

gemini_key = (
    os.getenv("GEMINI_API_KEY")
    or st.secrets.get("GEMINI_API_KEY", "")
)


# =========================================================
# 九州
# =========================================================
kyushu_prefectures = [
    "福岡",
    "佐賀",
    "長崎",
    "熊本",
    "大分",
    "宮崎",
    "鹿児島"
]


# =========================================================
# 明らかな第三者サイト
# =========================================================
excluded_domains = [
    "wikipedia.org",
    "yahoo.co.jp",
    "nikkei.com",
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
    "x.com",
    "shukatsu-line.pref.toyama.lg.jp",
    "koyou.pref.shizuoka.jp"
]


# =========================================================
# URL → ドメイン
# =========================================================
def extract_domain(url):

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


# =========================================================
# 第三者ドメイン
# =========================================================
def is_excluded_domain(domain):

    if not domain:
        return True

    return any(
        domain == excluded
        or domain.endswith("." + excluded)
        for excluded in excluded_domains
    )


# =========================================================
# Tavily
# =========================================================
def fetch_tavily_results(
    query,
    api_key,
    include_domains=None
):

    try:

        client = TavilyClient(
            api_key=api_key
        )

        params = {
            "query": query.strip().replace("`", ""),
            "search_depth": "basic",
            "max_results": 5
        }

        if include_domains:
            params["include_domains"] = include_domains

        response = client.search(
            **params
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


# =========================================================
# q1：公式サイト取得
# =========================================================
def search_official_site(
    company,
    api_key
):

    query = (
        f'"{company}" '
        f'会社概要 公式サイト'
    )

    results = fetch_tavily_results(
        query,
        api_key
    )

    if not results:
        return None, None, results

    # ---------------------------------------------
    # まず「対象会社名がタイトルに入っている」
    # 公式らしい結果を探す
    # ---------------------------------------------
    exact_candidates = []

    for result in results:

        url = result.get(
            "url",
            ""
        )

        title = result.get(
            "title",
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

        # 「株式会社」を外した会社名
        company_core = (
            company
            .replace(
                "株式会社",
                ""
            )
            .replace(
                "合同会社",
                ""
            )
            .replace(
                "有限会社",
                ""
            )
            .replace(
                "　",
                ""
            )
            .replace(
                " ",
                ""
            )
        )

        title_core = (
            title
            .replace(
                "株式会社",
                ""
            )
            .replace(
                "合同会社",
                ""
            )
            .replace(
                "有限会社",
                ""
            )
            .replace(
                "　",
                ""
            )
            .replace(
                " ",
                ""
            )
        )

        if company_core and company_core in title_core:

            exact_candidates.append(
                result
            )

    # ---------------------------------------------
    # exact候補があれば最初のもの
    # ---------------------------------------------
    if exact_candidates:

        selected = exact_candidates[0]

        return (
            selected.get("url"),
            extract_domain(
                selected.get("url")
            ),
            results
        )

    # ---------------------------------------------
    # なければ第三者を除外して最初の結果
    # ---------------------------------------------
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

        return (
            url,
            domain,
            results
        )

    return None, None, results


# =========================================================
# q2：公式サイト内検索
# =========================================================
def search_official_domain(
    company,
    official_domain,
    api_key
):

    query = (
        f'site:{official_domain} '
        f'九州 福岡 佐賀 長崎 熊本 大分 宮崎 鹿児島 '
        f'支店 支社 営業所 事業所 事業部 '
        f'営業部 法人営業 法人事業 拠点 '
        f'リフォーム事業部 Hub'
    )

    return fetch_tavily_results(
        query,
        api_key,
        include_domains=[
            official_domain
        ]
    )


# =========================================================
# 1社検索
# =========================================================
def search_company(
    company,
    api_key
):

    official_url, official_domain, q1_results = (
        search_official_site(
            company,
            api_key
        )
    )

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


# =========================================================
# JSON
# =========================================================
def safe_parse_json(text):

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


# =========================================================
# Gemini：拠点抽出
# =========================================================
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
            f"{item.get('official_domain', '')}\n"
            f"【公式サイト内検索結果】\n"
            f"{q2_text if q2_text else 'なし'}\n"
        )

    prompt = f"""
あなたは企業の拠点情報を抽出する担当者です。

以下は対象企業自身の公式ドメイン内を検索した結果です。

検索結果に実際に記載されている情報だけを使い、
九州内の対象企業自身の現在の具体的な拠点を抽出してください。

推測は禁止です。

【優先する拠点】

・本社
・支店
・支社
・営業所
・事業所
・事業部
・営業部
・法人営業部
・法人事業部
・リフォーム事業部
・営業拠点
・Hub

物流センター、配送センター、DC、倉庫も、
対象企業自身の公式拠点として明確に確認できる場合は候補。

ただし営業・事業拠点が確認できる場合は、
そちらを優先してください。

【除外】

・子会社
・関連会社
・グループ会社
・別法人
・代理店
・販売店
・パートナー
・協力会社
・顧客先
・施工現場
・納入先

例：

対象企業：
ニデック株式会社

検索結果：
ニデックテクノモータ株式会社 九州事業所

→ 別法人なので除外。

【重要】

検索結果にある拠点名をそのまま使ってください。

「九州エリア」
「九州各県」
「九州エリア店舗・事業所」
「福岡エリア」
などの曖昧な表現は拠点として使用しない。

住所も検索結果に記載されているものだけを使用。

例えば、

九州支店
〒810-0001 福岡市中央区天神1-14-18

なら、

{{
    "name": "九州支店",
    "address": "福岡市中央区天神1-14-18",
    "url": "その公式ページURL"
}}

としてください。

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


# =========================================================
# Gemini：キーワード・特記事項
# =========================================================
def analyze_metadata(
    batch_data,
    gemini_key
):

    client = genai.Client(
        api_key=gemini_key
    )

    targets = ""

    for item in batch_data:

        context = "\n".join(
            [
                (
                    f"- {r.get('title', '')}\n"
                    f"  {r.get('snippet', '')}\n"
                    f"  {r.get('url', '')}"
                )
                for r in item.get(
                    "q1_results",
                    []
                )
            ]
        )

        targets += (
            f"\n=== {item['company']} ===\n"
            f"{context}\n"
        )

    prompt = f"""
以下の企業について、
フックキーワード10個と特記事項を作成してください。

【フックキーワード】

企業の実際の事業内容から、
DX営業代行で使える具体的なキーワードを10個。

【特記事項】

2023年8月14日以降の重要トピックのみ。

・社名変更
・拠点新設
・拠点移転
・拠点拡張
・M&A
・グループ再編
・組織変更
・新規事業
・大規模設備投資

明確なものがなければ[]。

JSONのみ。

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


# =========================================================
# UI
# =========================================================
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


# =========================================================
# 実行
# =========================================================
if submit_button:

    # ---------------------------------------------
    # キャッシュ・前回結果を使わない
    # ---------------------------------------------
    st.session_state.pop(
        "batch_results",
        None
    )

    if not raw_input.strip():

        st.warning(
            "会社名を入力してください。"
        )

    elif not tavily_api_key or not gemini_key:

        st.error(
            "Streamlitの Secrets に "
            "TAVILY_API_KEY または GEMINI_API_KEY "
            "が設定されていません。"
        )

    else:

        # -----------------------------------------
        # 会社名取得
        # -----------------------------------------
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

        # -----------------------------------------
        # Tavily
        # -----------------------------------------
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

        # -----------------------------------------
        # Gemini：拠点
        # -----------------------------------------
        status_text.text(
            "公式サイトから九州拠点を抽出中..."
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
                        item.get(
                            "company"
                        )
                    ] = item.get(
                        "details",
                        []
                    )

        # -----------------------------------------
        # Gemini：metadata
        # -----------------------------------------
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
                        item.get(
                            "company"
                        )
                    ] = item

        # -----------------------------------------
        # 最終結果
        # -----------------------------------------
        batch_results = []

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

            # -----------------------------------
            # 拠点
            # -----------------------------------
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

                # 九州住所
                if not any(
                    pref in address
                    for pref in kyushu_prefectures
                ):
                    continue

                # 曖昧な拠点名
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

            # -----------------------------------
            # 重複削除
            # -----------------------------------
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

            # -----------------------------------
            # 判定
            # -----------------------------------
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
                    "公式サイト内の拠点検索結果を取得できない"
                )

            # -----------------------------------
            # metadata
            # -----------------------------------
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

            batch_results.append({
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
        ] = batch_results


# =========================================================
# 結果表示
# =========================================================
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

    # =====================================================
    # TSV
    # =====================================================
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

    # =====================================================
    # CSV
    # =====================================================
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

    # =====================================================
    # カード
    # =====================================================
    st.divider()

    st.subheader(
        "各社詳細・カード表示"
    )

    for r in results:

        with st.expander(
            f"{r['会社名']} ── 【 {r['判定']} 】"
        ):

            # ---------------------------------------------
            # 公式サイト
            # ---------------------------------------------
            if r.get(
                "公式サイト"
            ):

                st.markdown(
                    f"**公式サイト:** "
                    f"[{r['公式サイト']}]"
                    f"({r['公式サイト']})"
                )

            # ---------------------------------------------
            # 判定根拠
            # ---------------------------------------------
            if r.get(
                "_reason"
            ):

                st.info(
                    f"**判定根拠:** "
                    f"{r['_reason']}"
                )

            # ---------------------------------------------
            # 特記事項
            # ---------------------------------------------
            if r.get(
                "_raw_notes"
            ):

                st.info(
                    f"**特記事項:** "
                    f"{r['_raw_notes']}"
                )

            # ---------------------------------------------
            # キーワード
            # ---------------------------------------------
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

            # ---------------------------------------------
            # 拠点詳細
            # ---------------------------------------------
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

            # ---------------------------------------------
            # q1確認
            # ---------------------------------------------
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

            # ---------------------------------------------
            # q2確認
            # ---------------------------------------------
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
