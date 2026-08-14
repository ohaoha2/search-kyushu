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
# 1. 検索エンジンの実行関数（1回分）
# ==========================================
def fetch_ddg_results(query: str):
    clean_kw = query.strip().replace('`', '')
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
        return results
    except:
        return []

# ==========================================
# 2. マルチクエリによる網羅的検索（検索件数大幅増量）
# ==========================================
def search_multi_queries(keyword: str):
    # ① 全国の会社概要・拠点一覧を狙うクエリ
    q1 = f'"{keyword}" 会社概要 拠点 支店 一覧'
    # ② 九州・福岡の拠点・営業所をピンポイントで狙うクエリ（法人名完全一致なので別会社は拾いません）
    q2 = f'"{keyword}" 九州 福岡 支店 営業所'
    
    queries = [q1, q2]
    all_results = []
    seen_urls = set()
    
    for q in queries:
        res_list = fetch_ddg_results(q)
        for r in res_list:
            if r['url'] not in seen_urls:
                seen_urls.add(r['url'])
                all_results.append(r)
                
    if not all_results:
        return None, "検索結果を取得できませんでした。", queries
        
    # 最大25件まで結合してコンテキストを作成
    context = "\n".join([f"- タイトル: {r['title']}\n  内容: {r['snippet']}\n  URL: {r['url']}" for r in all_results[:25]])
    return context, None, queries

# ==========================================
# 3. JSONパース安全装置
# ==========================================
def safe_parse_json(text):
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        text = re.sub(r"```json|```", "", text).strip()
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if match: return json.loads(match.group(0))
        raise

# ==========================================
# 4. 分析関数
# ==========================================
def analyze_company_with_ai(query, web_context, gemini_key):
    client = genai.Client(api_key=gemini_key)
    prompt = f"""
    あなたは企業の所在調査のプロフェッショナルです。
    ユーザーが入力した正確な企業名: "{query}"

    【取得した大量のWeb検索結果（最大25件）】
    {web_context}

    指示:
    1. 検索結果から対象企業の「公式HP」のURLを特定し、"official_url" に格納（見つからない場合は null）。
    2. 入力された企業が九州地方（福岡, 佐賀, 長崎, 熊本, 大分, 宮崎, 鹿児島）に支店、営業所、工場、グループ拠点等の直営拠点を持っているかを徹底的に調査してください。少しでも九州における拠点や事業展開（福岡支店など）が確認できる場合は、必ず "is_found": true としてください。
    3. ただし、すでに閉業、閉鎖、廃止、移転完了している拠点は「存在しない（is_found: false）」と判定してください。
    4. "reasoning" には、確認できた九州の拠点名（例: 福岡支店など）を含めて簡潔な判定理由を記載してください。
    5. 営業アプローチで有効なフックキーワードを "sales_keywords" に10個抽出してください。
    6. 九州内の拠点ごとの詳細情報（名称、住所、詳細URL）を "details" リストに具体的にまとめてください。

    必ず以下のJSONフォーマットのみで回答してください：
    {{
        "is_found": trueまたはfalse,
        "official_url": "公式サイトのURLまたはnull",
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
        config=types.GenerateContentConfig(response_mime_type="application/json"),
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
    query = st.text_input("会社名、キーワード等を入力", value=default_query, placeholder="例: 株式会社〇〇")
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
            st.info("⚡ キャッシュから高速表示しています")
            cached_data = st.session_state.result_cache[query]
            web_context = cached_data["web_context"]
            used_queries = cached_data.get("used_queries", [])
            result = cached_data["result"]
        else:
            gemini_key = os.getenv("GEMINI_API_KEY")
            if not gemini_key:
                st.error("⚠️ サーバーのVariablesにAPIキー（GEMINI_API_KEY）が設定されていません。")
                st.stop()

            with st.spinner(f"「{query}」の情報を複数クエリで大量検索中..."):
                web_context, err, used_queries = search_multi_queries(query)
                
                if err:
                    st.error(err)
                    st.stop()

            with st.spinner("分析中..."):
                try:
                    result = analyze_company_with_ai(query, web_context, gemini_key)
                    st.session_state.result_cache[query] = {
                        "web_context": web_context,
                        "used_queries": used_queries,
                        "result": result
                    }
                except Exception as e:
                    st.error(f"分析エラーが発生しました: {e}")
                    st.stop()

        with st.expander("🔍 取得したWeb検索の生データ"):
            st.markdown(f"**実際に実行した検索クエリ:**\n- `{used_queries[0]}`\n- `{used_queries[1]}`")
            st.text(web_context)
        
        st.divider()
        
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
コレをベースに、１度に複数の企業について、公式HP、拠点、フックキーワードを検索し、一覧表示したい
インプットは、スプシから貼り付けたい
