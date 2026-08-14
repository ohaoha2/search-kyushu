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
    
    この企業の【全国の拠点・事業所・店舗一覧、または会社概要】を検索するためのクエリを作成してください。
    
    条件:
    1. ユーザーの入力文字列（法人格を含む）をそのままダブルクォーテーションで囲んで検索ワードに含めること。
    2. 地域名（九州・福岡など）は含めないこと。
    3. 「会社概要 拠点 支店」などの一般的な言葉を付与すること。
    
    出力例:
    「株式会社ニデック」→ `"株式会社ニデック" 会社概要 拠点 支店`
    「ニデック株式会社」→ `"ニデック株式会社" 会社概要 拠点 支店`
    
    検索クエリの文字列（1行）のみを出力してください。
    """
    try:
        response = client.models.generate_content(
            model='gemini-3.5-flash-lite',
            contents=prompt,
        )
        return response.text.strip()
    except:
        return f'"{keyword}" 会社概要 拠点 支店'

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
# 3. Pythonによる物理バリデーション（前株・後株の厳格チェック）
# ==========================================
def validate_corporate_match(user_query: str, result: dict):
    """
    ユーザーが入力した法人格の形式（前株か後株か）と、
    AIが取得した結果の企業名が一致しているかをPython側で強制チェックし、
    異なる場合は問答無用で非該当（is_found = False）に書き換える
    """
    cleaned_query = user_query.replace(" ", "")
    
    # ユーザーが「前株（株式会社〇〇）」で入力した場合
    if cleaned_query.startswith("株式会社"):
        core_name = cleaned_query.replace("株式会社", "")
        wrong_pattern = f"{core_name}株式会社" # 後株
        
        # details内の企業名をチェック
        valid_details = []
        for d in result.get('details', []):
            name = d.get('name', '').replace(" ", "")
            # 後株のパターンが含まれていて、かつ前株の正しい名前が含まれていない場合は除外
            if wrong_pattern in name and cleaned_query not in name:
                continue
            valid_details.append(d)
            
        result['details'] = valid_details
        
        # 推論テキストや詳細に後株の誤認が含まれている場合もブロック
        reasoning = result.get('reasoning', '')
        if wrong_pattern in reasoning and cleaned_query not in reasoning:
            result['is_found'] = False
            result['reasoning'] = f"入力された前株形式（{user_query}）と一致する企業情報が確認できなかったため除外しました。"
            result['details'] = []

    # ユーザーが「後株（〇〇株式会社）」で入力した場合
    elif cleaned_query.endswith("株式会社"):
        core_name = cleaned_query.replace("株式会社", "")
        wrong_pattern = f"株式会社{core_name}" # 前株
        
        valid_details = []
        for d in result.get('details', []):
            name = d.get('name', '').replace(" ", "")
            if wrong_pattern in name and cleaned_query not in name:
                continue
            valid_details.append(d)
            
        result['details'] = valid_details
        
        reasoning = result.get('reasoning', '')
        if wrong_pattern in reasoning and cleaned_query not in reasoning:
            result['is_found'] = False
            result['reasoning'] = f"入力された後株形式（{user_query}）と一致する企業情報が確認できなかったため除外しました。"
            result['details'] = []

    return result

# ==========================================
# 4. 分析関数
# ==========================================
def analyze_company_with_ai(query, web_context, gemini_key):
    client = genai.Client(api_key=gemini_key)
    prompt = f"""
    あなたは企業の所在調査のプロです。
    ユーザーが入力した正確なターゲット名: "{query}"

    【Web検索結果】
    {web_context}

    指示:
    1. 検索結果から「企業公式サイト(Official HP)」のURLを特定し、"official_url" に格納してください（見つからない場合は null）。
    2. 【最重要：法人格の厳格一致】ユーザーが入力した法人格の形（前株か後株か）を完全に一致させてください。例えば「株式会社ニデック」と入力された場合、「ニデック株式会社」は別法人として扱ってください。
    3. 入力された企業名と完全に一致する企業が、九州に直営拠点を持っているか調査してください。
    4. 必ず以下のJSONフォーマットのみで回答してください。
    
    {{
        "is_found": trueまたはfalse,
        "official_url": "公式サイトのURLまたはnull",
        "reasoning": "判定理由",
        "details": [{{"name": "企業名・拠点名", "address": "住所", "url": "URL"}}],
        "sales_keywords": ["キーワード1", "キーワード2", "キーワード3", "キーワード4", "キーワード5", "キーワード6", "キーワード7", "キーワード8", "キーワード9", "キーワード10"]
    }}
    """
    
    response = client.models.generate_content(
        model='gemini-3.5-flash',
        contents=prompt,
        config=types.GenerateContentConfig(response_mime_type="application/json"),
    )
    
    text = response.text.strip()
    text = re.sub(r"```json", "", text).replace("```", "").strip()
    raw_result = json.loads(text)
    
    # ★Pythonによる物理バリデーションを通す★
    return validate_corporate_match(query, raw_result)

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
    query = st.text_input("会社名を入力 (前株・後株を正確に区別して入力)", value=default_query, placeholder="例: 株式会社ニデック")
    submit_button = st.form_submit_button("検索", type="primary")

if submit_button:
    if not query:
        st.warning("会社名を入力してください。")
    else:
        if query in st.session_state.search_history:
            st.session_state.search_history.remove(query)
        st.session_state.search_history.insert(0, query)
        if len(st.session_state.search_history) > 10:
            st.session_state.search_history.pop()

        if query in st.session_state.result_cache:
            st.info("⚡ キャッシュから高速表示しています（API消費ゼロ）")
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

        with st.expander("🔍 取得したWeb検索の生データ (デバッグ用)"):
            st.markdown(f"**実際に検索したクエリ:** `{expanded_query}`")
            st.text(web_context)
        
        st.divider()
        
        official_url = result.get('official_url')
        if official_url and official_url != "null":
            st.markdown(f"### 🌐 公式サイト\n[{official_url}]({official_url})")

        if result.get('is_found'):
            st.success(f"⭕ 入力された法人名と一致し、九州拠点が確認されました。")
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
            st.error(f"❌ 入力された法人名に完全一致する九州拠点は確認されませんでした。")
            st.write(f"**判定理由:** {result.get('reasoning')}")
            
            keywords = result.get('sales_keywords', [])
            if keywords:
                st.markdown("### 🔑 フックキーワード")
                keywords_md = " ".join([f"`{kw}`" for kw in keywords])
                st.markdown(keywords_md)
