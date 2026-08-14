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
# 0. セッションステート初期化
# ==========================================
if "search_history" not in st.session_state:
    st.session_state.search_history = []
if "result_cache" not in st.session_state:
    st.session_state.result_cache = {}

# ==========================================
# 1. クエリ展開（厳格モード）
# ==========================================
def expand_query_with_ai(keyword: str, gemini_key):
    client = genai.Client(api_key=gemini_key)
    prompt = f"""
    ユーザーが入力したキーワード: "{keyword}"
    
    指示:
    この企業の【全国の拠点・事業所・店舗一覧、または会社概要】をWeb検索するためのクエリを作成してください。
    
    絶対条件:
    1. 【表記の完全尊重】ユーザーが入力した「株式会社〇〇」あるいは「〇〇株式会社」という表記をそのまま保持してください。AIの判断で勝手に表記を変えたり、統一したりしないでください。
    2. 地域名（九州・福岡など）は検索ワードに含めないでください。
    3. キーワードに「会社概要 拠点 支店」という一般的キーワードを付与してください。
    
    出力例:
    「株式会社ニデック」→ `"株式会社ニデック" 会社概要 拠点 支店`
    「ニデック株式会社」→ `"ニデック株式会社" 会社概要 拠点 支店`
    
    検索クエリ文字列（1行）のみを出力してください。
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
# 2. 検索関数
# ==========================================
def search_ddg_lite(expanded_query: str):
    clean_kw = expanded_query.strip().replace('`', '')
    url = "https://lite.duckduckgo.com/lite/"
    data = {'q': clean_kw}
    headers = {"User-Agent": "Mozilla/5.0"}

    try:
        res = requests.post(url, data=data, headers=headers, timeout=10)
        soup = BeautifulSoup(res.text, "html.parser")
        results = []
        rows = soup.find_all("tr")
        current_title = ""; current_url = ""
        for row in rows:
            link_tag = row.find("a", class_="result-link")
            if link_tag:
                current_title = link_tag.text.strip(); current_url = link_tag["href"]
            snippet_tag = row.find("td", class_="result-snippet")
            if snippet_tag:
                current_snippet = snippet_tag.text.strip()
                if current_title and current_url:
                    results.append({'title': current_title, 'url': current_url, 'snippet': current_snippet})
                    current_title = ""; current_url = ""
        
        if not results: return None, "検索結果を取得できませんでした。"
        context = "\n".join([f"- タイトル: {r['title']}\n  内容: {r['snippet']}\n  URL: {r['url']}" for r in results[:6]])
        return context, None
    except Exception as e:
        return None, f"検索エラー: {str(e)}"

# ==========================================
# 3. 分析関数（厳格チェックモード）
# ==========================================
def analyze_company_with_ai(query, web_context, gemini_key):
    client = genai.Client(api_key=gemini_key)
    prompt = f"""
    あなたは企業の所在調査のプロです。
    ユーザーが入力した検索ターゲット名: "{query}"

    【Web検索結果】
    {web_context}

    指示:
    1. 【最重要：表記の厳格一致】Web検索結果の中に、ユーザーが入力した「{query}」と**完全に一致する企業名**があるかを確認してください。
       - 「株式会社ニデック」を検索しているなら「ニデック株式会社」は、**全く別の法人**として扱い、検索結果から除外（無視）してください。
       - 類似した名前だけの別会社は、すべて除外してください。
    2. その上で、一致する企業が九州に拠点を持っているか調査してください。
    3. JSONで回答してください。
    
    {{
        "is_found": trueまたはfalse,
        "official_url": "公式サイトのURLまたはnull",
        "reasoning": "表記の厳密な一致判定を含めた判定理由",
        "details": [{{"name": "企業名・拠点名", "address": "住所", "url": "URL"}}],
        "sales_keywords": ["キーワード1", "キーワード2", "キーワード3", "キーワード4", "キーワード5", "キーワード6", "キーワード7", "キーワード8", "キーワード9", "キーワード10"]
    }}
    """
    
    response = client.models.generate_content(
        model='gemini-3.5-flash-lite',
        contents=prompt,
        config=types.GenerateContentConfig(response_mime_type="application/json"),
    )
    
    # JSONパース（安全装置付き）
    text = response.text.strip()
    text = re.sub(r"```json", "", text).replace("```", "").strip()
    return json.loads(text)

# ==========================================
# 4. Streamlit UI
# ==========================================
query = st.text_input("会社名を入力 (前株・後株を正確に入力してください)", placeholder="例: 株式会社ニデック")

if st.button("検索"):
    if not query: st.warning("入力してください")
    else:
        with st.spinner("検索・厳密照合中..."):
            gemini_key = os.getenv("GEMINI_API_KEY")
            expanded_query = expand_query_with_ai(query, gemini_key)
            web_context, err = search_ddg_lite(expanded_query)
            
            if err: st.error(err)
            else:
                result = analyze_company_with_ai(query, web_context, gemini_key)
                
                if result.get('is_found'):
                    st.success("⭕ 入力した企業名と一致し、かつ九州拠点が確認されました。")
                else:
                    st.error("❌ 入力した企業名と一致する九州拠点は見つかりませんでした。")
                    st.write("(注: 表記が少しでも異なると別法人として除外される設定になっています)")
                
                # 結果表示部分は以前のコードと同様
                if result.get('official_url'): st.write(f"🌐 {result['official_url']}")
                st.info(result.get('reasoning'))
