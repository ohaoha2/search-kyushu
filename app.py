import streamlit as st
import json
import os
from google import genai
from google.genai import types
from duckduckgo_search import DDGS

st.set_page_config(page_title="九州拠点・DX営業リサーチ", page_icon="✨")

st.title("✨ 九州拠点・DX営業リサーチツール（DuckDuckGo ＋ gemini-3.5-flash）")
st.write("DuckDuckGo検索でWeb情報を取得し、gemini-3.5-flashで正確にリサーチします。")

# DuckDuckGoによる検索関数
def search_web_with_ddg(query):
    try:
        search_query = f"{query} 会社 企業 拠点"
        with DDGS() as ddgs:
            results = list(ddgs.text(search_query, max_results=5))
        
        if not results:
            return None, "DuckDuckGoの検索結果が見つかりませんでした。"
            
        context = "\n".join([f"- タイトル: {r.get('title')}\n  内容: {r.get('body')}\n  URL: {r.get('href')}" for r in results])
        return context, None
    except Exception as e:
        return None, f"DuckDuckGo検索エラー: {str(e)}"

# Gemini 3.5 Flashによる分析関数
def analyze_company_data(query, web_context, gemini_key):
    client = genai.Client(api_key=gemini_key)
    
    prompt = f"""
    あなたは企業の所在調査およびDX営業戦略のプロフェッショナルです。
    検索ターゲット: "{query}"

    【取得した最新のWeb検索結果】
    {web_context}

    指示:
    1. 上記のWeb検索結果を基に、入力された情報（会社名、または住所）に該当する「正確な企業名や施設名」を突き止めてください。
    2. 近隣の無関係な有名施設（例：ヤマハのテストコースなど）と絶対に混同せず、実際の事業者を正確に特定してください。
    3. その企業が九州（福岡, 佐賀, 長崎, 熊本, 大分, 宮崎, 鹿児島）に実在の直営拠点を持っているか調査してください。
    4. 確証がある場合は "is_found": true とし、企業名・拠点名、正確な住所、URLを抽出してください。
    5. 確証がない場合でも、検索結果から読み取れる情報を基にベストエフォートで判定してください。
    6. "reasoning" は1〜2文で簡潔にまとめてください。
    7. この企業へのDX営業代行アプローチで使えそうなキーワードや業界特性（10個）を "sales_keywords" の配列として抽出してください。
    
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
        model='gemini-3.5-flash',
        contents=prompt,
        config=types.GenerateContentConfig(
            response_mime_type="application/json"
        ),
    )
    
    return json.loads(response.text.strip())

# 入力フォーム
query = st.text_input("会社名、または住所を入力", placeholder="例: 株式会社ティーエフケー、静岡県袋井市宇刈137")

if st.button("検索＆リサーチを実行", type="primary"):
    if not query:
        st.warning("会社名または住所を入力してください。")
    else:
        gemini_key = os.getenv("GEMINI_API_KEY")
        
        if not gemini_key:
            st.error("⚠️ サーバーのVariablesにGEMINI_API_KEYが設定されていません。")
            st.stop()

        with st.spinner(f"「{query}」をDuckDuckGoで検索中..."):
            # 1. DuckDuckGoで検索
            web_context, err = search_web_with_ddg(query)
            
            if err:
                st.error(err)
            else:
                # 検索結果の確認用エクスパンダー
                with st.expander("🔍 DuckDuckGo検索エンジンの取得データ"):
                    st.text(web_context)
                
                with st.spinner("gemini-3.5-flashで分析中..."):
                    try:
                        result = analyze_company_data(query, web_context, gemini_key)
                        
                        st.divider()
                        if result.get('is_found'):
                            st.success(f"⭕ 該当する企業・拠点が正確に確認されました！")
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
                            st.error(f"❌ 確実な九州拠点は確認されませんでした。")
                            st.write(f"**判定理由:** {result.get('reasoning')}")
                            
                            keywords = result.get('sales_keywords', [])
                            if keywords:
                                st.markdown("### 🔑 DX営業アプローチキーワード（参考）")
                                keywords_md = " ".join([f"`{kw}`" for kw in keywords])
                                st.markdown(keywords_md)
                                
                    except Exception as e:
                        st.error(f"分析エラーが発生しました: {e}")
