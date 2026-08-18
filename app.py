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
        text = text.replace("```json", "").replace("```", "").strip()
        match = re.search(r"\[.*\]|\{.*\}", text, re.DOTALL)
        if match:
            return json.loads(match.group(0))
        raise

# ==========================================
# Gemini分析
# ==========================================
def analyze_companies_batch(batch_data, gemini_key):
    client = genai.Client(api_key=gemini_key)
    prompt_targets = ""

    for i, item in enumerate(batch_data):

        q1_text = "\n".join(
            [
                (
                    f"- タイトル: {r.get('title', '')}\n"
                    f"  URL: {r.get('url', '')}\n"
                    f"  内容: {r.get('snippet', '')}\n"
                    f"  システム判定: {r.get('entity_relation', 'unknown')}"
                )
                for r in item.get("q1_results", [])[:20]
            ]
        )

        q2_text = "\n".join(
            [
                (
                    f"- タイトル: {r.get('title', '')}\n"
                    f"  URL: {r.get('url', '')}\n"
                    f"  内容: {r.get('snippet', '')}"
                )
                for r in item.get("q2_results", [])[:15]
            ]
        )

        candidates_text = json.dumps(
            item.get("official_candidates", []),
            ensure_ascii=False,
            indent=2
        )

        prompt_targets += (
            f"\n=== 対象企業 {i + 1} ===\n"
            f"【入力会社名】\n{item['company']}\n\n"
            f"【公式サイト候補】\n{candidates_text}\n\n"
            f"【Q1検索結果（会社概要用）】\n{q1_text if q1_text else 'なし'}\n\n"
            f"【Q2検索結果（公式ドメイン内 九州拠点用）】\n{q2_text if q2_text else '公式サイト内に該当する拠点ページが見つかりませんでした'}\n"
        )

    prompt = f"""
あなたは企業情報調査とDX営業提案の専門家です。

提供された検索結果だけを使って判断してください。
情報を推測・補完してはいけません。


==================================================
【最重要：正式法人名の照合】
==================================================
入力会社名と検索結果に現れる法人名を照合してください。
法人格の種類や位置（前株・後株）が違う場合、同じコア名称でも「✕ 不一致」です。


==================================================
【official_url】
==================================================
official_urlには、入力会社名の対象法人自身の「会社概要ページ」を記載してください。
【厳格な優先順位】
1. 会社概要・企業情報ページ（URLに /company, /about などが含まれるもの）を最優先。
2. トップページ（/ 終わり）は他にない場合のみ。
3. 採用、製品、ニュース、IRページは選ばない。


==================================================
【九州拠点】（厳密な抽出）
==================================================
Q1検索結果およびQ2検索結果から、入力会社名と完全に同一の法人が直接保有している九州地方の拠点名（支社、支店、営業所、事業所、工場、事業部、Hubなど）を抽出してください。

【厳格な禁止ルール】
- 住所（都道府県名、市区町村、番地）、ビル名、階数（〇F）、電話番号などは「絶対に」出力しないでください。純粋な「拠点名のみ」を抽出してください。
- 検索エンジンの抜粋の都合で「拠点名がなく、住所しか記載されていない」場合は、絶対に推測せず、空配列 [] を設定してください。
  （ダメな例：「福岡県北九州市小倉北区... Z121ビル3Ｆ」「佐賀県佐賀市駅南本町1番33号」）
  （良い例：「九州支社」「福岡営業所」「Fukuoka Hub」「法人事業部 福岡」）
- 子会社、関連会社の拠点は絶対に除外してください。
- 小売店舗、代理店、販売店も除外してください。
- 該当拠点がない場合、または別法人のものしかない場合は空配列 [] を設定してください。


==================================================
【company_match】
==================================================
"〇 一致" （正式法人名を確認でき、入力会社名と完全に一致）
"✕ 不一致" （法人格・法人格の位置・法人名が違う）
"⚠️確認できず" （正式法人名が確認できない）


==================================================
【部署別IT提案】
==================================================
対象企業の事業内容を踏まえ、IT営業で提案できるITツールを、1部署につき3個。


==================================================
【特記事項】
==================================================
直近３年間の社名変更のみ。なければ[]。


{prompt_targets}


==================================================
【JSON】
==================================================
[
  {{
    "company": "入力会社名",
    "official_url": "https://...",
    "company_match": "〇 一致",
    "kyushu_branches": ["九州支社", "熊本営業所", "Fukuoka Hub"],
    "department_keywords": [
      {{
        "department": "営業部",
        "keywords": ["SFA導入", "顧客管理DX", "商談進捗管理"]
      }}
    ],
    "notes": []
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
        "会社名リストを入力（スプレッドシートからそのまま貼り付け可能）",
        placeholder="株式会社○○○○\n株式会社△△△",
        height=180
    )
    submit_button = st.form_submit_button("一括検索・分析を実行", type="primary")

# ==========================================
# 実行
# ==========================================
if submit_button:
    if not raw_input.strip() or raw_input.strip() == "株式会社○○○○":
        st.warning("会社名を入力してください。")
    elif not serper_api_key or not gemini_key:
        st.error("Streamlitの Secrets に SERPER_API_KEY または GEMINI_API_KEY が設定されていません。")
    else:
        # キャッシュを強制リセットして再検索を確実に行う
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
            # ==================================
            # Serper API (エラーを確実に画面表示)
            # ==================================
            status.text("会社概要および九州拠点を検索中... (Googleエンジンで高速抽出中)")
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
                            "official_candidates": []
                        })
                    completed += 1
                    progress.progress((completed / len(companies_to_fetch)) * 0.4)

            # ==================================
            # Gemini (マルチスレッド並列処理)
            # ==================================
            status.text("AIによる会社概要・社名照合中...")
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

            # ==================================
            # 最終整形
            # ==================================
            status.text("結果を整形中...")
            for company in companies_to_fetch:
                fetched_item = next((item for item in fetched_data if item["company"] == company), None)
                if fetched_item is None:
                    fetched_item = {"q1_results": [], "q2_results": [], "official_candidates": []}

                result = company_map.get(company, {})

                official_url = result.get("official_url")
                if official_url in ["", "null"]:
                    official_url = None

                company_match = result.get("company_match", "⚠️確認できず")
                if company_match not in ["〇 一致", "✕ 不一致", "⚠️確認できず"]:
                    company_match = "⚠️確認できず"

                kyushu_branches = result.get("kyushu_branches", [])
                if not isinstance(kyushu_branches, list):
                    kyushu_branches = []
                kyushu_text = "、".join(str(x) for x in kyushu_branches if str(x).strip()) if kyushu_branches else "なし"

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

                    department_summary.append(f"【{department}】 " + " / ".join(keywords))

                department_text = "\n".join(department_summary)

                notes = result.get("notes", [])
                if not isinstance(notes, list):
                    notes = []
                notes_text = ", ".join(str(x) for x in notes)

                final_row = {
                    "会社名": company,
                    "会社概要URL": official_url,
                    "社名判定": company_match,
                    "九州拠点": kyushu_text,
                    "部署別IT提案": department_text,
                    "特記事項": notes_text,
                    "_raw_keywords": department_keywords,
                    "_raw_notes": notes_text,
                    "_q1_results": fetched_item.get("q1_results", []),
                    "_q2_results": fetched_item.get("q2_results", []),
                    "_official_candidates": fetched_item.get("official_candidates", [])
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
    st.subheader("検索・分析結果一覧")

    df_display = pd.DataFrame(results)
    expected_columns = ["会社名", "会社概要URL", "社名判定", "九州拠点", "部署別IT提案", "特記事項"]

    for col in expected_columns:
        if col not in df_display.columns:
            df_display[col] = ""

    df_display = df_display[expected_columns]

    st.dataframe(
        df_display,
        column_config={
            "会社概要URL": st.column_config.LinkColumn(
                "会社概要URL",
                help="会社概要ページを開きます"
            )
        },
        use_container_width=True
    )

    # ======================================
    # TSV
    # ======================================
    tsv_text = df_display.to_csv(sep="\t", index=False)
    with st.expander("スプレッドシート用の一括コピー（タブ区切りテキスト）"):
        st.code(tsv_text, language="text")

    # ======================================
    # CSV
    # ======================================
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

            if row["社名判定"] == "〇 一致":
                st.success("社名判定: 〇 一致")
            elif row["社名判定"] == "✕ 不一致":
                st.error("社名判定: ✕ 不一致")
            else:
                st.warning("社名判定: ⚠️確認できず")

            if row.get("九州拠点") and row["九州拠点"] != "なし":
                st.info(f"**九州拠点:** {row['九州拠点']}")
            else:
                st.write("**九州拠点:** なし")

            if row.get("_raw_keywords"):
                st.markdown("**部署別IT提案:**")
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

            if row.get("_raw_notes"):
                st.info(f"**特記事項:** {row['_raw_notes']}")

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
