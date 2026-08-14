import streamlit as st
import json
import os
import re
from google import genai
from google.genai import types

st.set_page_config(page_title="九州拠点・DX営業リサーチ（検索版）", page_icon="✨")

st.title("✨ 九州拠点・DX営業リサーチツール（Google検索連携）")
st.write("Google公式の検索機能（Grounding）を用いて、最新のWeb情報を基に正確にリサーチします。")

@st.cache_data(ttl=86400)
def analyze_company_with_google_search(query, gemini_key):
    client = genai.Client(api_key=gemini_key)
    
    prompt = f"""
    あなたは企業の所在調査およびDX営業戦略のプロフェッショナルです。
    検索ターゲット（会社名、または住所）: "{query}"
    
    指示:
    1. Google検索を用いて最新のWeb情報を確認し、入力された情報（会社名、または住所）に該当する「正確な企業名や施設名」を突き止めてください。
    2. 近隣の無関係な有名施設（例：ヤマハのテストコースなど）と絶対に混同せず、その住所や会社名に紐づく実際の事業者を正確に特定してください。
    3. その企業が九州（福岡, 佐賀, 長崎, 熊本, 大分, 宮崎, 鹿児島）に実在の直営拠点を持っているか調査してください。
    4. 確実な証拠がある場合のみ "is_found": true とし、企業名・拠点名、正確な住所、実際のURLを抽出してください。
    5. 確証がない場合は "is_found": false にしてください。
    6. "reasoning" は1〜2文で簡潔にまとめてください。
    7. この企業へのDX営業代行アプローチで使えそうなキーワードや業界特性（10個程度）を "sales_keywords" の配列として抽出してください。
    
    必ず以下のJSONフォーマットのみで回答してください（Markdownのバッククォート ``` は使わず、純粋なJSON文字列だけで出力してください）。
    {{
        "is_found": true,
        "reasoning": "1〜2文の簡潔な判定理由",
        "details": [
            {{"name": "企業名・拠点名", "address": "住所", "url": "URL"}}
        ],
        "sales_keywords": ["キーワード1", "キーワード2", "キーワード3", "キーワード4", "キーワード5", "キーワード6", "キーワード7", "キーワード8", "キーワード9", "キーワード10"]
    }}
    """
    
    # Google公式の検索ツール（Grounding）を有効化
    response = client.models.generate_content(
        model='gemini-2.5-flash', # 安定して検索ツールが使えるモデル
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

if st.button("Google検索でリサーチを実行", type="primary"):
    if not query:
        st.warning("会社名または住所を入力してください。")
    else:
        gemini_key = os.getenv("GEMINI_API_KEY")
        
        if not gemini_key:
            st.error("⚠️ サーバーのVariablesにAPIキー（GEMINI_API_KEY）が設定されていません。")
            st.stop()

        with st.spinner(f"「{query}」をGoogle検索で正確に調査中..."):
            try:
                result = analyze_company_with_google_search(query, gemini_key)
                
                st.divider()
                if result.get('is_found'):
                    st.success(f"⭕ 該当する企業・拠点が確認されました！")
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
                    st.error(f"❌ 確実な情報は確認されませんでした。")
                    st.write(f"**判定理由:** {result.get('reasoning')}")
                    
                    keywords = result.get('sales_keywords', [])
                    if keywords:
                        st.markdown("### 🔑 DX営業アプローチキーワード（参考）")
                        keywords_md = " ".join([f"`{kw}`" for kw in keywords])
                        st.markdown(keywords_md)
                        
            except Exception as e:
                st.error(f"エラーが発生しました（※無料枠の制限の場合はGoogle AI Studioで従量課金の有効化が必要です）: {e}")
