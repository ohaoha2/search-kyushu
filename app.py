import streamlit as st
import json
import os
import re
import pandas as pd
from tavily import TavilyClient
from google import genai
from google.genai import types

st.set_page_config(page_title="企業情報一括検索ツール", layout="wide")

st.title("企業情報一括検索ツール")

# ==========================================
# APIキーの自動取得（Secrets優先）
# ==========================================
tavily_api_key = os.getenv("TAVILY_API_KEY") or st.secrets.get("TAVILY_API_KEY", "")
gemini_key = os.getenv("GEMINI_API_KEY") or st.secrets.get("GEMINI_API_KEY", "")

# ==========================================
# 0. セッションステート初期化
# ==========================================
if "search_history" not in st.session_state:
    st.session_state.search_history = []

if "result_cache" not in st.session_state:
    st.session_state.result_cache = {}

# ==========================================
# 1. Tavily API 実行関数
# ==========================================
def fetch_tavily_results(query: str, api_key: str):
    try:
        client = TavilyClient(api_key=api_key)

        response = client.search(
            query=query.strip().replace("`", ""),
            search_depth="basic",
            max_results=5
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


def search_multi_queries(keyword: str, api_key: str):
    # API使用量を抑えるため検索は2クエリのみ
    q1 = f'"{keyword}" 会社概要 公式サイト'
    q2 = f'"{keyword}" 九州 支店 営業所 事業所 事業部 拠点'

    queries = [q1, q2]

    all_results = []
    seen_urls = set()

    for q in queries:
        res_list = fetch_tavily_results(q, api_key)

        for r in res_list:
            url = r.get("url", "")

            if url and url not in seen_urls:
                seen_urls.add(url)
                all_results.append(r)

    if not all_results:
        return "", []

    context = "\n".join(
        [
            f"- タイトル: {r['title']}\n"
            f"  内容: {r['snippet']}\n"
            f"  URL: {r['url']}"
            for r in all_results[:15]
        ]
    )

    return context, all_results


# ==========================================
# 2. JSONパース安全装置
# ==========================================
def safe_parse_json(text):
    try:
        return json.loads(text)

    except json.JSONDecodeError:
        text = re.sub(r"```json|```", "", text).strip()

        match = re.search(r"\[.*\]|\{.*\}", text, re.DOTALL)

        if match:
            return json.loads(match.group(0))

        raise


# ==========================================
# 3. 複数社を一括でAI分析する関数
# ==========================================
def analyze_companies_batch(batch_data, gemini_key):
    client = genai.Client(api_key=gemini_key)

    prompt_targets = ""

    for i, item in enumerate(batch_data):
        prompt_targets += (
            f"\n=== 対象企業 {i + 1}: {item['company']} ===\n"
            f"【検索結果】\n"
            f"{item['context']}\n"
        )

    template = """
あなたは企業の所在調査のプロフェッショナルです。ハルシネーションを厳禁とします。
提供された検索結果から確認できない情報を推測・補完してはいけません。

以下の複数の企業について、それぞれ提供された検索結果を基に厳密に調査し、
結果を必ずJSONの配列（リスト）で返してください。

{prompt_targets}

各企業ごとの共通指示:

1. "company"
入力された会社名をそのまま格納してください。

2. "official_url"
対象企業自身の公式サイトのコーポレートサイトURLを記載してください。
Wikipedia、求人サイト、ニュースサイト等は除外してください。
検索結果から対象企業自身の公式サイトであることを確認できない場合は null としてください。

3. "is_found"
以下の3つのいずれかを設定してください。

"⭕️九州拠点あり"
対象企業自身が現在運営している九州内の直営拠点であることが、
提供された検索結果から明確に確認できる場合のみ。

"❌九州拠点なし"
対象企業自身に現在の九州拠点がないことが、
提供された検索結果から明確に確認できる場合のみ。

"❓判定不明"
上記のどちらとも明確に確認できない場合。

重要：
「⭕️九州拠点あり」と判定するには、少なくとも以下の3点を確認できる必要があります。
- 九州内に存在すること
- 現在稼働していること
- 対象企業自身が運営する拠点であること

「❌九州拠点なし」と判定するには、
対象企業自身に現在の九州拠点がないことを示す明確な根拠が必要です。

「⭕️の根拠が見つからない」という理由だけで「❌」にしないでください。
判断できない場合は「❓判定不明」としてください。

【情報源の優先順位】
九州拠点の判定では、以下の順で情報を重視してください。
1. 対象企業自身の公式サイト
2. 対象企業自身の公式発表・公式ニュースリリース
3. 官公庁・自治体などの公的情報
4. その他の第三者情報

Yahoo!、求人サイト、企業情報サイト、ニュースサイト、まとめサイト等の
第三者情報だけを根拠として「⭕️九州拠点あり」と判定してはいけません。
第三者情報しか確認できない場合は「❓判定不明」としてください。

公式情報と第三者情報が矛盾する場合は、原則として公式情報を優先してください。

また、検索結果に具体的な拠点名や住所が記載されているだけでは、
現在稼働している対象企業自身の拠点とは判断しないでください。

以下は対象企業自身の九州拠点として扱わないでください：
- 施工実績
- 納入実績
- 顧客先
- プロジェクト現場
- 代理店
- 販売店
- パートナー企業
- 協力会社
- 子会社
- 関連会社
- 別法人のグループ会社
- 別法人が運営する店舗
- 別法人が運営する配送センター
- 別法人が運営する物流センター
- 別法人が運営する倉庫

対象企業が持株会社の場合、子会社・グループ会社の拠点を
持株会社自身の拠点として扱わないでください。

また、拠点名・住所・建物名に対象企業名が含まれているだけでは、
対象企業自身の拠点とは判定しないでください。

以下の場合は「❓判定不明」としてください：
- 九州拠点らしき情報はあるが、対象企業自身の拠点であることを確認できない
- 現在も稼働しているか確認できない
- 古い情報と新しい情報が混在している
- 検索結果同士で情報が矛盾している
- 拠点名は確認できるが、直営拠点であることの裏付けが不十分
- 第三者情報しか確認できず、公式情報で裏付けられない

以下の場合は「❌九州拠点なし」としてください：
- 対象企業自身に現在の九州拠点がないことが明確
- 九州にあるのが別法人の子会社・関連会社・グループ会社等の拠点だけであり、
  対象企業自身の九州拠点がないことが確認できる

過去の拠点情報、閉鎖済み拠点、移転前の拠点、
統合・再編前の拠点など、現在稼働していることを確認できない情報だけでは
「⭕️九州拠点あり」と判定しないでください。

現在の情報と過去の情報が混在していて現在の状態を確定できない場合は
「❓判定不明」としてください。

4. "details"
九州内の対象企業自身の現在稼働する直営拠点のうち、
提供された検索結果から確実に確認できるものだけを記載してください。

各拠点は以下の形式：
{
    "name": "拠点名称",
    "address": "住所",
    "url": "その拠点の存在と対象企業自身の拠点であることを裏付けるURL"
}

拠点の裏付けURLは、可能な限り対象企業自身の公式サイト、
公式発表、公式拠点一覧などの一次情報を使用してください。

第三者サイトしか根拠がない拠点は details に含めないでください。

以下の条件をすべて確認できない拠点は details に含めないでください。
- 対象企業自身の拠点である
- 九州内にある
- 現在稼働している

確認できない場合は無理に補完せず、detailsには含めないでください。

店舗、配送センター、物流センター、倉庫などは、
対象企業自身の事業拠点であることが明確に確認できる場合を除き、
detailsには含めないでください。

確実な拠点が確認できない場合は [] としてください。

5. "sales_keywords"
DX営業代行で相手に刺さるフックキーワードを10個のリストで返してください。
企業の事業内容や検索結果から確認できる特徴を踏まえてください。

6. "notes"
提供された検索結果の中に、
ここ3年以内（2023年8月14日以降）の以下のいずれかの重要トピックが
明確に確認できる場合のみ、日付と短い名詞句で簡潔に記載してください。
それ以外は必ず [] としてください。

- 社名変更・商号変更
- 拠点新設、移転、拡張
- M&A、グループ再編、組織変更
- 新規事業立ち上げ
- 大規模な設備投資

関係のないニュースや上場情報などは notes に入れないでください。

必ず以下のJSON配列フォーマットのみで回答してください：

[
    {
        "company": "会社名",
        "official_url": "https://...",
        "is_found": "⭕️九州拠点あり",
        "details": [
            {
                "name": "拠点名",
                "address": "住所",
                "url": "https://..."
            }
        ],
        "sales_keywords": [
            "キーワード1",
            "キーワード2",
            "キーワード3",
            "キーワード4",
            "キーワード5",
            "キーワード6",
            "キーワード7",
            "キーワード8",
            "キーワード9",
            "キーワード10"
        ],
        "notes": []
    }
]

is_found は必ず次のいずれかにしてください：
「⭕️九州拠点あり」
「❌九州拠点なし」
「❓判定不明」
"""

    prompt = template.replace("{prompt_targets}", prompt_targets)

    try:
        response = client.models.generate_content(
            model="gemini-3.5-flash-lite",
            contents=prompt,
            config=types.GenerateContentConfig(
                response_mime_type="application/json"
            ),
        )

        return safe_parse_json(response.text.strip())

    except Exception as e:
        st.error(f"AI分析バッチ処理エラー: {str(e)}")
        return []


# ==========================================
# 4. Streamlit UI 構築
# ==========================================
with st.form(key="batch_search_form"):

    raw_input = st.text_area(
        "会社名リストを入力（スプレッドシートからそのまま貼り付け可能）",
        placeholder="株式会社〇〇\n株式会社△△",
        height=150
    )

    submit_button = st.form_submit_button(
        "一括検索・分析を実行",
        type="primary"
    )


if submit_button:

    if not raw_input.strip():
        st.warning("会社名を入力してください。")

    elif not tavily_api_key or not gemini_key:
        st.error(
            "Streamlitの Secrets に TAVILY_API_KEY または GEMINI_API_KEY が設定されていません。"
        )

    else:
        st.session_state.result_cache = {}

        lines = raw_input.strip().split("\n")

        company_list = []

        for line in lines:
            parts = line.split("\t")
            comp = parts[0].strip()

            if comp and comp not in company_list:
                company_list.append(comp)

        batch_results = []

        progress_bar = st.progress(0)
        status_text = st.empty()

        company_map = {}

        # ------------------------------------------
        # Tavily検索
        # ------------------------------------------
        status_text.text("検索中...")

        fetched_data = []

        for i, comp in enumerate(company_list):

            context, raw_results = search_multi_queries(
                comp,
                tavily_api_key
            )

            fetched_data.append({
                "company": comp,
                "context": context,
                "raw_results": raw_results
            })

            progress_bar.progress(
                (i + 1) / max(len(company_list), 1) * 0.5
            )

        # ------------------------------------------
        # Gemini一括分析
        # ------------------------------------------
        chunk_size = 10

        if fetched_data:

            status_text.text("AIによる一括分析を実行中...")

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

                if isinstance(res_list, list):

                    for r in res_list:

                        comp_name = r.get("company")

                        # ----------------------------------
                        # official_url
                        # ----------------------------------
                        if (
                            not r.get("official_url")
                            or r.get("official_url") in ["null", ""]
                        ):
                            r["official_url"] = None

                        # ----------------------------------
                        # is_found
                        # ----------------------------------
                        if r.get("is_found") not in [
                            "⭕️九州拠点あり",
                            "❌九州拠点なし",
                            "❓判定不明"
                        ]:
                            r["is_found"] = "❓判定不明"

                        # ----------------------------------
                        # details
                        # ----------------------------------
                        if not isinstance(
                            r.get("details"),
                            list
                        ):
                            r["details"] = []

                        # ----------------------------------
                        # sales_keywords
                        # ----------------------------------
                        if not isinstance(
                            r.get("sales_keywords"),
                            list
                        ):
                            r["sales_keywords"] = []

                        # ----------------------------------
                        # notes
                        # ----------------------------------
                        if not isinstance(
                            r.get("notes"),
                            list
                        ):
                            r["notes"] = []

                        company_map[comp_name] = r

                        st.session_state.result_cache[
                            comp_name
                        ] = r

                progress_bar.progress(
                    0.5
                    + (
                        (i + len(chunk))
                        / len(fetched_data)
                    ) * 0.5
                )

        # ------------------------------------------
        # 九州拠点の後処理
        # ------------------------------------------
        kyushu_prefectures = [
            "福岡",
            "佐賀",
            "長崎",
            "熊本",
            "大分",
            "宮崎",
            "鹿児島"
        ]

        for comp in company_list:

            res = company_map.get(
                comp,
                {
                    "is_found": "❓判定不明",
                    "official_url": None,
                    "details": [],
                    "sales_keywords": [],
                    "notes": []
                }
            )

            raw_details = res.get("details", [])

            if not isinstance(raw_details, list):
                raw_details = []

            # ------------------------------------------
            # 九州住所のみ残す
            # ------------------------------------------
            valid_details = []

            for d in raw_details:

                if not isinstance(d, dict):
                    continue

                addr = d.get("address", "")

                if any(
                    pref in addr
                    for pref in kyushu_prefectures
                ):
                    valid_details.append(d)

            res["details"] = valid_details

            # ------------------------------------------
            # 判定整合性
            # trueなのに詳細拠点がない場合は
            # 「判定不明」に落とす
            # ------------------------------------------
            if (
                res.get("is_found")
                == "⭕️九州拠点あり"
                and not valid_details
            ):
                res["is_found"] = "❓判定不明"

            is_found_str = res.get(
                "is_found",
                "❓判定不明"
            )

            # ------------------------------------------
            # official_url
            # ------------------------------------------
            official_url = res.get("official_url")

            if (
                not official_url
                or official_url in ["null", ""]
            ):
                official_url = None

            # ------------------------------------------
            # details
            # ------------------------------------------
            details_summary = ", ".join(
                f"{d.get('name')} ({d.get('address')})"
                for d in valid_details
            )

            # ------------------------------------------
            # sales_keywords
            # ------------------------------------------
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

            # ------------------------------------------
            # notes
            # ------------------------------------------
            notes = res.get(
                "notes",
                []
            )

            if isinstance(
                notes,
                list
            ):
                notes_text = ", ".join(
                    str(x)
                    for x in notes
                )
            else:
                notes_text = (
                    str(notes)
                    if notes
                    else ""
                )

            # ------------------------------------------
            # 結果格納
            # ------------------------------------------
            batch_results.append({
                "会社名": comp,
                "公式サイト": official_url,
                "判定": is_found_str,
                "九州拠点": (
                    details_summary
                    if details_summary
                    else "なし"
                ),
                "フックキーワード": keywords_summary,
                "特記事項": notes_text,
                "_raw_details": valid_details,
                "_raw_keywords": keywords,
                "_raw_notes": notes_text
            })

        progress_bar.progress(1.0)
        status_text.text(
            "すべての処理が完了しました。"
        )

        st.session_state[
            "batch_results"
        ] = batch_results


# ==========================================
# 5. 一覧表示 ＆ ハイパーリンク設定 ＆ コピー機能
# ==========================================
if (
    "batch_results" in st.session_state
    and st.session_state["batch_results"]
):

    results = st.session_state["batch_results"]

    st.divider()
    st.subheader(
        "検索・分析結果一覧"
    )

    df_display = pd.DataFrame(results)

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
            "公式サイト": st.column_config.LinkColumn(
                "公式サイト",
                help="クリックすると公式HPが開きます"
            )
        },
        use_container_width=True
    )

    # ------------------------------------------
    # スプレッドシート用コピー
    # ------------------------------------------
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

    # ------------------------------------------
    # CSVダウンロード
    # ------------------------------------------
    csv_data = df_display.to_csv(
        index=False
    ).encode("utf-8-sig")

    st.download_button(
        label="結果をCSVでダウンロード",
        data=csv_data,
        file_name="kyushu_corporate_search_results.csv",
        mime="csv",
        type="primary"
    )

    # ------------------------------------------
    # 各社詳細・カード表示
    # ------------------------------------------
    st.divider()
    st.subheader(
        "各社詳細・カード表示"
    )

    for r in results:

        with st.expander(
            f"{r['会社名']} ── 【 {r['判定']} 】"
        ):

            # --------------------------------------
            # 公式サイト
            # --------------------------------------
            if r.get("公式サイト"):

                st.markdown(
                    f"**公式サイト:** "
                    f"[{r['公式サイト']}]"
                    f"({r['公式サイト']})"
                )

            # --------------------------------------
            # 特記事項
            # --------------------------------------
            if r.get("_raw_notes"):

                st.info(
                    f"**特記事項:** "
                    f"{r['_raw_notes']}"
                )

            # --------------------------------------
            # フックキーワード
            # --------------------------------------
            if r.get("_raw_keywords"):

                st.markdown(
                    "**フックキーワード:**"
                )

                st.markdown(
                    " ".join(
                        f"`{kw}`"
                        for kw in r["_raw_keywords"]
                    )
                )

            # --------------------------------------
            # 九州拠点
            # --------------------------------------
            if r.get("_raw_details"):

                st.markdown(
                    "**拠点詳細:**"
                )

                for d in r["_raw_details"]:

                    with st.container(
                        border=True
                    ):

                        st.markdown(
                            f"**{d.get('name')}**"
                        )

                        st.write(
                            f"住所: {d.get('address')}"
                        )

                        if (
                            d.get("url")
                            and d.get("url") != "null"
                        ):

                            st.markdown(
                                f"[詳細リンク]"
                                f"({d.get('url')})"
                            )
