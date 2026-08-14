import streamlit as st
import json
import os
import re
import pandas as pd
from tavily import TavilyClient
from google import genai
from google.genai import types

st.set_page_config(page_title="九州拠点一括検索ツール", page_icon="✨", layout="wide")

st.title("✨ 九州拠点一括検索・フックキーワード提案ツール")
st.markdown("Tavily AIとGeminiを活用した、安定・高速な一括調査ツールです。")

# ==========================================
# APIキーの自動取得（Secrets優先）
# ==========================================
tavily_api_key = os.getenv("TAVILY_API_KEY") or st.secrets.get("TAVILY_API_KEY", "")
gemini_key = os.getenv("GEMINI_API_KEY") or st.secrets.get("GEMINI_API_KEY", "")

# ==========================================
# 0. セッションステート初期化
# ==========================================
if "search_history" not in st.session_state:
    st.session_state.search_history = []
if "result_cache" not in st.session_state:
    st.session_state.result_cache = {}

# ==========================================
# 1. Tavily API 実行関数
# ==========================================
def fetch_tavily_results(query: str, api_key: str):
    try:
        client = TavilyClient(api_key=api_key)
        response = client.search(
            query=query.strip().replace('`', ''),
            search_depth="basic",
            max_results=5
        )
        results = []
        for item in response.get('results', []):
            results.append({
                'title': item.get('title', ''),
                'url': item.get('url', ''),
                'snippet': item.get('content', '')
            })
        return results
    except Exception as e:
        return []

def search_multi_queries(keyword: str, api_key: str):
    q1 = f'"{keyword}" 会社概要 公式サイト'
    q2 = f'"{keyword}" 九州 福岡 支店 営業所'
    
    queries = [q1, q2]
    all_results = []
    seen_urls = set()
    
    for q in queries:
        res_list = fetch_tavily_results(q, api_key)
        for r in res_list:
            if r['url'] not in seen_urls:
                seen_urls.add(r['url'])
                all_results.append(r)
                
    if not all_results:
        return "", []
        
    context = "\n".join([f"- タイトル: {r['title']}\n  内容: {r['snippet']}\n  URL: {r['url']}" for r in all_results[:15]])
    return context, all_results

# ==========================================
# 2. JSONパース安全装置
# ==========================================
def safe_parse_json(text):
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        text = re.sub(r"```json|
