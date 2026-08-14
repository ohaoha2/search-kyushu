import streamlit as st
import json
import os
import re
from google import genai
from google.genai import types

st.set_page_config(page_title="九州拠点・DX営業リサーチ", page_icon="✨")

st.title("✨ 九州拠点・DX営業リサーチツール")
st.write("企業の九州拠点の有無と、DX営業代行で使えるアプローチキーワード（約10個）を抽出します。")

@st.cache_data(ttl=86400)
def analyze_company(company_name, gemini_key):
    client = genai.Client(api_key=gemini_key)
    
    prompt = f"""
    あなたは企業の所在調査およびDX営業戦略のプロフェッショナルです。
    ターゲット企業: "{company_name}"
    
    この企業が九州（福岡, 佐賀, 長崎, 熊本, 大分, 宮崎, 鹿児島）に実在の拠点（支店、営業所、工場、事業所など）を持っているか調査してください。
    
    指示:
    1. ターゲット企業と「実体として同一である」拠点を特定してください。
    2. 名前が似ているだけのまったく関係ない別会社は徹底的に排除してください。
    3. 確実な証拠がある場合のみ "is_found": true とし、拠点名、住所、URLを抽出してください。
    4. 確証がない場合や別会社しか見つからない場合は "is_found": false にしてください。
    5. "reasoning" は1〜2文で簡潔にまとめてください。
    6. この企業へのDX営業代行アプローチで使えそうなキーワードや業界特性（10個程度）を "sales_keywords" の配列として抽出してください。
    
    必ず以下のJSONフォーマットのみで回答してください（Markdownのバッククォート ``` は使わず、純粋なJSON文字列だけで出力してください）。
    {{
        "is_found": true,
        "reasoning": "1〜2文の簡潔な判定理由",
        "details": [
            {{"name": "拠点名", "address": "住所", "url": "URL"}}
        ],
        "sales_keywords": ["キーワード1", "キーワード2", "キーワード3", "キーワード4", "キーワード5", "キーワード6", "キーワード7", "キーワード8", "キーワード9", "キーワード10"]
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

# 入力フォーム
company_name = st.text_input("調査したい会社名を入力", placeholder="例: 株式会社さわやか、東洋エンジニアリング")

if st.button("リサーチを実行", type="primary"):
    if not company_name:
        st.warning("会社名を入力してください。")
    else:
        gemini_key = os.getenv("GEMINI_API_KEY")
        
        if not gemini_key:
            st.error("⚠️ サーバーのVariablesにAPIキー（GEMINI_API_KEY）が設定されていません。")
            st.stop()

        with st.spinner(f"「{company_name}」の九州拠点とDX営業キーワードを分析中..."):
            try:
                result = analyze_company(company_name, gemini_key)
                
                # 結果表示
                st.divider()
                if result.get('is_found'):
                    st.success(f"⭕ 「{company_name}」の九州拠点が確認されました！")
                    st.info(f"**判定理由:** {result.get('reasoning')}")
                    
                    # 営業キーワードの表示（約10個）
                    keywords = result.get('sales_keywords', [])
                    if keywords:
                        st.markdown("### 🔑 DX営業アプローチキーワード")
                        keywords_md = " ".join([f"`{kw}`" for kw in keywords])
                        st.markdown(keywords_md)
                    
                    st.markdown("### 📍 拠点詳細")
                    for d in result.get('details', []):
                        with st.container(border=True):
                            st.markdown(f"**{d.get('name')}**")
                            st.write(f"住所: {d.get('address')}")
                            st.markdown(f"[詳細リンク]({d.get('url')})")
                else:
                    st.error(f"❌ 「{company_name}」の確実な九州拠点は確認されませんでした。")
                    st.write(f"**判定理由:** {result.get('reasoning')}")
                    
                    # キーワード表示
                    keywords = result.get('sales_keywords', [])
                    if keywords:
                        st.markdown("### 🔑 DX営業アプローチキーワード（参考）")
                        keywords_md = " ".join([f"`{kw}`" for kw in keywords])
                        st.markdown(keywords_md)
                        
            except Exception as e:
                st.error(f"エラーが発生しました: {e}")
