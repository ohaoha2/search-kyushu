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
    "news.yahoo.co.jp",
    "baseconnect.in",
    "metoree.com",
    "alarmbox.jp",
    "bigcompany.jp",
    "navitime.co.jp",
    "mynavi.jp",
    "rikunabi.com",
    "en-japan.com",
    "wantedly.com",
    "indeed.com"
]


# ==========================================
# URLからドメイン取得
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
# 第三者サイトかどうか
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
            "max_results": 20
        }

        if include_domains:
            kwargs["include_domains"] = include_domains

        response = client.search(**kwargs)

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
# 公式サイト候補スコア
# ==========================================
def score_official_candidate(
    company: str,
    result: dict
):

    title = result.get(
        "title",
        ""
    ).lower()

    snippet = result.get(
        "snippet",
        ""
    ).lower()

    url = result.get(
        "url",
        ""
    ).lower()

    score = 0

    # 会社名がタイトルにある
    if company.lower() in title:
        score += 20

    # 会社名が本文にある
    if company.lower() in snippet:
        score += 5

    # 公式サイトらしいタイトル
    official_words = [
        "公式",
        "会社概要",
        "会社情報",
        "企業情報",
        "コーポレート",
        "company",
        "corporate"
    ]

    for word in official_words:

        if word.lower() in title:
            score += 5

    # 明らかな第三者サイト除外
    domain = extract_domain(
        result.get("url", "")
    )

    if is_excluded_domain(domain):
        score -= 100

    return score


# ==========================================
# 公式サイト候補抽出
# ==========================================
def find_official_candidate(
    company: str,
    results: list
):

    candidates = []

    for result in results:

        url = result.get(
            "url",
            ""
        )

        domain = extract_domain(url)

        if not domain:
            continue

        if is_excluded_domain(domain):
            continue

        score = score_official_candidate(
            company,
            result
        )

        candidates.append({
            "score": score,
            "domain": domain,
            "url": url,
            "title": result.get(
                "title",
                ""
            )
        })

    if not candidates:
        return None

    candidates.sort(
        key=lambda x: x["score"],
        reverse=True
    )

    return candidates[0]


# ==========================================
# q1・q2 検索
# ==========================================
def search_company(
    company: str,
    api_key: str
):

    # ------------------------------------------
    # q1
    # 公式サイトを探す
    # ------------------------------------------
    q1 = (
        f'"{company}" '
        f'会社概要 公式サイト'
    )

    q1_results = fetch_tavily_results(
        q1,
        api_key
    )

    official_candidate = (
        find_official_candidate(
            company,
            q1_results
        )
    )

    if not official_candidate:

        return {
            "company": company,
            "official_url": None,
            "official_domain": None,
            "q1_results": q1_results,
            "q2_results": []
        }

    official_url = official_candidate[
        "url"
    ]

    official_domain = official_candidate[
        "domain"
    ]

    # ------------------------------------------
    # q2
    # 公式サイト内の具体的な拠点を探す
    # ------------------------------------------
    q2 = (
        f'site:{official_domain} '
        f'九州 福岡 佐賀 長崎 熊本 大分 宮崎 鹿児島 '
        f'支店 支社 営業所 事業所 拠点 '
        f'事業部 法人事業 法人営業 営業部 '
        f'リフォーム事業部 Hub'
    )

    q2_results = fetch_tavily_results(
        q2,
        api_key,
        include_domains=[
            official_domain
        ]
    )

    return {
        "company": company,
        "official_url": official_url,
        "official_domain": official_domain,
        "q1_results": q1_results,
        "q2_results": q2_results
    }


# ==========================================
# JSONパース
# ==========================================
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


# ==========================================
# Gemini
#
# 仕事は「公式検索結果からの転記」
# 判定はさせない
# ==========================================
def extract_company_details(
    batch_data,
    gemini_key
):

    client = genai.Client(
        api_key=gemini_key
    )

    targets = ""

    for i, item in enumerate(batch_data):

        official_domain = (
            item.get(
                "official_domain"
            )
            or ""
        )

        q2_results = item.get(
            "q2_results",
            []
        )

        # --------------------------------------
        # AIにはq2の公式検索結果だけ渡す
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
                for r in q2_results
            ]
        )

        targets += (
            f"\n=== 対象企業{i + 1}: "
            f"{item['company']} ===\n"
            f"公式ドメイン: {official_domain}\n"
            f"【公式サイト内検索結果】\n"
            f"{q2_text if q2_text else 'なし'}\n"
        )

    prompt = f"""
あなたは企業の九州拠点情報を「転記・抽出」する担当者です。

あなたの仕事は判定ではありません。

提供された【公式サイト内検索結果】から、
対象企業自身の具体的な九州拠点だけを正確に抜き出してください。

検索結果に書かれていない情報を推測・補完・一般化してはいけません。


【絶対ルール】

1.
検索結果のURLが対象企業自身の公式ドメインであること。

2.
拠点名・住所は検索結果に実際に記載されているものだけを使う。

3.
AIによる要約・一般化は禁止。

例えば、

「九州エリア」
「九州各県」
「九州エリア店舗・事業所」
「福岡エリア」
「九州の拠点」

などは拠点名として使用してはいけません。

4.
「支店」「支社」「営業所」「事業所」
「事業部」「法人事業部」「法人営業部」
「営業拠点」「Hub」など、
具体的な拠点名が確認できるものを優先。

5.
対象企業と別法人である
子会社・関連会社・グループ会社の拠点は除外。

例えば、

対象企業：
ニデック株式会社

検索結果：
ニデックテクノモータ株式会社 九州事業所

これは別法人なので除外。

6.
住所は検索結果に記載されたものをそのまま使用。

住所が福岡県までしか記載されていないなら、
福岡県とそのまま記載する。

勝手に住所を補完しない。

7.
拠点URLは、その拠点が実際に掲載されている
対象企業自身の公式URLを使う。

8.
過去の移転・閉鎖情報しかないものは除外。

9.
物流センター・配送センター・DCは、
対象企業自身の公式サイトに掲載されていれば候補とする。

ただし、
法人事業部・法人営業部・営業所・支店などの
営業・事業拠点があれば、そちらを優先。

10.
店舗についても対象企業自身の公式ページに掲載されていれば候補とするが、
法人事業部・営業所等がある場合はそちらを優先。

11.
1件でも具体的な九州拠点が確認できた場合は、
必ずすべて具体的な拠点を抽出する。

12.
拠点が確認できない場合は空配列[]。


【重要】

例えば以下のような公式検索結果があった場合：

九州支店
〒810-0001
福岡市中央区天神1-14-18

必ず、

{{
    "name": "九州支店",
    "address": "福岡市中央区天神1-14-18",
    "url": "検索結果に記載された公式URL"
}}

としてください。

「九州エリアの営業拠点」
などに言い換えてはいけません。


以下のJSONだけを返してください。

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

対象：

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

    except Exception as e:

        st.error(
            f"AI抽出エラー: {str(e)}"
        )

        return []


# ==========================================
# notes / keywords用のAI分析
# 拠点判定とは分離
# ==========================================
def analyze_company_metadata(
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
                )[:10]
            ]
        )

        targets += (
            f"\n=== "
            f"{item['company']} ===\n"
            f"{q1_text}\n"
        )

    prompt = f"""
以下の企業についてsales_keywordsとnotesだけを作成してください。

拠点判定は行わないでください。


sales_keywords:
DX営業代行で使えるフックキーワードを10個。


notes:
2023年8月14日以降の以下だけ。
- 社名変更
- 拠点新設・移転・拡張
- M&A
- グループ再編・組織変更
- 新規事業
- 大規模設備投資

明確な根拠がない場合は[]。

ニュースや上場情報だけなら[]。


検索結果：
{targets}


JSONだけ返してください。

[
    {{
        "company": "会社名",
        "sales_keywords": [],
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

    except Exception:

        return []


# ==========================================
# 入力フォーム
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

        # ======================================
        # 会社名抽出
        # ======================================
        lines = raw_input.strip().split("\n")

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

        progress_bar = st.progress(0)
        status_text = st.empty()

        fetched_data = []

        # ======================================
        # Tavily
        # 1社2クエリ
        # ======================================
        status_text.text(
            "検索中..."
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
        # Gemini
        # 拠点抽出
        # ======================================
        status_text.text(
            "公式サイトから拠点情報を抽出中..."
        )

        chunk_size = 10

        detail_map = {}

        for i in range(
            0,
            len(fetched_data),
            chunk_size
        ):

            chunk = fetched_data[
                i:i + chunk_size
            ]

            extracted = extract_company_details(
                chunk,
                gemini_key
            )

            if isinstance(
                extracted,
                list
            ):

                for item in extracted:

                    company = item.get(
                        "company"
                    )

                    details = item.get(
                        "details",
                        []
                    )

                    if not isinstance(
                        details,
                        list
                    ):

                        details = []

                    detail_map[
                        company
                    ] = details

        progress_bar.progress(
            0.75
        )

        # ======================================
        # Gemini
        # キーワード・notes
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

            metadata = analyze_company_metadata(
                chunk,
                gemini_key
            )

            if isinstance(
                metadata,
                list
            ):

                for item in metadata:

                    company = item.get(
                        "company"
                    )

                    metadata_map[
                        company
                    ] = item

        # ======================================
        # 最終結果
        # ======================================
        results = []

        for company in company_list:

            # ----------------------------------
            # 検索データ
            # ----------------------------------
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

            # ----------------------------------
            # AI抽出した拠点
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

                # --------------------------------
                # 必須項目
                # --------------------------------
                if not name:
                    continue

                if not address:
                    continue

                if not url:
                    continue

                # --------------------------------
                # 九州住所
                # --------------------------------
                if not any(
                    pref in address
                    for pref in KYUSHU_PREFECTURES
                ):

                    continue

                # --------------------------------
                # URL
                # 公式ドメイン以外除外
                # --------------------------------
                detail_domain = extract_domain(
                    url
                )

                if not detail_domain:
                    continue

                if is_excluded_domain(
                    detail_domain
                ):

                    continue

                if (
                    official_domain
                    and detail_domain
                    != official_domain
                ):

                    continue

                # --------------------------------
                # AIの一般化を防ぐ
                # --------------------------------
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
            # 最終判定
            # ----------------------------------
            if valid_details:

                status = (
                    "⭕️九州拠点あり"
                )

                reason = (
                    "対象企業自身の公式サイトから、"
                    "具体的な九州内拠点を確認"
                )

            else:

                # q2の公式検索結果が存在するか
                q2_results = search_data.get(
                    "q2_results",
                    []
                )

                if q2_results:

                    status = (
                        "❌九州拠点なし"
                    )

                    reason = (
                        "対象企業の公式サイトを検索したが、"
                        "具体的な九州内の自社拠点を確認できない"
                    )

                else:

                    status = (
                        "⚠️検索確認不足"
                    )

                    reason = (
                        "対象企業の公式サイト内の拠点検索結果を"
                        "十分に取得できなかった"
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
            # 表示用
            # ----------------------------------
            details_summary = ", ".join(
                f"{d['name']} ({d['address']})"
                for d in valid_details
            )

            keywords_summary = ", ".join(
                str(k)
                for k in keywords
            )

            notes_summary = ", ".join(
                str(n)
                for n in notes
            )

            # ----------------------------------
            # 検索URL候補
            # ----------------------------------
            q1_results = search_data.get(
                "q1_results",
                []
            )

            official_candidates = []

            for result in q1_results:

                url = result.get(
                    "url",
                    ""
                )

                domain = extract_domain(
                    url
                )

                if (
                    domain
                    and not is_excluded_domain(
                        domain
                    )
                ):

                    official_candidates.append(
                        {
                            "title": result.get(
                                "title",
                                ""
                            ),
                            "url": url,
                            "domain": domain,
                            "score": score_official_candidate(
                                company,
                                result
                            )
                        }
                    )

            official_candidates.sort(
                key=lambda x: x["score"],
                reverse=True
            )

            # ----------------------------------
            # 結果格納
            # ----------------------------------
            results.append({
                "会社名": company,
                "公式サイト": official_url,
                "判定": status,
                "九州拠点": (
                    details_summary
                    if details_summary
                    else "なし"
                ),
                "フックキーワード": keywords_summary,
                "特記事項": notes_summary,

                "_raw_details": valid_details,
                "_raw_keywords": keywords,
                "_raw_notes": notes_summary,
                "_reason": reason,

                "_q1_results": q1_results,
                "_q2_results": search_data.get(
                    "q2_results",
                    []
                ),
                "_official_candidates":
                    official_candidates
            })

        # ======================================
        # 完了
        # ======================================
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
# 6. 結果表示
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

    columns = [
        "会社名",
        "公式サイト",
        "判定",
        "九州拠点",
        "フックキーワード",
        "特記事項"
    ]

    for col in columns:

        if col not in df_display.columns:
            df_display[col] = ""

    df_display = df_display[
        columns
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
            if r.get(
                "公式サイト"
            ):

                st.markdown(
                    f"**公式サイト:** "
                    f"[{r['公式サイト']}]"
                    f"({r['公式サイト']})"
                )

            # ----------------------------------
            # 判定理由
            # ----------------------------------
            st.info(
                f"**判定根拠:** "
                f"{r['_reason']}"
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
                        f"`{x}`"
                        for x in r[
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
                            f"**{d['name']}**"
                        )

                        st.write(
                            f"住所: {d['address']}"
                        )

                        st.markdown(
                            f"[詳細リンク]"
                            f"({d['url']})"
                        )

            # ----------------------------------
            # 公式候補
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
                            f"{candidate['score']}"
                        )

                        st.write(
                            f"ドメイン: "
                            f"{candidate['domain']}"
                        )

                        st.write(
                            f"タイトル: "
                            f"{candidate['title']}"
                        )

                        st.markdown(
                            f"[URL]"
                            f"({candidate['url']})"
                        )

                        st.divider()

            # ----------------------------------
            # q2公式検索結果
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
                            f"**{result['title']}**"
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
            # q1検索結果
            # ----------------------------------
            if r.get(
                "_q1_results"
            ):

                with st.expander(
                    "公式サイト候補の検索結果を確認"
                ):

                    for result in r[
                        "_q1_results"
                    ]:

                        st.markdown(
                            f"**{result['title']}**"
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
