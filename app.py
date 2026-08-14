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
    
    このキーワードが指す「世間一般で最も有名な正式企業・ブランド」が、【九州地方（福岡など）に支店・営業所・工場などの拠点を持っているか】を調べるための、最適なWeb検索クエリ（スペース区切り）を作成してください。
    
    条件:
    1. 同名異法人（無関係な別会社）を避けるため、業種や旧社名などの固有名称を適度に含めること。
    2. 本社がある都道府県名（東京、静岡、京都など）は絶対に入れないこと（九州の拠点がヒットしづらくなるため）。
    3. 検索ワードの後半に「九州 福岡 拠点 支社 工場」などを付与すること。
    
    例:
    「さわやか」→「炭焼きレストランさわやか 九州 福岡 拠点 支店」
    「ニデック」→「ニデック 旧日本電産 九州 拠点 支社 工場」
    
    余計な挨拶や解説は省き、検索クエリの文字列（1行）のみを出力してください。
    """
    try:
        response = client.models.generate_content(
            model='gemini-3.5-flash-lite',
            contents=prompt,
        )
        return response.text.strip()
    except:
        return f"{keyword} 九州 拠点 支社 工場"

# ==========================================
# 2. DuckDuckGo Lite による検索関数
# ==========================================
def search_ddg_lite(expanded_query: str):
    # AIが作った最適な検索クエリをそのまま使用する
    clean_kw = re.sub(r'["（）()]', ' ', expanded_query).strip()
    
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
            
        # 検索結果の上位6件をコンテキストとして返す
        context = "\n".join([f"- タイトル: {r['title']}\n  内容: {r['snippet']}\n  URL: {r['url']}" for r in results[:6]])
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
        text = re.sub(r"
