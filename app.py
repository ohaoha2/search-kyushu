import streamlit as st
import json
import os
import re
from google import genai
from google.genai import types
from duckduckgo_search import DDGS

st.set_page_config(page_title="九州拠点・DX営業リサーチ", page_icon="✨")

st.title("✨ 九州拠点・DX営業リサーチツール")

# 検索関数（リージョンを日本に固定）
def search_web_info(query):
    try:
        with DDGS() as ddg:
            # 「会社」「企業」などの検索補助ワードを自動追加し、日本リージョンで検索
            search_query = f"{query} 会社 企業"
            results = [r for r in ddg.text(search_query, region='jp-jp', safesearch='off', max_results=7)]
            context = "\n".join([f"- {r.get('title')}: {r.get('body')}" for r in results])
            return context if context else None
    except Exception as e:
        return f"検索エラー: {e}"

@st.cache_data(ttl=86400)
def analyze_company_with_search(query, gemini_key):
    web_context = search_web_info(query)
    if not web_context:
        return {"is_found": False, "reasoning": "Web検索結果が得られませんでした。", "details": [], "sales_keywords": []}

    client = genai.Client(api_key=gemini_key)
    
    # 指示を日本語環境である前提で強化
    prompt = f"""
    あなたは日本の企業の所在調査のプロです。以下の【Web検索結果】は日本語の検索結果です。
    ターゲット: "{query}"

    【Web検索結果】
    {web_context}

    指示:
    1. 検索結果は日本語です。入力された企業名や住所に基づき、正確な法人名や拠点を特定してください。
    2. 無関係な英語の検索結果や自動車販売などは無視し、日本国内の企業情報に集中してください。
    3. 「確証がない」場合でも、検索結果にヒントがあれば "is_found": true とし、最も可能性の高い情報を出力してください。
    4. 営業キーワード（10個）は、この企業の業種・事業内容から推測して作成してください。

    必ず以下のJSONのみで回答してください。
    {{
        "is_found": true,
        "reasoning": "検索結果から特定した企業名と拠点状況",
        "details": [{{"name": "企業名・拠点名", "address": "住所", "url": "URL"}}],
        "sales_keywords": ["kw1", "kw2", ... "kw10"]
    }}
    """
    
    try:
        # 指定のモデル名を使用（存在しない場合はエラーになるので注意）
        response = client.models.generate_content(
            model='gemini-3.5-flash', 
            contents=prompt,
            config=types.GenerateContentConfig(response_mime_type="application/json"),
        )
        result = json.loads(response.text.strip())
    except Exception as e:
        result = {"is_found": False, "reasoning": f"AI分析エラー: {str(e)}"}
        
    result["raw_context"] = web_context
    return result

# 入力フォーム
query = st.text_input("会社名、または住所を入力", placeholder="例: 株式会社ティーエフケー")

if st.button("リサーチを実行"):
    if not query:
        st.warning("入力してください")
    else:
        gemini_key = os.getenv("GEMINI_API_KEY")
        with st.spinner("日本国内の情報を検索・分析中..."):
            result = analyze_company_with_search(query, gemini_key)
            
            with st.expander("🔍 検索エンジンから返ってきた生データ（これを見て企業情報があればAIが正解を出します）"):
                st.text(result.get("raw_context", "情報なし"))
            
            st.divider()
            if result.get('is_found'):
                st.success(f"⭕ 確認できました")
                st.info(f"**判定:** {result.get('reasoning')}")
                st.markdown("### 🔑 DX営業アプローチキーワード")
                st.write(", ".join(result.get('sales_keywords', [])))
                for d in result.get('details', []):
                    st.write(f"**{d.get('name')}** / {d.get('address')}")
            else:
                st.error(f"❌ 確認できませんでした")
                st.write(f"**理由:** {result.get('reasoning')}")
