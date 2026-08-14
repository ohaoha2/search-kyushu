import streamlit as st
import json
import os
import re
import requests
import pandas as pd
from bs4 import BeautifulSoup
from google import genai
from google.genai import types

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
    q1 = f'"{keyword}" 会社概要 拠点 支店 一覧'
    q2 = f'"{keyword}" 九州 福岡 支店 営業所'
    q3 = f'"{keyword}" 公式サイト コーポレート'
    
    queries = [q1, q2, q3]
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
        
    context = "\n".join([f"- タイトル: {r['title']}\n  内容: {r['snippet']}\n  URL: {r['url']}" for r in all_results[:25]])
    return context, all_results

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
# 3. 複数社を一括でAI分析する関数（バッチ処理・精度強化版）
# ==========================================
def analyze_companies_batch(batch_data, gemini_key):
    client = genai.Client(api_key=gemini_key)
    
    prompt_targets = ""
    for i, item in enumerate(batch_data):
        prompt_targets += f"\n=== 対象企業 {i+1}: {item['company']} ===\n【検索結果】\n{item['context']}\n"

    template = """
あなたは企業の所在調査のプロフェッショナルです。ハルシネーション（存在しない拠点をあると誤認すること）を厳禁とします。
以下の複数の企業について、それぞれ提供された検索結果を基に厳密に調査し、結果を必ず【JSONの配列（リスト）】で返してください。

{prompt_targets}

各企業ごとの共通指示:
1. "company": 入力された会社名をそのまま格納してください。
2. "official_url": 公式サイトのコーポレートサイトURL（Wikipedia、求人サイト、ニュースサイトは除外。見つからない場合は null）
3. "is_found": 九州地方（福岡, 佐賀, 長崎, 熊本, 大分, 宮崎, 鹿児島）に、現在稼働している直営の支店、営業所、工場、事業所が明確に裏付けられる場合のみ true としてください。以下の場合は必ず false にしてください：
   - 単なる「施工実績」「納入実績」「代理店」「パートナー企業」「別法人のグループ会社」の言及である場合
   - すでに閉鎖・廃止された拠点である場合
4. "details": 九州内の確実な直営拠点ごとの詳細情報（名称, 住所, URL）のリスト（見つからない場合は空配列 []）
5. "sales_keywords": 営業アプローチ用のキーワード10個のリスト

必ず以下のJSON配列フォーマットのみで回答してください（マークダウンの ```json や
