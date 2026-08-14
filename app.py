import streamlit as st
import json
import os
import re
from google import genai
from google.genai import types

st.set_page_config(page_title="九州拠点・地震対応チェッカー", page_icon="⚡")

st.title("⚡ 九州拠点・地震対応チェッカー")
st.write("熊本の地震に伴う一時的な対応として、企業の九州拠点の有無と状況を高速で調査します。")

@st.cache_data(ttl=86400)
def analyze_company(company_name, gemini_key):
    client = genai.Client(api_key=gemini_key)
    
    prompt = f"""
    企業「{company_name}」が九州（福岡, 佐賀, 長崎, 熊本, 大分, 宮崎, 鹿児島）に直営拠点（支店、営業所、工場、事業所など）を持っているか調査してください。
    特に熊本の地震等の災害に対する一時的な対応や、現地拠点の有無・状況に焦点を当ててください。
    同名異会社は完全に排除してください。
    
    必ず以下のJSON形式のみで出力してください（マークダウンのバッククォートは使用禁止）。
    {{
        "is_found": trueまたはfalse,
        "reasoning": "拠点の有無と地震等の対応・状況に関する1〜2文の簡潔な説明",
        "details": [{{"name": "拠点名", "address": "住所", "url": "URL"}}]
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

if st.button("高速で調査する", type="primary"):
    if not company_name:
        st.warning("会社名を入力してください。")
    else:
        gemini_key = os.getenv("GEMINI_API_KEY")
        
        if not gemini_key:
            st.error("⚠️ サーバーのVariablesにAPIキー（GEMINI_API_KEY）が設定されていません。")
            st.stop()

        with st.spinner(f"「{company_name}」の拠点を高速分析中..."):
            try:
                result = analyze_company(company_name, gemini_key)
                
                st.divider()
                if result.get('is_found'):
                    st.success(f"⭕ 「{company_name}」の九州拠点が確認されました！")
                    st.info(f"**状況・判定理由:** {result.get('reasoning')}")
                    
                    st.markdown("### 📍 拠点詳細")
                    for d in result.get('details', []):
                        with st.container(border=True):
                            st.markdown(f"**{d.get('name')}**")
                            st.write(f"住所: {d.get('address')}")
                            st.markdown(f"[詳細リンク]({d.get('url')})")
                else:
                    st.error(f"❌ 「{company_name}」の確実な九州拠点は確認されませんでした。")
                    st.write(f"**状況・判定理由:** {result.get('reasoning')}")
                    
            except Exception as e:
                st.error(f"エラーが発生しました: {e}")
