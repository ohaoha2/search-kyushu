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
    "baseconnect.in",
    "metoree.com",
    "alarmbox.jp",
    "bigcompany.jp",
    "navitime.co.jp",
    "mynavi.jp",
    "rikunabi.com",
    "indeed.com",
    "wantedly.com"
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
# 1社2クエリ
# ==========================================
def search_multi_queries(
    company: str,
    api_key: str
):

    # ------------------------------------------
    # q1
    # 公式サイトを取得
    # ------------------------------------------
    q1 = (
        f'"{company}" '
        f'会社概要 公式サイト'
    )

    q1_results = fetch_tavily_results(
        q1,
        api_key
    )

    # ------------------------------------------
    # q1結果から公式URL候補を決定
    # ------------------------------------------
    official_url = None
    official_domain = None

    for result in q1_results:

        url = result.get(
            "url",
            ""
        )

        domain = extract_domain(url)

        if not domain:
            continue

        if is_excluded_domain(
            domain
        ):
            continue

        # 公式候補らしいタイトルを優先
        title = result.get(
            "title",
            ""
        )

        title_lower = title.lower()

        official_words = [
            "公式",
            "会社概要",
            "会社情報",
            "企業情報",
            "コーポレート",
            "corporate",
            "company"
        ]

        if any(
            word.lower() in title_lower
            for word in official_words
        ):

            official_url = url
            official_domain = domain
            break

    # 公式候補らしいタイトルが無かった場合
    if not official_domain:

        for result in q1_results:

            url = result.get(
                "url",
                ""
            )

            domain = extract_domain(url)

            if not domain:
                continue

            if is_excluded_domain(
                domain
            ):
                continue

            official_url = url
            official_domain = domain
            break

    # ------------------------------------------
    # q2
    # 取得した公式サイト内を検索
    # ------------------------------------------
    q2_results = []

    if official_domain:

        q2 = (
            f'site:{official_domain} '
            f'九州 福岡 佐賀 長崎 熊本 大分 宮崎 鹿児島 '
            f'支店 支社 営業所 事業所 事業部 '
            f'法人営業 法人事業 拠点'
        )

        q2_results = fetch_tavily_results(
            q2,
            api_key,
            include_domains=[
                official_domain
            ]
        )

    # ------------------------------------------
    # AIに渡す検索結果
    # ------------------------------------------
    all_results = []

    seen_urls = set()

    for result in (
        q1_results + q2_results
    ):

        url = result.get(
            "url",
            ""
        )

        if (
            url
            and url not in seen_urls
        ):

            seen_urls.add(url)
            all_results.append(
                result
            )

    context = "\n".join(
        [
            (
                f"- タイトル: {r['title']}\n"
                f"  内容: {r['snippet']}\n"
                f"  URL: {r['url']}"
            )
            for r in all_results
        ]
    )

    return {
        "company": company,
        "official_url": official_url,
        "official_domain": official_domain,
        "q1_results": q1_results,
        "q2_results": q2_results,
        "context": context
    }


# ==========================================
# JSONパース
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

        prompt_targets += (
            f"\n=== 対象企業 {i + 1}: "
            f"{item['company']} ===\n"
            f"【公式サイトURL】\n"
            f"{item.get('official_url')}\n"
            f"【公式ドメイン】\n"
            f"{item.get('official_domain')}\n"
            f"【検索結果】\n"
            f"{item.get('context', '')}\n"
        )

    prompt = f"""
あなたは企業の所在調査のプロフェッショナルです。

以下の検索結果を基に各企業を調査してください。
ハルシネーションを厳禁とします。
検索結果にない情報を推測・補完してはいけません。

{prompt_targets}


==================================================
【公式サイトURL】
==================================================

official_urlは、入力された【公式サイトURL】を
そのまま使用してください。

別のURLに変更してはいけません。


==================================================
【九州拠点判定】
==================================================

以下の3種類のみ使用してください。

⭕️九州拠点あり
❌九州拠点なし
❓判定不明


【⭕️九州拠点あり】

対象企業自身の現在の九州内拠点が、
提供された公式サイト検索結果から確認できる場合。


【❌九州拠点なし】

対象企業自身の現在の九州内拠点がないことが、
提供された公式情報から確認できる場合。


【❓判定不明】

公式情報だけでは現在の九州拠点の有無を確認できない場合。


「⭕️の根拠が見つからない」
というだけで❌にしてはいけません。


==================================================
【重要：情報源】
==================================================

最優先：

1. 対象企業自身の公式サイト
2. 対象企業自身の公式発表

第三者サイトだけの情報では、
⭕️にしてはいけません。

Yahoo!
Wikipedia
Baseconnect
Metoree
求人サイト
企業情報サイト
ニュースサイト
その他第三者サイト

だけを根拠として⭕️にすることは禁止です。


==================================================
【別法人除外】
==================================================

以下は対象企業の拠点として扱いません。

・子会社
・関連会社
・グループ会社
・別法人
・代理店
・販売店
・パートナー企業
・協力会社


例えば、

対象企業：
ニデック株式会社

検索結果：
ニデックテクノモータ株式会社 九州事業所

これは別法人なので除外してください。


==================================================
【details】
==================================================

九州内の対象企業自身の具体的な拠点を記載してください。

優先順位：

1. 支店
2. 支社
3. 営業所
4. 事業所
5. 事業部
6. 法人営業部
7. 法人事業部
8. 営業拠点
9. リフォーム事業部
10. その他の恒常的な事業拠点


物流センター・配送センター・DC等も、
対象企業自身の拠点であることが公式情報から確認できれば候補です。

ただし、営業・事業拠点が確認できる場合はそちらを優先してください。


拠点名は検索結果に書かれている名称をそのまま使用してください。

例えば、

「法人事業部福岡」

を

「九州営業拠点」

などに変更してはいけません。


「九州エリア」
「九州各県」
「九州エリア店舗・事業所」

などの曖昧な表現は拠点名として使用しないでください。


住所も検索結果に記載されたものをそのまま使用してください。


各detailsは以下の形式です。

{{
    "name": "拠点名",
    "address": "住所",
    "url": "拠点を確認できる公式URL"
}}


==================================================
【判定とdetails】
==================================================

⭕️の場合はdetailsを1件以上必ず記載。

detailsが[]なのに⭕️にしない。

ただし、
detailsが[]だから自動的に❌にはしない。

公式情報で判断できなければ❓。


==================================================
【reason】
==================================================

判定根拠を短く記載してください。

⭕️：
「公式サイトの事業所一覧に九州支店が掲載されている」

❌：
「公式サイト上で現在の九州内の自社拠点を確認できない」

❓：
「第三者情報では確認できるが、現在の公式情報では確認できない」


==================================================
【sales_keywords】
==================================================

DX営業代行で刺さるフックキーワードを10個。


==================================================
【notes】
==================================================

2023年8月14日以降の以下のみ。

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


==================================================
【JSON】
==================================================

[
    {{
        "company": "会社名",
        "is_found": "⭕️九州拠点あり",
        "reason": "判定理由",
        "details": [
            {{
                "name": "拠点名",
                "address": "住所",
                "url": "https://..."
            }}
        ],
        "sales_keywords": [],
        "notes": []
    }}
]

JSONのみ返してください。
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
            f"AI分析エラー: {str(e)}"
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
        placeholder="株式会社〇〇\n株式会社△△",
        height=150
    )

    submit_button = st.form_submit_button(
        "一括検索・分析を実行",
        type="primary"
    )


# ==========================================
# 検索実行
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

        # --------------------------------------
        # 会社名リスト
        # --------------------------------------
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

        # --------------------------------------
        # Tavily
        # --------------------------------------
        status_text.text(
            "検索中..."
        )

        for i, company in enumerate(
            company_list
        ):

            data = search_multi_queries(
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

        # --------------------------------------
        # Gemini
        # --------------------------------------
        status_text.text(
            "AIによる一括分析を実行中..."
        )

        results_from_ai = []

        chunk_size = 10

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

                results_from_ai.extend(
                    res_list
                )

        # --------------------------------------
        # AI結果を会社名で管理
        # --------------------------------------
        company_map = {}

        for r in results_from_ai:

            company_map[
                r.get("company")
            ] = r

        # --------------------------------------
        # 最終結果
        # --------------------------------------
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

            # ----------------------------------
            # 公式URLはq1結果を採用
            # ----------------------------------
            official_url = search_data.get(
                "official_url"
            )

            # ----------------------------------
            # AI結果
            # ----------------------------------
            res = company_map.get(
                company,
                {
                    "is_found": "❓判定不明",
                    "reason": "検索結果を取得できませんでした。",
                    "details": [],
                    "sales_keywords": [],
                    "notes": []
                }
            )

            # ----------------------------------
            # 判定
            # ----------------------------------
            status = res.get(
                "is_found",
                "❓判定不明"
            )

            if status not in [
                "⭕️九州拠点あり",
                "❌九州拠点なし",
                "❓判定不明"
            ]:

                status = "❓判定不明"

            # ----------------------------------
            # details
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

                if not name:
                    continue

                if not address:
                    continue

                # 九州住所
                if not any(
                    pref in address
                    for pref in KYUSHU_PREFECTURES
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

            # ----------------------------------
            # detailsがないのに⭕️
            # ----------------------------------
            if (
                status
                == "⭕️九州拠点あり"
                and not valid_details
            ):

                status = "❓判定不明"

                reason = (
                    "九州拠点ありとの情報はあるが、"
                    "具体的な拠点情報を確認できませんでした。"
                )

            else:

                reason = str(
                    res.get(
                        "reason",
                        ""
                    )
                )

            # ----------------------------------
            # キーワード
            # ----------------------------------
            keywords = res.get(
                "sales_keywords",
                []
            )

            if not isinstance(
                keywords,
                list
            ):

                keywords = []

            keywords_summary = ", ".join(
                str(x)
                for x in keywords
            )

            # ----------------------------------
            # notes
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

            notes_summary = ", ".join(
                str(x)
                for x in notes
            )

            # ----------------------------------
            # 九州拠点表示
            # ----------------------------------
            details_summary = ", ".join(
                [
                    f"{d['name']} ({d['address']})"
                    for d in valid_details
                ]
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
                "_q1_results":
                    search_data.get(
                        "q1_results",
                        []
                    ),
                "_q2_results":
                    search_data.get(
                        "q2_results",
                        []
                    )
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
            if r.get(
                "公式サイト"
            ):

                st.markdown(
                    f"**公式サイト:** "
                    f"[{r['公式サイト']}]"
                    f"({r['公式サイト']})"
                )

            # ----------------------------------
            # 判定根拠
            # ----------------------------------
            reason = r.get(
                "_reason",
                ""
            )

            if reason:

                st.info(
                    f"**判定根拠:** {reason}"
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
                        [
                            f"`{kw}`"
                            for kw in r[
                                "_raw_keywords"
                            ]
                        ]
                    )
                )

            # ----------------------------------
            # 拠点詳細
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

                        if d.get(
                            "url"
                        ):

                            st.markdown(
                                f"[詳細リンク]"
                                f"({d.get('url')})"
                            )

            # ----------------------------------
            # q1確認
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
            # q2確認
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
