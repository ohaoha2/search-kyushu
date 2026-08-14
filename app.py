import streamlit as st
import json
import os
import re
from google import genai
from google.genai import types
from duckduckgo_search import DDGS

st.set_page_config(page_title="九州拠点・DX営業リサーチ", page_icon="✨")

st.title("✨ 九州拠点・DX営業リサーチツール")
st.write("会社名や住所からWeb検索（回数無制限・無料）で正確に特定し、DX営業代行のアプローチキーワードを抽出します。")

# DuckDuckGoを使って無料でWeb検索を行う関数（回数制限なし）
def search_web_info(query):
    try:
        with DDGS() as ddg:
            # 上位3件の検索結果を取得してテキストにまとめる
            results = [r for r in ddg.text(query, max_results=3)]
            context = "\n".join([f"- タイトル: {r.get('title')}\n  内容: {r.get('body')}" for r in results])
            return context if context else "検索結果が見つかりませんでした。"
    except Exception as e:
        return f"検索エラー: {e}"

@st.cache_data(ttl=86400)
def analyze_company_with_search(query, gemini_key):
    # 1. まずDuckDuckGoでWeb検索を実行して情報を集める
    web_context = search_web_info(query)
    
    # 2. 集めた検索結果をGeminiに読ませて分析・判定させる（Geminiの検索ツールは使わないので429エラーが出ない）
    client = genai.Client(api_key=gemini_key)
    
    prompt = f"""
    あなたは企業の所在調査およびDX営業戦略のプロフェッショナルです。
    検索ターゲット（会社名、または住所）: "{query}"
    
    以下の【Web検索結果】を参考にして、正確な企業名や拠点、九州拠点の有無を判定してください。
    
    【Web検索結果】
    {web_context}
    
    指示:
    1. 検索結果を基に、入力された情報（会社名、または住所）に該当する「正確な企業名や施設名」を突き止めてください。近隣の無関係な有名施設（例：ヤマハのテストコースなど）と絶対に混同しないでください。
    2. その企業が九州（福岡, 佐賀, 長崎, 熊本, 大分, 宮崎, 鹿児島）に実在の直営拠点を持っているか調査してください。
    3. 確実な証拠がある場合のみ "is_found": true とし、企業名・拠点名、正確な住所、URLを抽出してください。
    4. 確証がない場合は "is_found": false にしてください。
    5. "reasoning" は1〜2文で簡潔にまとめてください。
    6. この企業へのDX営業代行アプローチで使えそうなキーワードや業界特性（10個程度）を "sales_keywords" の配列として抽出してください。
    
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
    
    response = client.models.generate_content(
        model='gemini-1.5-flash',
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
query = st.text_input("会社名、または住所を入力", placeholder="例: 株式会社ティーエフケー、静岡県袋井市宇刈137")

if st.button("リサーチを実行", type="primary"):
    if not query:
        st.warning("会社名または住所を入力してください。")
    else:
        gemini_key = os.getenv("GEMINI_API_KEY")
        
        if not gemini_key:
            st.error("⚠️ サーバーのVariablesにAPIキー（GEMINI_API_KEY）が設定されていません。")
            st.stop()

        with st.spinner(f"「{query}」をWeb検索して正確に分析中..."):
            try:
                result = analyze_company_with_search(query, gemini_key)
                
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
