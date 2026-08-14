import streamlit as st
import json
import os
import re
from google import genai
from google.genai import types

st.set_page_config(page_title="九州拠点リサーチ", page_icon="✨")

st.title("✨ 九州拠点リサーチツール")
st.write("会社名または住所からWeb検索で正確に特定し、アプローチキーワードを抽出します。")

@st.cache_data(ttl=86400)
def analyze_company(query, gemini_key):
    client = genai.Client(api_key=gemini_key)
    
    prompt = f"""
    あなたは企業の所在調査およびDX営業戦略のプロフェッショナルです。
    検索ターゲット（会社名、または住所）: "{query}"
    
    指示:
    1. 必ずWeb上の最新情報を検索し、入力された情報（会社名、または住所）に該当する「正確な企業名や施設名」を突き止めてください。
    2. 住所や会社名に紐づく実際の事業者を正確に特定してください。
    3. その企業が九州（福岡, 佐賀, 長崎, 熊本, 大分, 宮崎, 鹿児島）に実在の直営拠点を持っているかも調査してください。
    4. 確実な証拠がある場合のみ "is_found": true とし、企業名・拠点名、正確な住所、URLを抽出してください。
    5. 確証がない場合は "is_found": false にしてください。
    6. "reasoning" は1〜2文で簡潔にまとめてください。
    7. この企業へのDX営業代行アプローチで使えそうなキーワードや業界特性（10個程度）を "sales_keywords" の配列として抽出してください。
    
    必ず以下のJSONフォーマットのみで回答してください（Markdownのバッククォート ``` は使ず、純粋なJSON文字列だけで出力してください）。
    {{
        "is_found": true,
        "reasoning": "1〜2文の簡潔な判定理由",
        "details": [
            {{"name": "企業名・拠点名", "address": "住所", "url": "URL"}}
        ],
        "sales_keywords": ["キーワード1", "キーワード2", "キーワード3", "キーワード4", "キーワード5", "キーワード6", "キーワード7", "キーワード8", "キーワード9", "キーワード10"]
    }}
    """
    
    # Google検索（Grounding）を有効化し、実際のWebから正確な情報を引く
    response = client.models.generate_content(
        model='gemini-2.5-flash',
        contents=prompt,
        config=types.GenerateContentConfig(
            tools=[{'google_search': {}}],
            response_mime_type="application/json"
        ),
    )
    
    raw_text = response.text.strip()
    if raw_text.startswith("```"):
        raw_text = re.sub(r"^```(?:json)?\n", "", raw_text)
        raw_text = re.sub(r"\n```$", "", raw_text)
        
    return json.loads(raw_text.strip())

# 入力フォーム
query = st.text_input("会社名、または住所を入力", placeholder="例: 株式会社ティーエフケー、静岡県袋井市宇刈137")

if st.button("リサーチを実行", type="primary"):
    if not query:
        st.warning("会社名または住所を入力してください。")
    else:
        gemini_key = os.getenv("GEMINI_API_KEY")
        
        if not gemini_key:
            st.error("⚠️ サーバーのVariablesにAPIキー（GEMINI_API_KEY）が設定されていません。")
            st.stop()

        with st.spinner(f"「{query}」をWeb検索と照合して正確に分析中..."):
            try:
                result = analyze_company(query, gemini_key)
                
                # 結果表示
                st.divider()
                if result.get('is_found'):
                    st.success(f"⭕ 該当する企業・拠点が正確に確認されました！")
                    st.info(f"**判定理由:** {result.get('reasoning')}")
                    
                    # 営業キーワードの表示（約10個）
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
                    st.error(f"❌ 正確な情報が確認されませんでした。")
                    st.write(f"**判定理由:** {result.get('reasoning')}")
                    
                    keywords = result.get('sales_keywords', [])
                    if keywords:
                        st.markdown("### 🔑 DX営業アプローチキーワード（参考）")
                        keywords_md = " ".join([f"`{kw}`" for kw in keywords])
                        st.markdown(keywords_md)
                        
            except Exception as e:
                st.error(f"エラーが発生しました: {e}")
