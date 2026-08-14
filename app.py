import streamlit as st
import json
import os
import re
from google import genai
from google.genai import types

st.set_page_config(page_title="九州拠点チェッカー（無料・高精度）", page_icon="✨")

st.title("✨ 九州拠点チェッカー（完全無料・高精度）")
st.write("Google Geminiの無料APIを使って、同名異会社を排除し正確に調査します。")

# 入力フォーム
company_name = st.text_input("調査したい会社名を入力", placeholder="例: 株式会社さわやか、東洋エンジニアリング")

if st.button("無料で調査する", type="primary"):
    if not company_name:
        st.warning("会社名を入力してください。")
    else:
        gemini_key = os.getenv("GEMINI_API_KEY")
        
        if not gemini_key:
            st.error("⚠️ サーバーのVariablesにAPIキー（GEMINI_API_KEY）が設定されていません。")
            st.stop()

        with st.spinner(f"「{company_name}」をAIが分析中...少々お待ちください。"):
            try:
                client = genai.Client(api_key=gemini_key)
                
                prompt = f"""
                あなたは企業の所在調査のプロフェッショナルです。
                ターゲット企業: "{company_name}"
                
                この企業が九州（福岡, 佐賀, 長崎, 熊本, 大分, 宮崎, 鹿児島, 沖縄）に実在の拠点（支店、営業所、工場、事業所など）を持っているか調査してください。
                
                指示:
                1. ターゲット企業と「実体として同一である」拠点を特定してください。
                2. 名前が似ているだけのまったく関係ない別会社（例：さわやかケアサービスやクリーニング店など）は徹底的に排除してください。
                3. 確実な証拠がある場合のみ "is_found": true とし、拠点名、住所、URLを抽出してください。
                4. 確証がない場合や別会社しか見つからない場合は "is_found": false にしてください。
                
                必ず以下のJSONフォーマットのみで回答してください（Markdownのバッククォート ``` は使わず、純粋なJSON文字列だけで出力してください）。
                {{
                    "is_found": true,
                    "reasoning": "判定理由の説明",
                    "details": [
                        {{"name": "拠点名", "address": "住所", "url": "URL"}}
                    ]
                }}
                """
                
                response = client.models.generate_content(
                    model='gemini-3.5-flash',
                    contents=prompt,
                    config=types.GenerateContentConfig(
                        response_mime_type="application/json"
                    ),
                )
                
                # --- ここでレスポンスのテキストを綺麗に掃除する処理を追加 ---
                raw_text = response.text.strip()
                # もし ```json ... ``` で囲まれていたら取り除く
                if raw_text.startswith("```"):
                    raw_text = re.sub(r"^```(?:json)?\n", "", raw_text)
                    raw_text = re.sub(r"\n```$", "", raw_text)
                
                result = json.loads(raw_text.strip())
                # ----------------------------------------------------
                
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
