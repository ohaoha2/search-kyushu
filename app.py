import streamlit as st
import json
import os
import re
from google import genai
from google.genai import types

st.set_page_config(page_title="九州DX営業リサーチツール", page_icon="✨")

st.title("九州DX営業リサーチツール")
st.write("企業の九州拠点の有無と、アプローチキーワードを抽出します。")

@st.cache_data(ttl=86400)
def analyze_company(company_name, gemini_key):
    client = genai.Client(api_key=gemini_key)
    
    prompt = f"""
    あなたはBtoBのDX営業戦略のプロフェッショナルです。
    ターゲット企業: "{company_name}"
    
    1. この企業が九州（福岡,佐賀,長崎,熊本,大分,宮崎,鹿児島）に実在の直営拠点（支店、営業所、工場、事業所など）を持つか調査してください（同名異会社は除外）。
    2. 彼女は「DXの営業代行」を行っています。九州拠点がローカルで独自のIT・DX投資権限を持ちそうか、あるいは本社一括管理型かを踏まえ、DX営業で刺さる具体的なキーワードや課題（業務効率化、人手不足解消、クラウド移行など）を3〜5個抽出してください。
    
    必ず以下のJSON形式のみで出力してください（マークダウンのバッククォートは使用禁止）。
    {{
        "is_found": trueまたはfalse,
        "reasoning": "1〜2文の判定理由と、拠点の位置づけ（権限の有無など）の考察",
        "details": [{{"name": "拠点名", "address": "住所", "url": "URL"}}],
        "sales_keywords": ["DXキーワード1", "DXキーワード2", "DXキーワード3"]
    }}
    """
    
    response = client.models.generate_content(
        model='gemini-3.5-flash',
        contents=prompt,
        config=types.GenerateContentConfig(
            response_mime_type="application/json"
        ),
    )
    
    raw_text = response.text.strip()
    if raw_text.startswith("```"):
        raw_text = re.sub(r"^```(?:json)?\n", "", raw_text)
        raw_text = re.sub(r"\n```$", "", raw_text)
        
    return json.loads(raw_text.strip())

company_name = st.text_input("調査したい会社名を入力", placeholder="例: 株式会社さわやか")

if st.button("DX営業リサーチを実行", type="primary"):
    if not company_name:
        st.warning("会社名を入力してください。")
    else:
        gemini_key = os.getenv("GEMINI_API_KEY")
        
        if not gemini_key:
            st.error("⚠️ サーバーのVariablesにAPIキー（GEMINI_API_KEY）が設定されていません。")
            st.stop()

        with st.spinner(f"「{company_name}」の九州拠点とDX営業の切り口を分析中..."):
            try:
                result = analyze_company(company_name, gemini_key)
                
                st.divider()
                if result.get('is_found'):
                    st.success(f"⭕ 「{company_name}」の九州拠点が確認されました！")
                    st.info(f"**分析・判定理由:** {result.get('reasoning')}")
                    
                    keywords = result.get('sales_keywords', [])
                    if keywords:
                        st.markdown("### 🔑 DX営業アプローチキーワード")
                        st.markdown(" ".join([f"`{kw}`" for kw in keywords]))
                    
                    st.markdown("### 📍 拠点詳細")
                    for d in result.get('details', []):
                        with st.container(border=True):
                            st.markdown(f"**{d.get('name')}**")
                            st.write(f"住所: {d.get('address')}")
                            st.markdown(f"[詳細リンク]({d.get('url')})")
                else:
                    st.error(f"❌ 「{company_name}」の確実な九州拠点は確認されませんでした（本社アプローチ推奨）。")
                    st.write(f"**分析・判定理由:** {result.get('reasoning')}")
                    
                    keywords = result.get('sales_keywords', [])
                    if keywords:
                        st.markdown("### 🔑 本社向けDX営業キーワード")
                        st.markdown(" ".join([f"`{kw}`" for kw in keywords]))
                    
            except Exception as e:
                st.error(f"エラーが発生しました: {e}")
