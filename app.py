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
    1. 入力された法人格（前株・後株など）をそのままダブルクォーテーションで囲んで完全一致検索させること。
    2. 同名の別企業（例: 「株式会社ニデック」と「ニデック株式会社」など）と混同されないようなワードをバランスよく含めること。
    
    余計な挨拶や解説は省き、検索クエリの文字列（1行）のみを出力してください。
    """
    try:
        response = client.models.generate_content(
            model='gemini-3.5-flash-lite',
            contents=prompt,
        )
        return response.text.strip().replace('`', '')
    except:
        return f'"{keyword}" 会社概要 拠点 支店'

# ==========================================
# 2. DuckDuckGo Lite による検索関数（安定版）
# ==========================================
def search_ddg_lite(expanded_query: str):
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
            
        context = "\n".join([f"- タイトル: {r['title']}\n  内容: {r['snippet']}\n  URL: {r['url']}" for r in results[:8]])
        return context, None

    except Exception as e:
        return None, f"検索エラー: {str(e)}"

# ==========================================
# 3. JSONパースの安全装置付き・分析関数
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
    ユーザーが入力した正確な企業名・キーワード: "{query}"

    【重要：企業同一性の厳守】
    - 入力された法人名（前株・後株を含む）と一致する正しい企業情報を特定してください。
    - 例えば「株式会社ニデック」（愛知県蒲郡市の医療機器メーカー）と「ニデック株式会社」（旧・日本電産などの別企業）を完全に区別し、文字面や同名異社の巨大企業の情報と混同して判定理由を書かないこと。

    【取得したWeb検索結果】
    {web_context}

    指示:
    1. 検索結果から対象企業の「公式HP」のURLを特定し、"official_url" に格納（見つからない場合は null）。
    2. 九州（福岡, 佐賀, 長崎, 熊本, 大分, 宮崎, 鹿児島）に支店、営業所、工場などの直営拠点を持っているか調査し、存在する場合は "is_found": true としてください。
    3. "reasoning" には、入力された企業の正しい事業内容を踏まえて、九州拠点の有無に関する判定理由を1〜2文で簡潔に記述してください（別企業の情報を混入させないこと）。
    4. この企業への営業アプローチで有効なフックキーワードを "sales_keywords" に10個抽出してください。
    5. 拠点ごとの詳細情報（名前、住所、詳細URL）を "details" リストにまとめてください。

    必ず以下のJSONフォーマットのみで回答してください：
    {{
        "is_found": trueまたはfalse,
        "official_url": "公式サイトのURLまたはnull",
        "reasoning": "1〜2文の簡潔な判定理由（企業混同厳禁）",
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
# 4. Streamlit UI 構築
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
    query = st.text_input("会社名、キーワード等を入力", value=default_query, placeholder="例: 株式会社ニデック")
    submit_button = st.form_submit_button("検索", type="primary")

if submit_button:
    if not query:
        st.warning("会社名、キーワード等を入力してください。")
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

            with st.spinner(f"「{query}」の情報を検索中..."):
                expanded_query = expand_query_with_ai(query, gemini_key)
                web_context, err = search_ddg_lite(expanded_query)
                
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
        # 結果の描画（UI・公式HP・詳細情報）
        # ==========================================
        with st.expander("🔍 取得したWeb検索の生データ (デバッグ用)"):
            st.markdown(f"**実際に検索したクエリ:** `{expanded_query}`")
            st.text(web_context)
        
        st.divider()
        
        # 公式サイトの表示（常に最上部に綺麗に配置）
        official_url = result.get('official_url')
        if official_url and official_url != "null":
            st.markdown(f"### 🌐 公式サイト\n[{official_url}]({official_url})")
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
                    if d.get('url'):
                        st.markdown(f"[詳細リンク]({d.get('url')})")
        else:
            st.error(f"❌ 九州拠点は確認されませんでした。")
            st.write(f"**判定理由:** {result.get('reasoning')}")
            
            keywords = result.get('sales_keywords', [])
            if keywords:
                st.markdown("### 🔑 フックキーワード")
                keywords_md = " ".join([f"`{kw}`" for kw in keywords])
                st.markdown(keywords_md)
