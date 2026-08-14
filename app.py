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
    # 目的を分けて検索する
    q1 = f'"{keyword}" 会社概要 公式サイト'
    q2 = f'"{keyword}" 九州 拠点 支店 営業所'
    q3 = f'"{keyword}" 九州 事業所 支社 事業部 営業拠点'

    queries = [q1, q2, q3]
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

    # AIに渡す検索結果
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
# 2. JSONパース安全装置（マークダウン干渉対策済）
# ==========================================
def safe_parse_json(text):
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        # バッククォート3つがマークダウンと干渉しないよう文字列置換を使用
        text = text.replace("```json", "").replace("```", "").strip()
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
以下の複数の企業について、それぞれ提供された検索結果を基に厳密に調査し、結果を必ずJSONの配列（リスト）で返してください。

{prompt_targets}

各企業ごとの共通指示:

1. "company"
入力された会社名をそのまま格納してください。

2. "official_url"
公式サイトのコーポレートサイトURL。
Wikipedia、求人サイト、ニュースサイト等は除外してください。
公式サイトであることを検索結果から確認できない場合は null としてください。

3. "is_found"
九州地方（福岡、佐賀、長崎、熊本、大分、宮崎、鹿児島）に、現在稼働している対象企業自身の直営拠点が明確に確認できる場合のみ true としてください。

対象となる拠点：
- 支店
- 営業所
- 支社
- 工場
- 事業所
- 研究所
- 対象企業自身が運営する事業部
- その他、対象企業自身の営業拠点

以下は対象外です：
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
- フランチャイズ店舗
- 店舗・ショップ・販売拠点
- 配送センター
- 物流センター
- 倉庫
- 単なる配送先・納品先

ただし、店舗・配送センター等とは別に、対象企業自身の法人事業部・営業拠点が明確に確認できる場合は、その拠点を対象としてください。

以下の場合は false としてください：
- 九州に対象企業自身の現在稼働する拠点がないことが明確な場合
- 九州拠点として挙げられているのが、子会社・関連会社・別法人のグループ会社等の拠点である場合
- 過去の拠点情報であり、現在も稼働していることが確認できない場合
- 閉鎖・統合・再編前の拠点しか確認できない場合

以下の場合は null としてください：
- 九州拠点らしき情報はあるが、対象企業自身の拠点であることを確認できない場合
- 現在も稼働しているか確認できない場合
- 検索結果同士で情報が矛盾している場合
- 古い情報と新しい情報が混在し、現在の状態を確定できない場合
- 拠点名は確認できるが、直営拠点であることの裏付けが不十分な場合

「ありそう」「全国展開しているから九州にもありそう」といった推測は禁止してください。

確実に確認できない場合は true にせず、false または null としてください。

4. "details"
九州内の確実な直営拠点ごとの詳細情報をリストにしてください。

各拠点：
- "name": 拠点名称
- "address": 住所
- "url": その拠点の存在および対象企業自身の拠点であることを裏付けるURL

対象企業自身の拠点であることを確認できない拠点は含めないでください。

子会社・関連会社・グループ会社など別法人の拠点は含めないでください。

店舗、配送センター、物流センター、倉庫なども原則として含めないでください。

現在稼働していることを確認できない過去の拠点も含めないでください。

確実な拠点がない場合は [] としてください。

5. "sales_keywords"
DX営業代行で相手に刺さるフックキーワードを10個のリストで返してください。

6. "notes"
提供された検索結果の中に、2023年8月14日以降の以下のいずれかの重要トピックが明確に確認できる場合のみ、日付と短い名詞句で簡潔に記載してください。

- 社名変更・商号変更
- 拠点新設、移転、拡張
- M&A、グループ再編、組織変更
- 新規事業立ち上げ
- 大規模な設備投資

該当しない場合は必ず [] としてください。

関係のないニュースや上場情報などは notes に入れないでください。

必ず以下のJSON配列フォーマットのみで回答してください。

[
    {
        "company": "会社名",
        "official_url": "https://...",
        "is_found": true,
        "details": [
            {
                "name": "...",
                "address": "...",
                "url": "..."
            }
        ],
        "sales_keywords": [
            "キーワード1",
            "キーワード2"
        ],
        "notes": []
    }
]

九州拠点の有無を確実に判定できない場合は、
"is_found": null
としてください。
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

        # ------------------------------------------
        # 会社名リストの取得
        # ------------------------------------------
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
            context, raw_results = search_multi_queries(comp, tavily_api_key)
            fetched_data.append({
                "company": comp,
                "context": context,
                "raw_results": raw_results
            })
            progress_bar.progress((i + 1) / max(len(company_list), 1) * 0.5)

        # ------------------------------------------
        # Gemini一括分析
        # ------------------------------------------
        chunk_size = 10

        if fetched_data:
            status_text.text("AIによる一括分析を実行中...")

            for i in range(0, len(fetched_data), chunk_size):
                chunk = fetched_data[i:i + chunk_size]
                res_list = analyze_companies_batch(chunk, gemini_key)

                if isinstance(res_list, list):
                    for r in res_list:
                        comp_name = r.get("company")

                        if not r.get("official_url") or r.get("official_url") in ["null", ""]:
                            r["official_url"] = None

                        # is_foundをtrue / false / nullに正規化
                        if "is_found" not in r:
                            r["is_found"] = None
                        elif r["is_found"] not in [True, False, None]:
                            r["is_found"] = None

                        # detailsをリストに正規化
                        if not isinstance(r.get("details"), list):
                            r["details"] = []

                        # keywordsをリストに正規化
                        if not isinstance(r.get("sales_keywords"), list):
                            r["sales_keywords"] = []

                        # notesをリストに正規化
                        if not isinstance(r.get("notes"), list):
                            r["notes"] = []

                        company_map[comp_name] = r
                        st.session_state.result_cache[comp_name] = r

                progress_bar.progress(
                    0.5 + ((i + len(chunk)) / len(fetched_data)) * 0.5
                )

        # ------------------------------------------
        # 九州拠点の後処理
        # ------------------------------------------
        kyushu_prefectures = ["福岡", "佐賀", "長崎", "熊本", "大分", "宮崎", "鹿児島"]

        for comp in company_list:
            res = company_map.get(
                comp,
                {
                    "is_found": None,
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
                if any(pref in addr for pref in kyushu_prefectures):
                    valid_details.append(d)

            res["details"] = valid_details

            # ------------------------------------------
            # 判定整合性
            # ------------------------------------------
            if res.get("is_found") is True and not valid_details:
                res["is_found"] = None

            is_found = res.get("is_found")
            if is_found is True:
                is_found_str = "⭕️九州拠点あり"
            elif is_found is False:
                is_found_str = "❌九州拠点なし"
            else:
                is_found_str = "△判定不明"

            # ------------------------------------------
            # official_url
            # ------------------------------------------
            official_url = res.get("official_url")
            if not official_url or official_url in ["null", ""]:
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
            keywords = res.get("sales_keywords", [])
            if not isinstance(keywords, list):
                keywords = []
            keywords_summary = ", ".join(str(x) for x in keywords)

            # ------------------------------------------
            # notes
            # ------------------------------------------
            notes = res.get("notes", [])
            if isinstance(notes, list):
                notes_text = ", ".join(str(x) for x in notes)
            else:
                notes_text = str(notes) if notes else ""

            # ------------------------------------------
            # 結果格納
            # ------------------------------------------
            batch_results.append({
                "会社名": comp,
                "公式サイト": official_url,
                "判定": is_found_str,
                "九州拠点": details_summary if details_summary else "なし",
                "フックキーワード": keywords_summary,
                "特記事項": notes_text,
                "_is_found": is_found,
                "_raw_details": valid_details,
                "_raw_keywords": keywords,
                "_raw_notes": notes_text
            })

        progress_bar.progress(1.0)
        status_text.text("すべての処理が完了しました。")
        st.session_state["batch_results"] = batch_results

# ==========================================
# 5. 一覧表示 ＆ ハイパーリンク設定 ＆ コピー機能
# ==========================================
if "batch_results" in st.session_state and st.session_state["batch_results"]:
    results = st.session_state["batch_results"]

    st.divider()
    st.subheader("検索・分析結果一覧")

    df_display = pd.DataFrame(results)

    expected_columns = [
        "会社名", "公式サイト", "判定", "九州拠点", "フックキーワード", "特記事項"
    ]

    for col in expected_columns:
        if col not in df_display.columns:
            df_display[col] = ""

    df_display = df_display[expected_columns]

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
    tsv_text = df_display.to_csv(sep="\t", index=False)

    with st.expander("スプレッドシート用の一括コピー（タブ区切りテキスト）"):
        st.markdown(
            "下の枠内のテキストをコピーして、"
            "スプレッドシートにそのまま貼り付けることができます。"
        )
        st.code(tsv_text, language="text")

    # ------------------------------------------
    # CSVダウンロード
    # ------------------------------------------
    csv_data = df_display.to_csv(index=False).encode("utf-8-sig")

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
    st.subheader("各社詳細・カード表示")

    for r in results:
        with st.expander(f"{r['会社名']} ── 【 {r['判定']} 】"):
            if r.get("公式サイト"):
                st.markdown(f"**公式サイト:** [{r['公式サイト']}]({r['公式サイト']})")
            
            if r.get("_raw_notes"):
                st.info(f"**特記事項:** {r['_raw_notes']}")
            
            if r.get("_raw_keywords"):
                st.markdown("**フックキーワード:**")
                st.markdown(" ".join(f"`{kw}`" for kw in r["_raw_keywords"]))
            
            if r.get("_raw_details"):
                st.markdown("**拠点詳細:**")
                for d in r["_raw_details"]:
                    with st.container(border=True):
                        st.markdown(f"**{d.get('name')}**")
                        st.write(f"住所: {d.get('address')}")
                        if d.get("url") and d.get("url") != "null":
                            st.markdown(f"[詳細リンク]({d.get('url')})")
