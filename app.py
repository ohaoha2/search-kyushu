import streamlit as st
import json
import os
import re
import pandas as pd
from tavily import TavilyClient
from google import genai
from google.genai import types

st.set_page_config(page_title="九州拠点一括検索ツール", page_icon="✨", layout="wide")

st.title("✨ 九州拠点一括検索・フックキーワード提案ツール")
st.markdown("Tavily AIとGeminiを活用した、安定・高速な一括調査ツールです。")

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
            query=query.strip().replace('`', ''),
            search_depth="basic",
            max_results=5
        )
        results = []
        for item in response.get('results', []):
            results.append({
                'title': item.get('title', ''),
                'url': item.get('url', ''),
                'snippet': item.get('content', '')
            })
        return results
    except Exception as e:
        return []

def search_multi_queries(keyword: str, api_key: str):
    q1 = f'"{keyword}" 会社概要 公式サイト'
    q2 = f'"{keyword}" 九州 福岡 支店 営業所'
    q3 = f'"{keyword}" 社名変更 商号変更'
    
    queries = [q1, q2, q3]
    all_results = []
    seen_urls = set()
    
    for q in queries:
        res_list = fetch_tavily_results(q, api_key)
        for r in res_list:
            if r['url'] not in seen_urls:
                seen_urls.add(r['url'])
                all_results.append(r)
                
    if not all_results:
        return "", []
        
    context = "\n".join([f"- タイトル: {r['title']}\n  内容: {r['snippet']}\n  URL: {r['url']}" for r in all_results[:15]])
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
        prompt_targets += f"\n=== 対象企業 {i+1}: {item['company']} ===\n【検索結果】\n{item['context']}\n"

    template = """
あなたは企業の所在調査のプロフェッショナルです。ハルシネーション（存在しない拠点をあると誤認すること）を厳禁とします。
以下の複数の企業について、それぞれ提供された検索結果を基に厳密に調査し、結果を必ず【JSONの配列（リスト）】で返してください。

{prompt_targets}

各企業ごとの共通指示:
1. "company": 入力された会社名をそのまま格納してください。
2. "official_url": 公式サイトのコーポレートサイトURL（Wikipedia、求人サイト、ニュースサイトは除外。見つからない場合は null）
3. "details": 九州地方（福岡県, 佐賀県, 長崎県, 熊本県, 大分県, 宮崎県, 鹿児島県）に実在する確実な直営拠点ごとの詳細情報（名称, 住所, URL）のリスト。※本州や北海道など、九州外の拠点は絶対に含めないこと。見つからない場合は空配列 []
4. "is_found": 上記の九州内の直営拠点が明確に裏付けられる場合のみ true としてください。九州外の拠点しかない場合は必ず false にしてください。
5. "sales_keywords": DX営業代行で相手に刺さるフックキーワード10個のリスト
6. "notes": 提供された検索結果の中に、直近（ここ3年以内）の以下のいずれかの重要トピックがある場合のみ、具体的に1〜2文で簡潔に記載してください。
   - 社名変更・商号変更
   - 拠点新設、移転、拡張
   - M&A、グループ再編、組織変更
   - 新規事業立ち上げや大規模な設備投資

必ず以下のJSON配列フォーマットのみで回答してください：
[
    {
        "company": "会社名",
        "is_found": true,
        "official_url": "https://...",
        "details": [{"name": "...", "address": "...", "url": "..."}],
        "sales_keywords": ["キーワード1", "キーワード2", ...],
        "notes": "直近の社名変更情報のみ（古い歴史は除外）"
    }
]
    """
    prompt = template.replace("{prompt_targets}", prompt_targets)

    try:
        response = client.models.generate_content(
            model='gemini-2.5-flash',
            contents=prompt,
            config=types.GenerateContentConfig(response_mime_type="application/json"),
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
        "📋 会社名リストを入力（スプレッドシートからそのまま貼り付け可能）",
        placeholder="株式会社〇〇\n株式会社△△",
        height=150
    )
    submit_button = st.form_submit_button("一括検索・分析を実行", type="primary")

if submit_button:
    if not raw_input.strip():
        st.warning("会社名を入力してください。")
    elif not tavily_api_key or not gemini_key:
        st.error("⚠️ Streamlitの Secrets に TAVILY_API_KEY または GEMINI_API_KEY が設定されていません。")
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
        
        to_fetch = company_list
        company_map = {}

        status_text.text("🌐 Tavily AIで高速検索を実行中...")
        fetched_data = []
        for i, comp in enumerate(to_fetch):
            context, raw_results = search_multi_queries(comp, tavily_api_key)
            fetched_data.append({
                "company": comp,
                "context": context,
                "raw_results": raw_results
            })
            progress_bar.progress((i + 1) / max(len(to_fetch), 1) * 0.5)

        chunk_size = 10
        
        if fetched_data:
            status_text.text("🤖 AIによる一括分析を実行中...")
            for i in range(0, len(fetched_data), chunk_size):
                chunk = fetched_data[i:i+chunk_size]
                res_list = analyze_companies_batch(chunk, gemini_key)
                
                if isinstance(res_list, list):
                    for r in res_list:
                        comp_name = r.get("company")
                        if not r.get('official_url') or r.get('official_url') in ["null", ""]:
                            for item in chunk:
                                if item['company'] == comp_name and item['raw_results']:
                                    for rr in item['raw_results']:
                                        url_lower = rr['url'].lower()
                                        if not any(x in url_lower for x in ["wikipedia", "job", "wantedly", "en-japan", "rikunabi", "mynavi", "yahoo", "google"]):
                                            r['official_url'] = rr['url']
                                            break
                                    if not r.get('official_url') and item['raw_results']:
                                        r['official_url'] = item['raw_results'][0]['url']
                        
                        company_map[comp_name] = r
                        st.session_state.result_cache[comp_name] = r
                
                progress_bar.progress(0.5 + ((i + len(chunk)) / len(fetched_data)) * 0.5)

        # 九州の県名リスト（強制バリデーション用）
        kyushu_prefectures = ["福岡", "佐賀", "長崎", "熊本", "大分", "宮崎", "鹿児島"]

        for comp in company_list:
            res = company_map.get(comp, {
                "is_found": False,
                "official_url": None,
                "details": [],
                "sales_keywords": [],
                "notes": ""
            })

            # 【強力な安全装置】抽出された拠点の住所に「九州の県名」が含まれているかをPython側で強制チェック
            raw_details = res.get('details', [])
            valid_details = []
            for d in raw_details:
                addr = d.get('address', '')
                if any(pref in addr for pref in kyushu_prefectures):
                    valid_details.append(d)

            # 有効な九州拠点が1つもなければ、強制的に is_found = False に書き換える
            if not valid_details:
                res['is_found'] = False
                valid_details = []

            is_found_str = "⭕ 九州拠点あり" if res.get('is_found') else "❌ 九州拠点なし"
            official_url = res.get('official_url')
            if not official_url or official_url in ["null", ""]: 
                official_url = None
            
            details_summary = ", ".join([f"{d.get('name')} ({d.get('address')})" for d in valid_details])
            keywords_summary = ", ".join(res.get('sales_keywords', []))
            notes_text = res.get('notes', '')

            batch_results.append({
                "会社名": comp,
                "判定": is_found_str,
                "公式サイト": official_url,
                "九州拠点": details_summary if details_summary else "なし",
                "フックキーワード": keywords_summary,
                "特記事項": notes_text,
                "_raw_details": valid_details,
                "_raw_keywords": res.get('sales_keywords', []),
                "_raw_notes": notes_text
            })

        progress_bar.progress(1.0)
        status_text.text("✅ すべての処理が完了しました！")
        st.session_state["batch_results"] = batch_results

# ==========================================
# 5. 一覧表示 ＆ ハイパーリンク設定 ＆ コピー機能
# ==========================================
if "batch_results" in st.session_state and st.session_state["batch_results"]:
    results = st.session_state["batch_results"]
    
    st.divider()
    st.subheader("📊 検索・分析結果一覧")

    df_display = pd.DataFrame(results)
    expected_columns = ["会社名", "判定", "公式サイト", "九州拠点", "フックキーワード", "特記事項"]
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

    tsv_text = df_display.to_csv(sep="\t", index=False)
    with st.expander("📋 スプレッドシート用の一括コピー（タブ区切りテキスト）"):
        st.markdown("下の枠内のテキストをコピーして、スプレッドシートにそのまま貼り付けることができます。")
        st.code(tsv_text, language="text")

    csv_data = df_display.to_csv(index=False).encode('utf-8-sig')
    st.download_button(
        label="📥 結果をCSVでダウンロード",
        data=csv_data,
        file_name="kyushu_corporate_search_results.csv",
        mime="csv",
        type="primary"
    )

    st.divider()
    st.subheader("📍 各社詳細・カード表示")
    
    for r in results:
        with st.expander(f"{r['会社名']} ── 【 {r['判定']} 】"):
            if r.get('公式サイト'):
                st.markdown(f"**🌐 公式サイト:** [{r['公式サイト']}]({r['公式サイト']})")
            
            if r.get('_raw_notes'):
                st.info(f"💡 **特記事項:** {r['_raw_notes']}")
            
            if r.get('_raw_keywords'):
                st.markdown("**🔑 フックキーワード:**")
                st.markdown(" ".join([f"`{kw}`" for kw in r['_raw_keywords']]))
                
            if r.get('_raw_details'):
                st.markdown("**📍 拠点詳細:**")
                for d in r['_raw_details']:
                    with st.container(border=True):
                        st.markdown(f"**{d.get('name')}**")
                        st.write(f"住所: {d.get('address')}")
                        if d.get('url') and d.get('url') != "null":
                            st.markdown(f"[詳細リンク]({d.get('url')})")
