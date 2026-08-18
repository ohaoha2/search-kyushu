import streamlit as st
import json
import os
import re
import requests
import pandas as pd
import concurrent.futures
from urllib.parse import urlparse
from google import genai
from google.genai import types

st.set_page_config(
    page_title="企業情報一括検索ツール",
    layout="wide"
)

st.title("企業情報一括検索ツール")

# ==========================================
# APIキー
# ==========================================
serper_api_key = (
    os.getenv("SERPER_API_KEY")
    or st.secrets.get("SERPER_API_KEY", "")
)

gemini_key = (
    os.getenv("GEMINI_API_KEY")
    or st.secrets.get("GEMINI_API_KEY", "")
)

# ==========================================
# セッションステート (キャッシュ)
# ==========================================
if "search_history" not in st.session_state:
    st.session_state.search_history = []

if "result_cache" not in st.session_state:
    st.session_state.result_cache = {}

# ==========================================
# 法人格一覧
# ==========================================
LEGAL_FORMS = [
    "株式会社", "有限会社", "合同会社", "合資会社", "合名会社",
    "一般社団法人", "一般財団法人", "公益社団法人", "公益財団法人",
    "学校法人", "医療法人", "社会福祉法人", "宗教法人", "特定非営利活動法人",
    "NPO法人", "独立行政法人", "国立大学法人", "地方独立行政法人",
    "相互会社", "信用金庫", "信用組合",
]

# ==========================================
# 第三者サイト除外（最小限）
# ==========================================
def is_excluded_domain(domain: str):
    if not domain:
        return True

    # 純粋な機械収集の企業データベースのみに絞る
    excluded_domains = [
        "wikipedia.org", "irbank.net", "compalyze.co.jp", "houjin.jp", 
        "xn--pckua2a7gp15o89zb.com", "baseconnect.in"
    ]

    return any(
        domain == excluded or domain.endswith("." + excluded)
        for excluded in excluded_domains
    )

# ==========================================
# URL → ドメイン
# ==========================================
def extract_domain(url: str):
    try:
        parsed = urlparse(url)
        if not parsed.netloc:
            return None
        domain = parsed.netloc.lower()
        if domain.startswith("www."):
            domain = domain[4:]
        return domain
    except Exception:
        return None

# ==========================================
# 会社名正規化
# ==========================================
def normalize_company_name(name: str):
    if not name:
        return ""
    name = str(name)
    name = re.sub(r"[\s ]+", "", name)
    return name.strip()

# ==========================================
# 法人格情報を抽出
# ==========================================
def parse_legal_entity(name: str):
    normalized = normalize_company_name(name)
    if not normalized:
        return {"original": "", "core": "", "legal_form": None, "position": "unknown"}

    sorted_forms = sorted(LEGAL_FORMS, key=len, reverse=True)

    for form in sorted_forms:
        if normalized.startswith(form):
            core = normalized[len(form):]
            if core:
                return {"original": normalized, "core": core, "legal_form": form, "position": "front"}
        if normalized.endswith(form):
            core = normalized[:-len(form)]
            if core:
                return {"original": normalized, "core": core, "legal_form": form, "position": "back"}

    return {"original": normalized, "core": normalized, "legal_form": None, "position": "unknown"}

# ==========================================
# 候補テキストから法人名候補を判定
# ==========================================
def candidate_entity_relation(input_company: str, candidate_text: str):
    input_info = parse_legal_entity(input_company)
    if not input_info["core"]:
        return "unknown"

    text = normalize_company_name(candidate_text)
    input_original = input_info["original"]
    input_core = input_info["core"]
    input_form = input_info["legal_form"]

    if input_core and input_form:
        opposite_company = ""
        if input_info["position"] == "front":
            opposite_company = f"{input_core}{input_form}"
        elif input_info["position"] == "back":
            opposite_company = f"{input_form}{input_core}"
        
        if opposite_company and (opposite_company in text) and (input_original not in text):
            return "mismatch"

    if input_original and input_original in text:
        return "match"

    if not input_core:
        return "unknown"

    for form in sorted(LEGAL_FORMS, key=len, reverse=True):
        front_pattern = re.escape(form) + re.escape(input_core)
        back_pattern = re.escape(input_core) + re.escape(form)

        has_front = re.search(front_pattern, text)
        has_back = re.search(back_pattern, text)

        if not has_front and not has_back:
            continue

        if (
            form == input_form
            and ((input_info["position"] == "front" and has_front) or
                 (input_info["position"] == "back" and has_back))
        ):
            return "match"

        return "mismatch"

    return "unknown"

# ==========================================
# Serper API 検索 (無料枠エラー回避版)
# ==========================================
def fetch_serper_results(query: str, api_key: str, include_domains: list = None):
    url = "https://google.serper.dev/search"
    
    # ★無料枠制限の回避: 「-site:」「OR」「()」を使わず超シンプルなクエリにする
    if include_domains and len(include_domains) > 0:
        # site: ドメイン名 だけであれば無料枠でも許可される
        final_query = f"{query} site:{include_domains[0]}"
    else:
        # 除外検索(-site:)はAPI側で行わず、Pythonのフィルタに任せる
        final_query = query

    payload = {
        "q": final_query,
        "gl": "jp",
        "hl": "ja",
        "num": 20
    }
    
    headers = {
        'X-API-KEY': api_key.strip(),
        'Content-Type': 'application/json'
    }
    
    response = requests.post(url, headers=headers, json=payload, timeout=20)
    
    if response.status_code != 200:
        raise Exception(f"Serper API エラー (HTTP {response.status_code}): {response.text}")
        
    data = response.json()
    
    results = []
    for item in data.get("organic", []):
        results.append({
            "title": item.get("title", ""),
            "url": item.get("link", ""),
            "snippet": item.get("snippet", "")
        })
    return results

# ==========================================
# 公式候補スコア
# ==========================================
def score_official_candidate(company: str, result: dict):
    title = result.get("title", "")
    snippet = result.get("snippet", "")
    url = result.get("url", "")
    title_lower = title.lower()
    snippet_lower = snippet.lower()
    url_lower = url.lower()

    score = 0
    if normalize_company_name(company).lower() in title_lower:
        score += 25
    if normalize_company_name(company).lower() in snippet_lower:
        score += 15

    relation = candidate_entity_relation(company, title + "\n" + snippet)
    if relation == "match":
        score += 30
    elif relation == "mismatch":
        score -= 100

    official_words = [
        "会社概要", "会社情報", "企業情報", "企業概要",
        "company profile", "corporate profile", "about us", "about", "profile", "outline", "corporate", "company"
    ]
    for word in official_words:
        if word.lower() in title_lower:
            score += 50

    official_paths = [
        "/company", "/corporate", "/about", "/about-us", "/about_us", "/profile", "/outline", "company.html", "about.html", "profile.html"
    ]
    for path in official_paths:
        if path in url_lower:
            score += 50

    parsed_url = urlparse(url)
    if parsed_url.path in ["", "/", "/index.html", "/index.php"]:
        score -= 10

    domain = extract_domain(url)
    if is_excluded_domain(domain):
        score -= 100

    return score

# ==========================================
# 公式候補取得
# ==========================================
def find_official_candidates(company: str, results: list):
    candidates = []
    for result in results:
        url = result.get("url", "")
        title = result.get("title", "")
        snippet = result.get("snippet", "")
        domain = extract_domain(url)

        # Python側で確実にスパムサイトを弾く
        if not domain or is_excluded_domain(domain):
            continue

        candidate_text = title + "\n" + snippet
        relation = candidate_entity_relation(company, candidate_text)

        if relation == "mismatch":
            continue

        score = score_official_candidate(company, result)
        candidates.append({
            "score": score,
            "domain": domain,
            "title": title,
            "url": url,
            "snippet": snippet,
            "entity_relation": relation
        })

    candidates.sort(key=lambda x: x["score"], reverse=True)
    unique_candidates = []
    seen_urls = set()

    for candidate in candidates:
        url = candidate["url"]
        if url in seen_urls:
            continue
        seen_urls.add(url)
        unique_candidates.append(candidate)

    return unique_candidates[:10]

# ==========================================
# 会社検索
# ==========================================
def search_company(company: str, api_key: str):
    
    # ① Q1: 会社概要の検索
    q1 = f'"{company}" 会社概要'
    q1_results = fetch_serper_results(q1, api_key)

    official_candidates = find_official_candidates(company, q1_results)
    
    best_domain = None
    if official_candidates:
        best_domain = official_candidates[0]["domain"]

    # ② Q2: 九州拠点の検索
    q2_results = []
    if best_domain:
        info = parse_legal_entity(company)
        core_name = info["core"] if info["core"] else company

        # ★無料枠制限の回避: 「()」と「OR」を排除し、Googleのセマンティック検索に任せる
        q2_keywords = f'{core_name} 九州 福岡 拠点 支社 支店 営業所 事業所 工場'
        
        raw_q2_results = fetch_serper_results(q2_keywords, api_key, include_domains=[best_domain])

        for r in raw_q2_results:
            domain = extract_domain(r["url"])
            if domain and (domain == best_domain or domain.endswith("." + best_domain)):
                q2_results.append(r)

    return {
        "q1_results": q1_results,
        "q2_results": q2_results,
        "official_candidates": official_candidates
    }

# ==========================================
# JSONパース
# ==========================================
def safe_parse_json(text):
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        text = text.replace("```json", "").replace("
