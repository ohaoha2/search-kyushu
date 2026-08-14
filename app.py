import streamlit as st
import json
import os
import re
import requests
import pandas as pd
import time
from bs4 import BeautifulSoup
from google import genai
from google.genai import types
from concurrent.futures import ThreadPoolExecutor, as_completed

st.set_page_config(page_title="九州拠点一括検索ツール", page_icon="✨", layout="wide")

st.title("✨ 九州拠点一括検索・フックキーワード提案ツール")
st.markdown("スプレッドシートなどから会社名をコピーし、下のテキストエリアに貼り付けて一括検索してください。")

# ==========================================
# 0. セッションステート初期化
# ==========================================
if "search_history" not in st.session_state:
    st.session_state.search_history = []
if "result_cache" not in st.session_state:
    st.session_state.result_cache = {}

# ==========================================
# 1. 検索エンジンの実行関数
# ==========================================
def fetch_ddg_results(query: str):
    clean_kw = query.strip().replace('`', '')
    url = "https://lite.duckduckgo.com/lite/"
    data = {'q': clean_kw}
    headers = {"User-Agent": "Mozilla/5.0"}
    try:
        # ブロック回避のための待機（スレッド数が少ないため許容範囲です）
        time.sleep(0.5)
        res = requests.post(url, data=data, headers=headers, timeout=10)
        soup = BeautifulSoup(res.text, "html.parser")
        results = []
        rows = soup.find_all("tr")
        current_title = ""; current_url = ""
        for row in rows:
            link_tag = row.find("a", class_="result-link")
            if link_tag:
                current_title = link_tag.text.strip(); current_url = link_tag["href"]
            snippet_tag = row.find("td", class_="result-snippet")
            if snippet_tag:
                current_snippet = snippet_tag.text.strip()
                if current_title and current_url:
                    results.append({'title': current_title, 'url': current_url, 'snippet': current_snippet})
                    current_title = ""; current_url = ""
        return results
    except:
        return []

def search_multi_queries(keyword: str):
    # 検索クエリは以前の安定版を維持
    queries = [
        f'"{keyword}" 会社概要 拠点',
        f'"{keyword}" 福岡 九州 支店 営業所'
    ]
    
    all_results = []
    seen_urls = set()
    
    for q in queries:
        res_list = fetch_ddg_results(q)
        for r in res_list:
            if r['url'] not in seen_urls:
                seen_urls.add(r['url'])
                all_results.append(r)
                
    if not all_results:
        return "", []
        
    # 【重要】検索件数は25件に戻します（情報の網羅性を担保）
    top_results = all_results[:25]
    context = "\n".join([f"- タイトル: {r['title']}\n  内容: {r['snippet']}\n  URL: {r['url']}" for r in top_results])
    return context, top_results

# ==========================================
# 2. JSONパース安全装置
# ==========================================
def safe_parse_json(text):
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        text = re.sub(r"```json|```", "", text).strip()
        match = re.search(r"\[.*\]|\{.*\}", text, re.DOTALL)
        if match: return json.loads(match.group(0))
        raise

# ==========================================
# 3. AI分析関数（プロンプト保持）
# ==========================================
def analyze_companies_batch(batch_data, gemini_key):
    client = genai.Client(api_key=gemini_key)
    
    prompt_targets = ""
    for i, item in enumerate(batch_data):
        prompt_targets += f"\n=== 対象企業 {i+1}: {item['company']} ===\n【検索結果】\n{item['context']}\n"

    template = (
        "あなたは企業の所在調査のプロフェッショナルです。同名異社（例：別業種の同名企業）の混同や、ハルシネーション（存在しない拠点をあると誤認すること）を厳禁とします。\n"
        "以下の複数の企業について、それぞれ提供された検索結果を基に厳密に調査し、結果を必ず【JSONの配列（リスト）】で返してください。\n\n"
        "{prompt_targets}\n\n"
        "各企業ごとの共通指示:\n"
        "1. \"company\": 入力された会社名をそのまま格納してください。\n"
        "2. \"official_url\": 公式サイトのコーポレートサイトURL（Wikipedia、求人サイト、ニュースサイトは除外。見つからない場合は null）\n"
        "3. \"is_found\": 九州地方（福岡, 佐賀, 長崎, 熊本, 大分, 宮崎, 鹿児島）に、現在稼働している直営の支店、営業所、工場、事業所が明確に裏付けられる場合のみ true としてください。以下の場合は必ず false にしてください：\n"
        "   - 同名異社の情報である場合\n"
        "   - 単なる「施工実績」「納入実績」「代理店」「パートナー企業」「別法人のグループ会社」の言及である場合\n"
        "   - すでに閉鎖・廃止された拠点である場合\n"
        "4. \"details\": 九州内の確実な直営拠点ごとの詳細情報（名称, 住所, URL）のリスト（見つからない場合は空配列 []）\n"
        "5. \"sales_keywords\": 営業アプローチ用のキーワード10個のリスト\n\n"
        "必ず以下のJSON配列フォーマットのみで回答してください：\n"
        "[\n"
        "    {\n"
        "        \"company\": \"会社名\",\n"
        "        \"is_found\": true,\n"
        "        \"official_url\": \"https://...\",\n"
        "        \"details\": [{\"name\": \"...\", \"address\": \"...\", \"url\": \"...\"}],\n"
        "        \"sales_keywords\": [\"キーワード1\", \"キーワード2\", ...]\n"
        "    }\n"
        "]"
    )
    prompt = template.replace("{prompt_targets}", prompt_targets)

    try:
        response = client.models.generate_content(
            model='gemini-3.5-flash-lite',
            contents=prompt,
            config=types.GenerateContentConfig(response_mime_type="application/json"),
        )
        return safe_parse_json(response.text.strip())
    except Exception as e:
        st.error(f"AI分析エラー: {str(e)}")
        return []

# ==========================================
# 4. Streamlit UI 構築
# ==========================================
with st.form(key="batch_search_form"):
    raw_input = st.text_area("📋 会社名リストを入力", placeholder="株式会社〇〇\n株式会社△△", height=150)
    submit_button = st.form_submit_button("一括検索・分析を実行", type="primary")

if submit_button:
    if not raw_input.strip():
        st.warning("会社名を入力してください。")
    else:
        lines = raw_input.strip().split("\n")
        company_list = [l.split("\t")[0].strip() for l in lines if l.strip()]
        company_list = list(dict.fromkeys(company_list))

        gemini_key = os.getenv("GEMINI_API_KEY")
        if not gemini_key:
            st.error("APIキーが設定されていません。")
            st.stop()

        batch_results = []
        progress_bar = st.progress(0)
        status_text = st.empty()
        
        company_map = {}
        to_fetch = [c for c in company_list if c not in st.session_state.result_cache]
        
        for c in company_list:
            if c in st.session_state.result_cache:
                company_map[c] = st.session_state.result_cache[c]

        status_text.text("🌐 Web検索とAI分析を実行中...")
        fetched_data = []

        def process_single_company(comp):
            context, raw_results = search_multi_queries(comp)
            return {"company": comp, "context": context, "raw_results": raw_results}

        # 【重要】並行処理数を2に制限し、ブロックを回避
        if to_fetch:
            with ThreadPoolExecutor(max_workers=2) as executor:
                future_to_comp = {executor.submit(process_single_company, c): c for c in to_fetch}
                
                completed = 0
                for future in as_completed(future_to_comp):
                    fetched_data.append(future.result())
                    completed += 1
                    progress_bar.progress((completed / max(len(to_fetch), 1)) * 0.5)

        # 【重要】チャンクサイズを10に戻す
        chunk_size = 10
        for i in range(0, len(fetched_data), chunk_size):
            chunk = fetched_data[i:i+chunk_size]
            res_list = analyze_companies_batch(chunk, gemini_key)
            if isinstance(res_list, list):
                for r in res_list:
                    company_map[r["company"]] = r
                    st.session_state.result_cache[r["company"]] = r
            
            base_progress = 0.5
            ai_progress = ((i + len(chunk)) / len(fetched_data)) * 0.5 if fetched_data else 0.5
            progress_bar.progress(min(base_progress + ai_progress, 1.0))

        for comp in company_list:
            res = company_map.get(comp, {"is_found": False, "official_url": None, "details": [], "sales_keywords": []})
            batch_results.append({
                "会社名": comp,
                "判定": "⭕ 九州拠点あり" if res.get('is_found') else "❌ 拠点なし",
                "公式サイト": res.get('official_url'),
                "確認された拠点": ", ".join([f"{d.get('name')} ({d.get('address')})" for d in res.get('details', [])]) if res.get('details') else "なし",
                "フックキーワード": ", ".join(res.get('sales_keywords', [])),
                "_raw_details": res.get('details', []),
                "_raw_keywords": res.get('sales_keywords', [])
            })

        progress_bar.progress(1.0)
        status_text.text("✅ 完了！")
        st.session_state["batch_results"] = batch_results

# ==========================================
# 5. 表示
# ==========================================
if "batch_results" in st.session_state:
    results = st.session_state["batch_results"]
    df = pd.DataFrame(results)[["会社名", "判定", "公式サイト", "確認された拠点", "フックキーワード"]]
    
    st.divider()
    st.subheader("📊 検索・分析結果一覧")
    st.dataframe(df, use_container_width=True)
    
    st.code(df.to_csv(sep="\t", index=False), language="text")
    
    for r in results:
        with st.expander(f"{r['会社名']} ({r['判定']})"):
            if r['公式サイト']:
                st.markdown(f"**🌐 公式サイト:** [{r['公式サイト']}]({r['公式サイト']})")
            if r['_raw_details']:
                for d in r['_raw_details']:
                    st.write(f"- {d.get('name')} : {d.get('address')}")
