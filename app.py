import streamlit as st
import json
import os
import re
from google import genai
from google.genai import types
from duckduckgo_search import DDGS

st.set_page_config(page_title="九州拠点・DX営業リサーチ", page_icon="✨")

st.title("✨ 九州拠点・DX営業リサーチツール (gemini-3.5-flash)")

# 検索関数
def search_web_info(query):
    try:
        with DDGS() as ddg:
            results = [r for r in ddg.text(query, max_results=7)] # 検索範囲を拡大
            context = "\n".join([f"- {r.get('title')}: {r.get('body')}" for r in results])
            return context if context else None
    except Exception:
        return None

@st.cache_data(ttl=86400)
def analyze_company_with_search(query, gemini_key):
    web_context = search_web_info(query)
    
    # AIへの指示（強引に拾うように変更）
    client = genai.Client(api_key=gemini_key)
    prompt = f"""
    あなたは凄腕の営業リサーチ担当です。以下の【Web検索結果】から、可能な限り正確な情報を抽出してください。
    ターゲット: "{query}"

    【Web検索結果】
    {web_context if web_context else "検索情報なし"}

    指示:
    1. 【最優先】検索結果の中にターゲット企業（またはその拠点）に関連しそうな情報があれば、たとえ断片的であっても、積極的に "is_found": true と判定してください。
    2. 住所が入力された場合、その場所にある企業・施設を検索結果から推測して特定してください。
    3. 「確証がない」という理由で即座にfalseにせず、検索結果から読み取れる情報を元にベストエフォートで回答すること。
    4. 営業キーワード（10個）は、その企業の業界特性を推測して必ず作成すること。

    必ず以下のJSONのみで回答してください。
    {{
        "is_found": true,
        "reasoning": "Web検索結果に基づいた判定理由（推測を含む場合はその旨を記載）",
        "details": [{{"name": "企業名・拠点名", "address": "住所", "url": "URL"}}],
        "sales_keywords": ["kw1", "kw2", ... "kw10"]
    }}
    """
    
    # ユーザー指定のモデルを使用
    try:
        response = client.models.generate_content(
            model='gemini-3.5-flash', # 指定通り設定
            contents=prompt,
            config=types.GenerateContentConfig(response_mime_type="application/json"),
        )
        result = json.loads(response.text.strip())
    except:
        # モデル名が存在しない場合のフォールバック（保険）
        result = {"is_found": False, "reasoning": "モデル呼び出しエラー、または検索結果が空です。"}
        
    result["raw_context"] = web_context
    return result

# 入力フォーム
query = st.text_input("会社名、または住所を入力", placeholder="例: 株式会社ティーエフケー")

if st.button("リサーチを実行"):
    if not query:
        st.warning("入力してください")
    else:
        gemini_key = os.getenv("GEMINI_API_KEY")
        with st.spinner("分析中..."):
            result = analyze_company_with_search(query, gemini_key)
            
            # デバッグ表示
            with st.expander("🔍 検索エンジンの生データ（AIが何を読んでいるか）"):
                st.text(result.get("raw_context", "情報なし"))
            
            st.divider()
            if result.get('is_found'):
                st.success(f"⭕ 確認できました")
                st.info(f"**理由:** {result.get('reasoning')}")
                st.markdown("### 🔑 DX営業アプローチキーワード")
                st.write(", ".join(result.get('sales_keywords', [])))
                for d in result.get('details', []):
                    st.write(f"**{d.get('name')}** / {d.get('address')}")
            else:
                st.error(f"❌ 確認できませんでした")
                st.write(f"**理由:** {result.get('reasoning')}")
