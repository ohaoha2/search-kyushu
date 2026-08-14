import streamlit as st
import json
import os
import re
import requests
from bs4 import BeautifulSoup
from google import genai
from google.genai import types

st.set_page_config(page_title="九州拠点リサーチ", page_icon="✨")

st.title("九州拠点リサーチツール")

# ==========================================
# 0. セッションステート（履歴）の初期化
# ==========================================
if "search_history" not in st.session_state:
    st.session_state.search_history = []

# ==========================================
# 1. DuckDuckGo Lite による検索関数
# ==========================================
def search_ddg_lite(keyword: str):
    clean_kw = re.sub(r'[・"（）()]', ' ', keyword).strip()
    if clean_kw.replace("-", "").isdigit():
        query = f"{clean_kw} 住所"
    else:
        # ダブルクォーテーションで囲んで類似企業への誤爆を防ぐ
        query = f'"{clean_kw}" 九州 福岡 拠点 工場 支社'

    url = "https://lite.duckduckgo.com/lite/"
    data = {'q': query}
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
            
        context = "\n".join([f"- タイトル: {r['title']}\n  内容: {r['snippet']}\n  URL: {r['url']}" for r in results[:5]])
        return context, None

    except Exception as e:
        return None, f"検索エラー: {str(e)}"

# ==========================================
# 2. JSONパースの安全装置付き・分析関数
# ==========================================
def safe_parse_json(text):
    """Geminiの出力から確実にJSON部分だけを抜き出してパースする関数"""
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
    検索ターゲット（会社名、または電話番号）: "{query}"

    【取得したWeb検索結果】
    {web_context}

    指示:
    1. 入力された検索ターゲット（"{query}"）と一致する正式な企業を対象としてください。文字が部分的に似ているだけの「まったく別の企業・別法人」とは絶対に混同しないでください。
    2. その企業が九州（福岡, 佐賀, 長崎, 熊本, 大分, 宮崎, 鹿児島）に実在の直営拠点（支店、営業所、工場など）を持っているか調査してください。
    3. 確証がある場合は "is_found": true とし、企業名・拠点名、正確な住所や地域、URLを抽出してください。
    4. "reasoning" は、利用者が混乱するような細かい社名の違いのツッコミや別企業である旨の解説は避け、シンプルに九州拠点の有無や状況を1〜2文で簡潔にまとめてください。
    5. この企業へのDX営業代行アプローチで、相手が食いつきそうなフックキーワード（10個）を "sales_keywords" の配列として抽出してください。

    必ず以下のJSONフォーマットのみで回答してください（Markdownのバッククォートなどは一切使わず、純粋なJSON文字列だけで出力してください）。
    {{
        "is_found": true,
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
# 3. Streamlit UI 構築（履歴機能 ＆ フォーム）
# ==========================================

# 履歴がある場合は、セレクトボックスで選べるようにする
default_query = ""
if st.session_state.search_history:
    selected_history = st.selectbox(
        "🕒 過去の検索履歴から選ぶ",
        ["-- 履歴から選択する --"] + st.session_state.search_history
    )
    if selected_history != "-- 履歴から選択する --":
        default_query = selected_history

with st.form(key="search_form"):
    # 履歴から選ばれていればそれがテキストボックスの初期値になる
    query = st.text_input("会社名、住所等を入力", value=default_query, placeholder="例: 〇〇株式会社")
    submit_button = st.form_submit_button("検索", type="primary")

if submit_button:
    if not query:
        st.warning("会社名、住所等を入力してください。")
    else:
        # 履歴への追加処理（重複を避け、最新を先頭にする。最大10件まで）
        if query in st.session_state.search_history:
            st.session_state.search_history.remove(query)
        st.session_state.search_history.insert(0, query)
        if len(st.session_state.search_history) > 10:
            st.session_state.search_history.pop()

        gemini_key = os.getenv("GEMINI_API_KEY")
        
        if not gemini_key:
            st.error("⚠️ サーバーのVariablesにAPIキー（GEMINI_API_KEY）が設定されていません。")
            st.stop()

        with st.spinner(f"「{query}」を検索中..."):
            web_context, err = search_ddg_lite(query)
            
            if err:
                st.error(err)
            else:
                with st.expander("🔍 取得したWeb検索の生データ"):
                    st.text(web_context)
                
                with st.spinner("分析中..."):
                    try:
                        result = analyze_company_with_ai(query, web_context, gemini_key)
                        
                        st.divider()
                        if result.get('is_found'):
                            st.success(f"⭕ 九州拠点が確認されました。")
                            st.info(f"**判定理由:** {result.get('reasoning')}")
                            
                            keywords = result.get('sales_keywords', [])
                            if keywords:
                                st.markdown("### 🔑 DX営業アプローチキーワード")
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
                                
                    except Exception as e:
                        st.error(f"分析エラーが発生しました: {e}")
