import streamlit as st
import json
import os
import re
import requests
from bs4 import BeautifulSoup
from google import genai
from google.genai import types

st.set_page_config(page_title="九州拠点検索", page_icon="✨")

st.title("九州拠点検索・フックキーワード提案ツール")

# ==========================================
# 0. セッションステート（履歴 ＆ キャッシュ）の初期化
# ==========================================
if "search_history" not in st.session_state:
    st.session_state.search_history = []

if "result_cache" not in st.session_state:
    st.session_state.result_cache = {}

# ==========================================
# 1. キーワードの自動補正（検索クエリの最適化）
# ==========================================
def expand_query_with_ai(keyword: str, gemini_key):
    client = genai.Client(api_key=gemini_key)
    prompt = f"""
    ユーザーが入力したキーワード: "{keyword}"
    
    この企業の【全国の拠点・事業所・店舗一覧、または会社概要】を正しくヒットさせるための、最適なWeb検索クエリを作成してください。
    
    条件:
    1. 【地域名の強制排除】「九州」や「福岡」といった地域名を検索ワードに絶対に含めないでください。
    2. 【前株・後株の厳守】ユーザーが「〇〇株式会社」などと入力した場合、絶対にその表記を崩さず、ダブルクォーテーションで囲んで完全一致検索（例：'"さわやか株式会社"'）にしてください。
    3. キーワードの後半には、「拠点」「事業所」「支店」「店舗」「会社概要」などの一般的な言葉を付与してください。
    
    例:
    「さわやか株式会社」→ `"さわやか株式会社" 会社概要 拠点 店舗`
    「ニデック」→ `ニデック 旧日本電産 拠点 支社 会社概要`
    
    余計な挨拶や解説は省き、検索クエリの文字列（1行）のみを出力してください。
    """
    try:
        response = client.models.generate_content(
            model='gemini-3.5-flash-lite',
            contents=prompt,
        )
        return response.text.strip()
    except:
        return f'"{keyword}" 会社概要 拠点'

# ==========================================
# 2. 【新規】前株・後株の物理フィルタリング関数
# ==========================================
def filter_strict_corporate_type(query: str, results: list):
    """
    検索エンジンが勝手に前株・後株を混ぜてきた場合、
    Python側で物理的に「逆転している検索結果」を削除する関数
    """
    if not ("株式会社" in query or "有限会社" in query or "合同会社" in query):
        return results # 法人格の指定がない場合はそのままスルー
        
    filtered = []
    for r in results:
        text = r['title'] + " " + r['snippet']
        
        # ユーザーが「後株（〇〇株式会社）」で検索した場合
        if query.endswith("株式会社"):
            core_name = query.replace("株式会社", "").strip()
            bad_pattern = f"株式会社{core_name}" # 前株のパターン
            # テキストに「株式会社さわやか」が含まれていて、かつ「さわやか株式会社」が含まれていないなら除外（ゴミ箱行き）
            if bad_pattern in text and query not in text:
                continue
                
        # ユーザーが「前株（株式会社〇〇）」で検索した場合
        elif query.startswith("株式会社"):
            core_name = query.replace("株式会社", "").strip()
            bad_pattern = f"{core_name}株式会社" # 後株のパターン
            if bad_pattern in text and query not in text:
                continue
                
        filtered.append(r)
        
    return filtered

# ==========================================
# 3. DuckDuckGo Lite による検索関数
# ==========================================
def search_ddg_lite(original_query: str, expanded_query: str):
    clean_kw = expanded_query.strip().replace('`', '')
    
    url = "https://lite.duckduckgo.com/lite/"
    data = {'q': clean_kw}
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    }

    try:
        res = requests.post(url, data=data, headers=headers, timeout=10)
        soup = BeautifulSoup(res.text, "html.parser")
        
        results = []
        rows = soup.find_all("tr")
        current_title = ""
        current_url = ""
        
        for row in rows:
            link_tag = row.find("a", class_="result-link")
            if link_tag:
                current_title = link_tag.text.strip()
                current_url = link_tag["href"]
            
            snippet_tag = row.find("td", class_="result-snippet")
            if snippet_tag:
                current_snippet = snippet_tag.text.strip()
                if current_title and current_url:
                    results.append({
                        'title': current_title,
                        'url': current_url,
                        'snippet': current_snippet
                    })
                    current_title = ""
                    current_url = ""

        if not results:
            return None, "検索結果を取得できませんでした。"
            
        # ★ここでPythonによる物理フィルターを発動し、前株・後株違いを消し去る★
        filtered_results = filter_strict_corporate_type(original_query, results)
        
        if not filtered_results:
            return "【システム通知】入力された前株・後株に完全一致する企業の検索結果が見つかりませんでした。", None
            
        context = "\n".join([f"- タイトル: {r['title']}\n  内容: {r['snippet']}\n  URL: {r['url']}" for r in filtered_results[:6]])
        return context, None

    except Exception as e:
        return None, f"検索エラー: {str(e)}"

# ==========================================
# 4. JSONパースの安全装置付き・分析関数
# ==========================================
def safe_parse_json(text):
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        text = re.sub(r"```json", "", text)
        text = re.sub(r"```", "", text).strip()
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if match:
            return json.loads(match.group(0))
        raise

def analyze_company_with_ai(query, web_context, gemini_key):
    client = genai.Client(api_key=gemini_key)
    
    prompt = f"""
    あなたは企業の所在調査およびDX営業戦略のプロフェッショナルです。
    ユーザーが本来検索したかったターゲット: "{query}"

    【取得したWeb検索結果（公式の会社概要や拠点一覧など）】
    {web_context}

    指示:
    1. もし【取得したWeb検索結果】が「見つかりませんでした」というシステム通知だった場合、即座に "is_found": false とし、「指定された法人名（前株・後株の完全一致）の企業情報が確認できませんでした」と回答してください。
    2. 【最重要・同名異法人の厳禁】文字面が一部一致するだけの別法人（例：「さわやか株式会社」に対する「株式会社さわやか倶楽部」など）は完全に別法人とみなして絶対に弾いてください。
    3. 取得した公式情報（検索結果）をもとに、その企業（または正規グループ会社）が九州（福岡, 佐賀, 長崎, 熊本, 大分, 宮崎, 鹿児島）に実在の直営拠点を持っているか調査してください。検索結果に九州の記載がない場合は「存在しない（is_found: false）」としてください。
    4. "reasoning" には、九州拠点の有無や閉業に関する事実のみを1〜2文でシンプルに述べてください。
    5. この企業へのDX営業アプローチで、相手が食いつきそうなフックキーワードを "sales_keywords" に10個抽出してください。

    必ず以下のJSONフォーマットのみで回答してください：
    {{
        "is_found": trueまたはfalse,
        "reasoning": "1〜2文の簡潔な判定理由",
        "details": [
            {{"name": "企業名・拠点名", "address": "住所・地域", "url": "URL"}}
        ],
        "sales_keywords": ["キーワード1", "キーワード2", "キーワード3", "キーワード4", "キーワード5", "キーワード6", "キーワード7", "キーワード8", "キーワード9", "キーワード10"]
    }}
    """
    
    response = client.models.generate_content(
        model='gemini-3.5-flash-lite',
        contents=prompt,
        config=types.GenerateContentConfig(
            response_mime_type="application/json"
        ),
    )
    
    return safe_parse_json(response.text.strip())

# ==========================================
# 5. Streamlit UI 構築
# ==========================================

default_query = ""
if st.session_state.search_history:
    selected_history = st.selectbox(
        "🕒 過去の検索履歴から選ぶ",
        ["-- 履歴から選択する --"] + st.session_state.search_history
    )
    if selected_history != "-- 履歴から選択する --":
        default_query = selected_history

with st.form(key="search_form"):
    query = st.text_input("会社名、住所等を入力", value=default_query, placeholder="例: さわやか株式会社")
    submit_button = st.form_submit_button("検索", type="primary")

if submit_button:
    if not query:
        st.warning("会社名、住所等を入力してください。")
    else:
        if query in st.session_state.search_history:
            st.session_state.search_history.remove(query)
        st.session_state.search_history.insert(0, query)
        if len(st.session_state.search_history) > 10:
            st.session_state.search_history.pop()

        if query in st.session_state.result_cache:
            st.info("⚡ キャッシュ（保存されたデータ）から高速表示しています（API消費ゼロ）")
            cached_data = st.session_state.result_cache[query]
            web_context = cached_data["web_context"]
            expanded_query = cached_data.get("expanded_query", "不明")
            result = cached_data["result"]
        else:
            gemini_key = os.getenv("GEMINI_API_KEY")
            if not gemini_key:
                st.error("⚠️ サーバーのVariablesにAPIキー（GEMINI_API_KEY）が設定されていません。")
                st.stop()

            with st.spinner(f"「{query}」の会社情報を検索中..."):
                expanded_query = expand_query_with_ai(query, gemini_key)
                # ★修正: フィルター用に元の入力キーワード(query)も関数に渡す
                web_context, err = search_ddg_lite(query, expanded_query)
                
                if err:
                    st.error(err)
                    st.stop()

            with st.spinner("分析中..."):
                try:
                    result = analyze_company_with_ai(query, web_context, gemini_key)
                    st.session_state.result_cache[query] = {
                        "web_context": web_context,
                        "expanded_query": expanded_query,
                        "result": result
                    }
                except Exception as e:
                    st.error(f"分析エラーが発生しました: {e}")
                    st.stop()

        # ==========================================
        # 結果の描画
        # ==========================================
        with st.expander("🔍 取得したWeb検索の生データ (デバッグ用)"):
            st.markdown(f"**実際に検索したクエリ:** `{expanded_query}`")
            st.text(web_context)
        
        st.divider()
        if result.get('is_found'):
            st.success(f"⭕ 九州拠点が確認されました。")
            st.info(f"**判定理由:** {result.get('reasoning')}")
            
            keywords = result.get('sales_keywords', [])
            if keywords:
                st.markdown("### 🔑 フックキーワード")
                keywords_md = " ".join([f"`{kw}`" for kw in keywords])
                st.markdown(keywords_md)
            
            st.markdown("### 📍 企業・拠点詳細")
            for d in result.get('details', []):
                with st.container(border=True):
                    st.markdown(f"**{d.get('name')}**")
                    st.write(f"住所: {d.get('address')}")
                    st.markdown(f"[詳細リンク]({d.get('url')})")
        else:
            st.error(f"❌ 九州拠点は確認されませんでした。")
            st.write(f"**判定理由:** {result.get('reasoning')}")
            
            keywords = result.get('sales_keywords', [])
            if keywords:
                st.markdown("### 🔑 フックキーワード")
                keywords_md = " ".join([f"`{kw}`" for kw in keywords])
                st.markdown(keywords_md)
