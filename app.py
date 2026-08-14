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
# 1. キーワード補正
# ==========================================
def expand_query_with_ai(keyword: str, gemini_key):
    client = genai.Client(api_key=gemini_key)
    prompt = f"""
    ユーザーが入力したキーワード: "{keyword}"
    この企業の【全国の拠点・事業所・店舗一覧、または会社概要】を正しくヒットさせるための、最適なWeb検索クエリを作成してください。
    条件:
    1. 地域名（九州・福岡など）は含めない。
    2. 法人格（株式会社など）の表記は変えず、ダブルクォーテーションで囲む。
    3. 「会社概要 拠点 店舗」などのワードを付与する。
    """
    try:
        response = client.models.generate_content(model='gemini-3.5-flash-lite', contents=prompt)
        return response.text.strip()
    except:
        return f'"{keyword}" 会社概要 拠点'

# ==========================================
# 2. 前株・後株の物理フィルター
# ==========================================
def filter_strict_corporate_type(query: str, results: list):
    if not any(x in query for x in ["株式会社", "有限会社", "合同会社"]):
        return results
    filtered = []
    for r in results:
        text = (r['title'] + " " + r['snippet']).replace(" ", "")
        q_clean = query.replace(" ", "")
        
        # ユーザーが「後株」なら「前株」のパターンは弾く
        if query.endswith("株式会社"):
            core = query.replace("株式会社", "").strip()
            if f"株式会社{core}" in text and q_clean not in text: continue
        # ユーザーが「前株」なら「後株」のパターンは弾く
        elif query.startswith("株式会社"):
            core = query.replace("株式会社", "").strip()
            if f"{core}株式会社" in text and q_clean not in text: continue
        filtered.append(r)
    return filtered

# ==========================================
# 3. 検索関数
# ==========================================
def search_ddg_lite(original_query: str, expanded_query: str):
    clean_kw = expanded_query.strip().replace('`', '')
    url = "https://lite.duckduckgo.com/lite/"
    data = {'q': clean_kw}
    headers = {"User-Agent": "Mozilla/5.0"}

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
                    results.append({'title': current_title, 'url': current_url, 'snippet': current_snippet})
                    current_title = ""; current_url = ""

        if not results: return None, "検索結果を取得できませんでした。"
        
        filtered_results = filter_strict_corporate_type(original_query, results)
        if not filtered_results: return "【システム通知】入力された前株・後株に完全一致する企業の検索結果が見つかりませんでした。", None
            
        context = "\n".join([f"- タイトル: {r['title']}\n  内容: {r['snippet']}\n  URL: {r['url']}" for r in filtered_results[:6]])
        return context, None
    except Exception as e:
        return None, f"検索エラー: {str(e)}"

# ==========================================
# 4. 分析関数（公式URL抽出を追加）
# ==========================================
def analyze_company_with_ai(query, web_context, gemini_key):
    client = genai.Client(api_key=gemini_key)
    prompt = f"""
    あなたは企業の所在調査のプロです。
    ターゲット: "{query}"
    検索結果: {web_context}
    
    指示:
    1. 検索結果から「企業公式サイト(Official HP)」のURLを特定してください。
    2. 九州に実在の拠点があるか調査してください。
    3. JSONフォーマットで回答してください。
    {{
        "is_found": true/false,
        "official_url": "公式サイトのURL、見つからなければ null",
        "reasoning": "判定理由",
        "details": [{{"name": "拠点名", "address": "住所", "url": "詳細URL"}}],
        "sales_keywords": ["..."]
    }}
    """
    response = client.models.generate_content(
        model='gemini-3.5-flash-lite',
        contents=prompt,
        config=types.GenerateContentConfig(response_mime_type="application/json"),
    )
    return json.loads(response.text.strip())

# ==========================================
# 5. UI構築
# ==========================================
# (前略：UI部分は以前のコードとほぼ同じ)
query = st.text_input("会社名を入力")
if st.button("検索"):
    # 検索・分析プロセスを実行
    # ...
    # 結果の表示部分
    if result.get('is_found'):
        st.success("⭕ 九州拠点が確認されました。")
        
        # ★公式HPの表示★
        if result.get('official_url'):
            st.markdown("### 🌐 公式サイト")
            st.write(result.get('official_url'))
            
        st.info(f"**判定理由:** {result.get('reasoning')}")
        # (以下略)
