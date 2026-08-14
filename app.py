import streamlit as st
import json
import os
from google import genai
from google.genai import types

st.set_page_config(page_title="九州拠点チェッカー（無料・高精度）", page_icon="✨")

st.title("✨ 九州拠点チェッカー（完全無料・高精度）")
st.write("Google Geminiの無料APIと検索機能を使って、同名異会社を排除し正確に調査します。")

# 入力フォーム
company_name = st.text_input("調査したい会社名を入力", placeholder="例: 株式会社さわやか、東洋エンジニアリング")

if st.button("無料で調査する", type="primary"):
    if not company_name:
        st.warning("会社名を入力してください。")
    else:
        # Variablesから環境変数としてAPIキーを取得
        gemini_key = os.getenv("GEMINI_API_KEY")
        
        if not gemini_key:
            st.error("⚠️ サーバーのVariablesにAPIキー（GEMINI_API_KEY）が設定されていません。")
            st.stop()

        with st.spinner(f"「{company_name}」をAIがGoogle検索＆分析中...少々お待ちください。"):
            try:
                client = genai.Client(api_key=gemini_key)
                
                prompt = f"""
                あなたは企業の所在調査のプロフェッショナルです。
                ターゲット企業: "{company_name}"
                
                Google検索を使用して、この企業が九州（福岡、佐賀、長崎、熊本、大分、宮崎、鹿児島、沖縄）に実在の拠点（支店、営業所、工場、事業所など）を持っているか調査してください。
                
                指示:
                1. 検索結果を活用し、ターゲット企業と「実体として同一である」拠点を特定してください。
                2. 名前が似ているだけのまったく関係ない別会社（例：さわやかケアサービスやクリーニング店など、資本関係や事業内容が異なるもの）は徹底的に排除してください。
                3. 確実な証拠がある場合のみ "is_found": true とし、拠点名、住所、URLを抽出してください。
                4. 確証がない場合や別会社しか見つからない場合は "is_found": false にしてください。
                
                必ず以下のJSONフォーマットで回答してください。
                {{
                    "is_found": boolean,
                    "reasoning": "なぜ同一企業と判定したか、あるいはなぜ別会社と判断して除外したかの詳細な理由",
                    "details": [
                        {{"name": "拠点名", "address": "住所（不明な場合は記載なし）", "url": "URL"}}
                    ]
                }}
                """
                
                # 最新の gemini-3.5-flash と Google検索ツール（Grounding）を使用
                response = client.models.generate_content(
                    model='gemini-3.5-flash',
                    contents=prompt,
                    config=types.GenerateContentConfig(
                        tools=[{'google_search': {}}],
                        response_mime_type="application/json"
                    ),
                )
                
                result = json.loads(response.text)
                
                # 結果表示
                st.divider()
                if result.get('is_found'):
                    st.success(f"⭕ 「{company_name}」の九州拠点が確認されました！")
                    st.info(f"**AIの判定理由:** {result.get('reasoning')}")
                    for d in result.get('details', []):
                        with st.container(border=True):
                            st.markdown(f"**📍 {d.get('name')}**")
                            st.write(f"住所: {d.get('address')}")
                            st.markdown(f"[詳細リンク]({d.get('url')})")
                else:
                    st.error(f"❌ 「{company_name}」の確実な九州拠点は確認されませんでした。")
                    st.write(f"**判定理由:** {result.get('reasoning')}")
                    
            except Exception as e:
                st.error(f"エラーが発生しました: {e}")