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
# 0. 初期化
# ==========================================
if "search_history" not in st.session_state:
    st.session_state.search_history = []
if "result_cache" not in st.session_state:
    st.session_state.result_cache = {}

# ==========================================
# 1. クエリ生成
# ==========================================
def expand_query_with_ai(keyword: str, gemini_key):
    client = genai.Client(api_key=gemini_key)
    # クエリに「拠点」「店舗」などを必ず含めるよう強制
    prompt = f'ユーザー入力: "{keyword}"\n\nこの企業の拠点一覧や会社概要を検索するためのクエリを1行で作成してください。\n絶対条件: "{keyword}" という表記をダブルクォーテーションで囲んで完全一致検索させること。'
    try:
        response = client.models.generate_content(model='gemini-3.5-flash-lite', contents=prompt)
        return response.text.strip().replace('"', '')
    except:
        return f'"{keyword}" 拠点 支店 営業所'

# ==========================================
# 2. 検索関数
# ==========================================
def search_ddg_lite(query: str):
    url = "https://lite.duckduckgo.com/lite/"
    data = {'q': query}
    headers = {"User-Agent": "Mozilla/5.0"}
    try:
        res = requests.post(url, data=data, headers=headers, timeout=10)
        soup = BeautifulSoup(res.text, "html.parser")
        results = []
        for row in soup.find_all("tr"):
            link = row.find("a", class_="result-link")
            snippet = row.find("td", class_="result-snippet")
            if link and snippet:
                results.append({'title': link.text.strip(), 'url': link['href'], 'snippet': snippet.text.strip()})
        return "\n".join([f"- {r['title']}: {r['snippet']} ({r['url']})" for r in results[:12]]), None
    except Exception as e:
        return None, str(e)

# ==========================================
# 3. 分析関数
# ==========================================
def analyze_company_with_ai(query, web_context, gemini_key):
    client = genai.Client(api_key=gemini_key)
    prompt = f"""
    あなたは企業の所在調査のプロです。ターゲット: "{query}"

    【Web検索結果】
    {web_context}

    指示:
    1. 公式サイトのURLを特定してください。見つからない場合はnull。
    2. 九州拠点（福岡, 佐賀, 長崎, 熊本, 大分, 宮崎, 鹿児島）の有無を判定。
       ※Web検索結果の抜粋に記載がなくても、企業名と公式サイトのURLが一致していれば、拠点が存在する可能性を考慮して分析してください。
    3. 【厳格ルール】入力された法人格（株式会社ニデックとニデック株式会社など）が異なる場合は別会社として扱ってください。
    
    必ず以下のJSONで回答:
    {{
        "is_found": true/false,
        "official_url": "URL",
        "reasoning": "判定理由",
        "details": [{{"name": "拠点名", "address": "住所", "url": "URL"}}],
        "sales_keywords": ["kw1", "kw2", "kw3"]
    }}
    """
    response = client.models.generate_content(
        model='gemini-3.5-flash-lite',
        contents=prompt,
        config=types.GenerateContentConfig(response_mime_type="application/json"),
    )
    return json.loads(re.sub(r"```json|```", "", response.text).strip())

# ==========================================
# 4. UI 構築
# ==========================================
query = st.text_input("会社名を入力 (例: 株式会社ニデック)", placeholder="株式会社ニデック")

if st.button("検索"):
    gemini_key = os.getenv("GEMINI_API_KEY")
    with st.spinner("検索中..."):
        expanded = expand_query_with_ai(query, gemini_key)
        context, err = search_ddg_lite(expanded)
        
        if err: st.error(err)
        else:
            result = analyze_company_with_ai(query, context, gemini_key)
            
            # 1. 公式サイトは常に表示
            if result.get('official_url'):
                st.markdown(f"### 🌐 公式サイト\n{result['official_url']}")
            
            st.divider()

            # 2. 判定結果
            if result.get('is_found'):
                st.success("⭕ 九州拠点が確認されました")
            else:
                st.error("❌ 九州拠点は確認されませんでした")
            
            st.info(f"**判定理由:** {result.get('reasoning')}")

            # 3. キーワード
            if result.get('sales_keywords'):
                st.markdown("### 🔑 フックキーワード")
                st.write(" ".join([f"`{kw}`" for kw in result['sales_keywords']]))

            # 4. 詳細
            if result.get('details'):
                st.markdown("### 📍 拠点詳細")
                for d in result['details']:
                    with st.container(border=True):
                        st.markdown(f"**{d.get('name')}**")
                        st.write(d.get('address'))
