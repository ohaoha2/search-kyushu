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

serper_api_key = os.getenv("SERPER_API_KEY") or st.secrets.get("SERPER_API_KEY", "")
gemini_key = os.getenv("GEMINI_API_KEY") or st.secrets.get("GEMINI_API_KEY", "")

if "search_history" not in st.session_state:
    st.session_state.search_history = []
if "result_cache" not in st.session_state:
    st.session_state.result_cache = {}

LEGAL_FORMS = [
    "株式会社", "有限会社", "合同会社", "合資会社", "合名会社",
    "一般社団法人", "一般財団法人", "公益社団法人", "公益財団法人",
    "学校法人", "医療法人", "社会福祉法人", "宗教法人", "特定非営利活動法人",
    "NPO法人", "独立行政法人", "国立大学法人", "地方独立行政法人",
    "相互会社", "信用金庫", "信用組合",
]

# ==========================================
# 【新規】Python側で子会社・別法人拠点を自動判定・除外する関数
# ==========================================
def is_subsidiary_or_different_entity(branch_name: str, input_company: str) -> bool:
    if not branch_name or not input_company:
        return False
    
    norm_branch = normalize_company_name(branch_name)
    norm_company = normalize_company_name(input_company)
    
    # 拠点名の中に法人格（株式会社等）が含まれているか確認
    for form in LEGAL_FORMS:
        if form in norm_branch:
            # 拠点名の中に含まれる社名が入力社名（例：ニデック株式会社）と一致しない場合は子会社/別法人とみなす
            if norm_company not in norm_branch:
                return True
    return False

def is_excluded_domain(domain: str):
    if not domain:
        return True
    excluded_domains = [
        "wikipedia.org", "irbank.net", "compalyze.co.jp", "houjin.jp", 
        "xn--pckua2a7gp15o89zb.com", "baseconnect.in"
    ]
    return any(domain == excluded or domain.endswith("." + excluded) for excluded in excluded_domains)

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

def extract_main_domain(domain: str):
    """サブドメイン（holdings.panasonic等）を除いたメインの識別名を抽出"""
    if not domain:
        return ""
    parts = domain.split(".")
    if len(parts) >= 3 and parts[-2] in ["co", "com", "ne", "or", "go", "ac", "lg"]:
        return parts[-3]
    if len(parts) >= 2:
        return parts[-2]
    return domain

def normalize_company_name(name: str):
    if not name:
        return ""
    name = str(name)
    name = re.sub(r"[\s ]+", "", name)
    return name.strip()

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
        if form == input_form and ((input_info["position"] == "front" and has_front) or (input_info["position"] == "back" and has_back)):
            return "match"
        return "mismatch"
    return "unknown"

def fetch_serper_results(query: str, api_key: str):
    url = "https://google.serper.dev/search"
    payload = {"q": query, "gl": "jp", "hl": "ja", "num": 40}
    headers = {'X-API-KEY': api_key.strip(), 'Content-Type': 'application/json'}
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

def scrape_page_text(url: str):
    try:
        headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'}
        response = requests.get(url, headers=headers, timeout=5)
        response.encoding = response.apparent_encoding
        if response.status_code == 200:
            html = response.text
            html = re.sub(r'<(script|style)[^>]*>.*?</\1>', ' ', html, flags=re.DOTALL | re.IGNORECASE)
            text = re.sub(r'<[^>]+>', ' ', html)
            text = re.sub(r'\s+', ' ', text).strip()
            return text[:4000]
    except Exception:
        pass
    return ""

def score_official_candidate(company: str, result: dict, rank: int):
    title = result.get("title", "")
    snippet = result.get("snippet", "")
    url = result.get("url", "")
    domain = extract_domain(url)
    score = max(0, (20 - rank) * 10)

    if normalize_company_name(company).lower() in title.lower():
        score += 25
    if normalize_company_name(company).lower() in snippet.lower():
        score += 15

    relation = candidate_entity_relation(company, title + "\n" + snippet)
    if relation == "match":
        score += 30
    elif relation == "mismatch":
        score -= 100

    official_words = ["会社概要", "会社情報", "企業情報", "企業概要", "company profile", "corporate profile", "about us", "about", "profile", "outline", "corporate", "company"]
    for word in official_words:
        if word.lower() in title.lower():
            score += 50

    official_paths = ["/company", "/corporate", "/about", "/about-us", "/about_us", "/profile", "/outline", "company.html", "about.html", "profile.html"]
    for path in official_paths:
        if path in url.lower():
            score += 50

    parsed_url = urlparse(url)
    if parsed_url.path in ["", "/", "/index.html", "/index.php"]:
        score -= 10

    spam_domains = [
        "metoree.com", "doda.jp", "mynavi.jp", "rikunabi.com", "en-japan.com",
        "salesnow.jp", "syukatsu-kaigi.jp", "jobtalk.jp", "openwork.jp", "en-hyouban.com",
        "prtimes.jp", "mapion.co.jp", "navitime.co.jp", "bizmaps.jp", "nikkei.com",
        "yahoo.co.jp", "toyokeizai.net", "atengineer.com", "ipros.com"
    ]
    for spam in spam_domains:
        if domain and spam in domain:
            score -= 100
    return score

def find_official_candidates(company: str, results: list):
    candidates = []
    for idx, result in enumerate(results):
        url = result.get("url", "")
        title = result.get("title", "")
        snippet = result.get("snippet", "")
        domain = extract_domain(url)
        if not domain or is_excluded_domain(domain):
            continue
        relation = candidate_entity_relation(company, title + "\n" + snippet)
        if relation == "mismatch":
            continue
        score = score_official_candidate(company, result, idx)
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
        if candidate["url"] in seen_urls:
            continue
        seen_urls.add(candidate["url"])
        unique_candidates.append(candidate)
    return unique_candidates[:10]

def search_company(company: str, api_key: str):
    q1 = f'{company} 会社概要 公式 社名変更'
    q1_results = fetch_serper_results(q1, api_key)
    official_candidates = find_official_candidates(company, q1_results)
    
    best_domain = None
    if official_candidates:
        best_domain = official_candidates[0]["domain"]

    q2_results = []
    scraped_texts = []
    
    if best_domain:
        info = parse_legal_entity(company)
        core_name = info["core"] if info["core"] else company
        
        clean_core = re.sub(r'(ホールディングス|HD)$', '', core_name)
        q2_keywords = f'{clean_core} 九州 福岡 拠点 支社 支店 営業所 事業所 Office拠点'
        raw_q2_results = fetch_serper_results(q2_keywords, api_key)

        best_main = extract_main_domain(best_domain)

        for r in raw_q2_results:
            domain = extract_domain(r["url"])
            if domain:
                main_dom = extract_main_domain(domain)
                if main_dom == best_main or domain == best_domain or domain.endswith("." + best_domain):
                    q2_results.append(r)
                
        for r in q2_results[:3]:
            url = r["url"]
            if not url.lower().endswith(".pdf"):
                scraped = scrape_page_text(url)
                if scraped:
                    scraped_texts.append(f"【{r['title']}】(URL: {url}) の直読みデータ:\n{scraped}")

    return {
        "q1_results": q1_results,
        "q2_results": q2_results,
        "official_candidates": official_candidates,
        "scraped_text": "\n\n".join(scraped_texts)
    }

def safe_parse_json(text):
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        text = text.replace("```json", "").replace("```", "").strip()
        match = re.search(r"\[.*\]|\{.*\}", text, re.DOTALL)
        if match:
            return json.loads(match.group(0))
        raise

def analyze_companies_batch(batch_data, gemini_key):
    client = genai.Client(api_key=gemini_key)
    prompt_targets = ""

    for i, item in enumerate(batch_data):
        q1_text = "\n".join([f"- タイトル: {r.get('title', '')}\n  URL: {r.get('url', '')}\n  内容: {r.get('snippet', '')}\n  システム判定: {r.get('entity_relation', 'unknown')}" for r in item.get("q1_results", [])[:20]])
        q2_text = "\n".join([f"- タイトル: {r.get('title', '')}\n  URL: {r.get('url', '')}\n  内容: {r.get('snippet', '')}" for r in item.get("q2_results", [])[:15]])
        candidates_text = json.dumps(item.get("official_candidates", []), ensure_ascii=False, indent=2)

        prompt_targets += (
            f"\n=== 対象企業 {i + 1} ===\n"
            f"【入力会社名】\n{item['company']}\n\n"
            f"【公式サイト候補】\n{candidates_text}\n\n"
            f"【Q1検索結果（会社概要・社名変更用）】\n{q1_text if q1_text else 'なし'}\n\n"
            f"【Q2検索結果（公式ドメイン内 九州拠点用）】\n{q2_text if q2_text else '公式サイト内に該当する拠点ページが見つかりませんでした'}\n\n"
            f"【Q2ページ本文 直読みデータ（詳細拠点情報）】\n{item.get('scraped_text', '取得失敗 または 該当ページなし')}\n"
        )

    # ★ プロンプトはご指示通り一切変更していません ★
    prompt = f"""
あなたは企業情報調査とDX営業提案の専門家です。
提供された検索結果（およびページ直読みデータ）だけを使って判断してください。
【重要】検索結果テキスト内に明記されていない事実・日付を推測や計算で算出して補完することは厳禁です。

==================================================
【official_url】
==================================================
入力会社名の対象法人自身の「会社概要ページ」を記載してください。
【優先順位】
URLのパスに /company, /about, /corporate, /profile などが含まれる「会社概要・企業情報ページ」を最優先で選ぶこと。トップページ（/ 終わり）は他になければ選ぶ。

==================================================
【九州拠点】（厳密な抽出とURLの紐付け）
==================================================
検索結果の中から、対象法人が直接保有している九州地方の拠点名（支社、支店、営業所、工場など）を抽出してください。
抽出した拠点ごとに、情報が記載されていたページの「URL」もセットで出力してください。

【厳格な禁止ルール】
- 住所、ビル名、階数、電話番号などは出力しないでください。「拠点名のみ」を抽出してください。拠点名が不明な場合は、住所でを出力してください。
- 拠点名がなく住所しか記載されていない場合は、推測せず空配列 [] を設定してください。
- 【最重要：子会社・グループ会社の徹底除外】
  入力会社名と完全一致する会社名の拠点のみを抽出してください。子会社やグループ会社の拠点は、絶対に抽出しないでください。
- 小売店舗そのもの（販売店）は除外してください。ただし店舗内に「法人事業部」等がある場合は抽出可。

==================================================
【company_match】（社名判定の厳格ルール）
==================================================
1. 検索結果の会社概要から対象企業の【現在の最新の正式法人名】を特定する。
2. 【入力会社名】と【現在の最新の正式法人名】を比較する。

・【入力会社名】が【現在の最新の正式法人名】と一致している場合（表記の揺れやスペース差含む）：
  → **絶対に「〇」** とだけ出力してください。
  ※過去に社名変更があった企業であっても、入力された名前が現在の正式社名であれば、判定は100%「〇」です。

・【入力会社名】が『過去の旧社名』であり、現在は別の新社名に変更されている場合のみ：
  → **「[変更年月日] 「現在の新社名」へ変更」** と出力してください。
  （例：入力社名が「旧社名株式会社」の場合 → 「2023年4月1日に「新社名株式会社」へ変更」）
 日付がわからない場合は、「「現在の新社名」へ変更」のみを出力。日付の推測や、実在しない日付の生成を厳禁とする。
 旧社名と新社名を絶対に混同しないでください。
 
・正式法人名が確認できない場合 → 「確認できず」

==================================================
【部署別IT】
==================================================
対象企業の事業内容を踏まえ、IT営業で提案できるITツールを、1部署につき3個。

{prompt_targets}

==================================================
【JSON】
==================================================
[
  {{
    "company": "入力会社名",
    "official_url": "https://.../company/",
    "company_match": "〇 一致",
    "kyushu_branches": [
      {{
        "name": "九州支社",
        "url": "https://..."
      }}
    ],
    "department_keywords": [
      {{
        "department": "営業部",
        "keywords": ["SFA導入", "顧客管理DX", "商談進捗管理"]
      }}
    ]
  }}
]
"""

    try:
        response = client.models.generate_content(
            model="gemini-3.5-flash-lite",
            contents=prompt,
            config=types.GenerateContentConfig(
                response_mime_type="application/json"
            )
        )
        return safe_parse_json(response.text.strip())
    except Exception as e:
        st.error(f"AI分析エラー: {str(e)}")
        return []

# ==========================================
# UI
# ==========================================
with st.form(key="batch_search_form"):
    raw_input = st.text_area(
        "会社名リストを入力",
        placeholder="株式会社○○○○\n株式会社△△△",
        height=180
    )
    submit_button = st.form_submit_button("検索", type="primary")

# ==========================================
# 実行
# ==========================================
if submit_button:
    if not raw_input.strip() or raw_input.strip() == "株式会社○○○○":
        st.warning("会社名を入力してください。")
    elif not serper_api_key or not gemini_key:
        st.error("Streamlitの Secrets に SERPER_API_KEY または GEMINI_API_KEY が設定されていません。")
    else:
        st.session_state.result_cache = {}
        st.session_state.pop("batch_results", None)

        lines = raw_input.strip().split("\n")
        company_list = []
        for line in lines:
            parts = line.split("\t")
            company = parts[0].strip()
            if company and company not in company_list:
                company_list.append(company)

        companies_to_fetch = company_list
        final_results = []
        
        progress = st.progress(0)
        status = st.empty()

        if companies_to_fetch:
            status.text("検索中...")
            fetched_data = []

            def fetch_wrapper(comp):
                data = search_company(comp, serper_api_key)
                return {"company": comp, **data}

            with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
                futures = {executor.submit(fetch_wrapper, comp): comp for comp in companies_to_fetch}
                completed = 0
                for future in concurrent.futures.as_completed(futures):
                    comp_name = futures[future]
                    try:
                        fetched_data.append(future.result())
                    except Exception as e:
                        st.error(f"【検索失敗】 {comp_name}のデータ取得中にエラーが発生しました: {str(e)}")
                        fetched_data.append({
                            "company": comp_name,
                            "q1_results": [],
                            "q2_results": [],
                            "official_candidates": [],
                            "scraped_text": ""
                        })
                    completed += 1
                    progress.progress((completed / len(companies_to_fetch)) * 0.4)

            status.text("分析中...")
            company_map = {}
            chunk_size = 5
            chunks = [fetched_data[i:i + chunk_size] for i in range(0, len(fetched_data), chunk_size)]

            def gemini_wrapper(chunk):
                return analyze_companies_batch(chunk, gemini_key)

            with concurrent.futures.ThreadPoolExecutor(max_workers=3) as executor:
                futures = [executor.submit(gemini_wrapper, chunk) for chunk in chunks]
                completed_chunks = 0
                for future in concurrent.futures.as_completed(futures):
                    try:
                        res_list = future.result()
                        if isinstance(res_list, list):
                            for r in res_list:
                                comp = r.get("company")
                                if comp:
                                    company_map[comp] = r
                    except Exception as e:
                        st.error(f"【AI分析エラー】: {str(e)}")
                    completed_chunks += 1
                    progress.progress(0.4 + (completed_chunks / len(chunks)) * 0.5)

            status.text("結果を整形中...")
            for company in companies_to_fetch:
                fetched_item = next((item for item in fetched_data if item["company"] == company), None)
                if fetched_item is None:
                    fetched_item = {"q1_results": [], "q2_results": [], "official_candidates": [], "scraped_text": ""}

                result = company_map.get(company, {})

                official_url = result.get("official_url")
                if official_url in ["", "null"]:
                    official_url = None

                company_match = str(result.get("company_match", "⚠️確認できず")).strip()
                company_match = company_match.replace("**", "").replace("`", "").strip()

                # ★【Python側自動補正】入力社名＝現在の新社名の場合にAIが「✕ 変更」とした場合を「〇」に強制救済
                norm_input = normalize_company_name(company)
                match_new_name = re.search(r"[『「](.*?)[』」]", company_match)
                if match_new_name:
                    extracted_new_name = normalize_company_name(match_new_name.group(1))
                    if extracted_new_name and (extracted_new_name == norm_input or candidate_entity_relation(company, extracted_new_name) == "match"):
                        company_match = "〇"

                kyushu_branches = result.get("kyushu_branches", [])
                if not isinstance(kyushu_branches, list):
                    kyushu_branches = []
                
                branch_md_list = []
                for b in kyushu_branches:
                    b_name = ""
                    b_url = ""
                    if isinstance(b, dict):
                        b_name = b.get("name", "").strip()
                        b_url = b.get("url", "").strip()
                    elif isinstance(b, str):
                        b_name = b.strip()

                    if not b_name:
                        continue

                    # ★【Python側自動フィルタ】子会社・別法人拠点を強制除外
                    if is_subsidiary_or_different_entity(b_name, company):
                        continue

                    if b_url and b_url.lower() != "null":
                        branch_md_list.append(f"[{b_name}]({b_url})")
                    else:
                        branch_md_list.append(b_name)

                kyushu_text = "\n".join(branch_md_list) if branch_md_list else "なし"

                department_keywords = result.get("department_keywords", [])
                if not isinstance(department_keywords, list):
                    department_keywords = []

                department_summary = []
                for item in department_keywords:
                    if not isinstance(item, dict):
                        continue
                    department = str(item.get("department", "")).strip()
                    keywords = item.get("keywords", [])
                    
                    if not department:
                        continue
                    if not isinstance(keywords, list):
                        keywords = []

                    keywords = [str(x) for x in keywords if str(x).strip()]
                    if not keywords:
                        continue

                    department_summary.append(f"【{department}】\n" + "\n".join(f"・{kw}" for kw in keywords))

                department_text = "\n\n".join(department_summary)

                final_row = {
                    "会社名": company,
                    "会社概要URL": official_url,
                    "社名判定": company_match,
                    "九州拠点": kyushu_text,
                    "部署別IT": department_text,
                    "_raw_keywords": department_keywords,
                    "_q1_results": fetched_item.get("q1_results", []),
                    "_q2_results": fetched_item.get("q2_results", []),
                    "_official_candidates": fetched_item.get("official_candidates", []),
                    "_scraped_text": fetched_item.get("scraped_text", "")
                }
                
                st.session_state.result_cache[company] = final_row
                final_results.append(final_row)

        progress.progress(1.0)
        status.text("すべての処理が完了しました。")

        ordered_results = []
        for comp in company_list:
            row = next((r for r in final_results if r["会社名"] == comp), None)
            if row:
                ordered_results.append(row)

        st.session_state["batch_results"] = ordered_results

# ==========================================
# 結果表示
# ==========================================
if "batch_results" in st.session_state and st.session_state["batch_results"]:

    results = st.session_state["batch_results"]

    st.divider()
    st.subheader("検索結果一覧")

    df_display = pd.DataFrame(results)
    expected_columns = ["会社名", "会社概要URL", "社名判定", "九州拠点", "部署別IT"]

    for col in expected_columns:
        if col not in df_display.columns:
            df_display[col] = ""

    df_display = df_display[expected_columns]

    md_table = "| 会社名 | 会社概要URL | 社名判定 | 九州拠点 | 部署別IT |\n"
    md_table += "|---|---|---|---|---|\n"
    
    for row in results:
        company_md = row.get("会社名", "").replace("\n", " ")
        url = row.get("会社概要URL")
        url_md = f"[リンク]({url})" if url else "確認できず"
        match_md = row.get("社名判定", "")
        
        kyushu_md = str(row.get("九州拠点", "")).replace("\n", "<br>")
        it_prop_md = str(row.get("部署別IT", "")).replace("\n", "<br>")
        
        md_table += f"| {company_md} | {url_md} | {match_md} | {kyushu_md} | {it_prop_md} |\n"

    st.markdown(md_table, unsafe_allow_html=True)

    # ======================================
    # TSV / CSV
    # ======================================
    st.write("---")
    tsv_text = df_display.to_csv(sep="\t", index=False)
    with st.expander("コピー用"):
        st.code(tsv_text, language="text")

    csv_data = df_display.to_csv(index=False).encode("utf-8-sig")
    st.download_button(
        label="結果をCSVでダウンロード",
        data=csv_data,
        file_name="corporate_search_results.csv",
        mime="text/csv",
        type="primary"
    )

    # ======================================
    # カード
    # ======================================
    st.divider()
    st.subheader("各社詳細・カード表示")

    for row in results:
        with st.expander(f"{row['会社名']} ── 【{row['社名判定']}】"):
            
            if row.get("会社概要URL"):
                st.markdown(f"**会社概要URL:** [{row['会社概要URL']}]({row['会社概要URL']})")
            else:
                st.write("**会社概要URL:** 確認できず")

            match_str = str(row["社名判定"])
            if match_str == "〇" or match_str.startswith("〇"):
                st.success(f"社名判定: {match_str}")
            elif match_str.startswith("✕") or "✕" in match_str:
                st.error(f"社名判定: {match_str}")
            else:
                st.warning(f"社名判定: {match_str}")

            if row.get("九州拠点") and row["九州拠点"] != "なし":
                st.markdown(f"**九州拠点:** \n{row['九州拠点']}")
            else:
                st.write("**九州拠点:** なし")

            if row.get("_raw_keywords"):
                st.markdown("**部署別IT:**")
                for item in row["_raw_keywords"]:
                    if not isinstance(item, dict):
                        continue
                    department = item.get("department", "")
                    keywords = item.get("keywords", [])
                    if not department:
                        continue
                    st.markdown(f"**【{department}】**")
                    for keyword in keywords:
                        st.markdown(f"- {keyword}")

            with st.expander("🔎 デバッグ：会社概要・公式サイト検索結果 (Q1)"):
                q1_results = row.get("_q1_results", [])
                if not q1_results:
                    st.write("検索結果なし")
                else:
                    for idx, result in enumerate(q1_results, start=1):
                        st.markdown(f"### Q1-{idx}")
                        st.write(f"**タイトル:** {result.get('title', '')}")
                        st.write(f"**URL:** {result.get('url', '')}")
                        st.write(f"**内容:** {result.get('snippet', '')}")
                        st.divider()

            with st.expander("🔎 デバッグ：九州拠点検索結果 (Q2)"):
                q2_results = row.get("_q2_results", [])
                if not q2_results:
                    st.write("公式サイト内に該当する拠点ページが見つかりませんでした")
                else:
                    for idx, result in enumerate(q2_results, start=1):
                        st.markdown(f"### Q2-{idx}")
                        st.write(f"**タイトル:** {result.get('title', '')}")
                        st.write(f"**URL:** {result.get('url', '')}")
                        st.write(f"**内容:** {result.get('snippet', '')}")
                        st.divider()
                        
            with st.expander("🔎 デバッグ：ページ直読みデータ (スクレイピング)"):
                scraped_text = row.get("_scraped_text", "")
                if not scraped_text:
                    st.write("直読みデータの取得はありませんでした。")
                else:
                    st.write(scraped_text)

            with st.expander("🔎 デバッグ：公式候補"):
                candidates = row.get("_official_candidates", [])
                if not candidates:
                    st.write("公式サイト候補なし")
                else:
                    for candidate in candidates:
                        st.write(f"スコア: {candidate.get('score')}")
                        st.write(f"ドメイン: {candidate.get('domain')}")
                        st.write(f"タイトル: {candidate.get('title')}")
                        st.write(f"URL: {candidate.get('url')}")
                        st.write(f"法人関係判定: {candidate.get('entity_relation')}")
                        st.divider()
