import streamlit as st
import json
import os
import re
import pandas as pd
from urllib.parse import urlparse
from tavily import TavilyClient
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
tavily_api_key = (
    os.getenv("TAVILY_API_KEY")
    or st.secrets.get("TAVILY_API_KEY", "")
)

gemini_key = (
    os.getenv("GEMINI_API_KEY")
    or st.secrets.get("GEMINI_API_KEY", "")
)

# ==========================================
# セッションステート
# ==========================================
if "search_history" not in st.session_state:
    st.session_state.search_history = []

if "result_cache" not in st.session_state:
    st.session_state.result_cache = {}

# ==========================================
# 法人格一覧
# ==========================================
LEGAL_FORMS = [
    "株式会社",
    "有限会社",
    "合同会社",
    "合資会社",
    "合名会社",
    "一般社団法人",
    "一般財団法人",
    "公益社団法人",
    "公益財団法人",
    "学校法人",
    "医療法人",
    "社会福祉法人",
    "宗教法人",
    "特定非営利活動法人",
    "NPO法人",
    "独立行政法人",
    "国立大学法人",
    "地方独立行政法人",
    "相互会社",
    "信用金庫",
    "信用組合",
]

# ==========================================
# 第三者サイト除外
# ==========================================
def is_excluded_domain(domain: str):

    if not domain:
        return True

    excluded_domains = [
        "wikipedia.org", "yahoo.co.jp", "news.yahoo.co.jp", "nikkei.com",
        "toyokeizai.net", "mynavi.jp", "rikunabi.com", "en-japan.com",
        "wantedly.com", "indeed.com", "onecareer.jp", "doda.jp",
        "bizreach.jp", "green-japan.com", "metoree.com", "navitime.co.jp",
        "irbank.net", "xn--pckua2a7gp15o89zb.com", "pr.mono.ipros.com", 
        "ipros.com", "atengineer.com", "baseconnect.in", "houjin.jp", "prtimes.jp"
    ]

    return any(
        domain == excluded
        or domain.endswith("." + excluded)
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

    # 前株・後株の逆パターンを厳密排除
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
            and (
                (input_info["position"] == "front" and has_front)
                or
                (input_info["position"] == "back" and has_back)
            )
        ):
            return "match"

        return "mismatch"

    return "unknown"

# ==========================================
# Tavily検索 (ドメイン絞り込み対応)
# ==========================================
def fetch_tavily_results(query: str, api_key: str, include_domains: list = None):
    try:
        client = TavilyClient(api_key=api_key)
        exclude_list = [
            "wikipedia.org", "yahoo.co.jp", "news.yahoo.co.jp", "nikkei.com",
            "toyokeizai.net", "mynavi.jp", "rikunabi.com", "en-japan.com",
            "wantedly.com", "indeed.com", "onecareer.jp", "doda.jp",
            "bizreach.jp", "green-japan.com", "metoree.com", "navitime.co.jp",
            "irbank.net", "xn--pckua2a7gp15o89zb.com", "pr.mono.ipros.com", 
            "ipros.com", "atengineer.com", "baseconnect.in", "houjin.jp", "prtimes.jp"
        ]

        params = {
            "query": query.strip().replace("`", ""),
            "search_depth": "basic",
            "max_results": 20,
        }
        
        # ドメインが指定されていれば追加、指定されていなければ除外リストを使用
        if include_domains:
            params["include_domains"] = include_domains
        else:
            params["exclude_domains"] = exclude_list

        response = client.search(**params)

        results = []
        for item in response.get("results", []):
            results.append({
                "title": item.get("title", ""),
                "url": item.get("url", ""),
                "snippet": item.get("content", "")
            })

        return results
    except Exception as e:
        st.error(f"Tavily検索エラー: {str(e)}")
        st.exception(e)
        return []

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
# 会社検索 (Q1:会社概要, Q2:九州拠点 - 厳密ドメイン検索)
# ==========================================
def search_company(company: str, api_key: str):
    
    # ① Q1: 会社概要の検索（Web全体から公式サイトを探す）
    q1 = f'"{company}" 会社概要'
    q1_results = fetch_tavily_results(q1, api_key)

    # 公式サイト候補を抽出
    official_candidates = find_official_candidates(company, q1_results)
    
    target_domains = None
    if official_candidates:
        # 一番スコアの高い公式サイトのドメインを取得
        best_domain = official_candidates[0]["domain"]
        target_domains = [best_domain]

    # ② Q2: 九州拠点の検索（特定した公式サイト内のみを検索）
    # ドメイン内で絞り込んでいるため、会社名を入れずに拠点名だけで探す
    q2_keywords = "九州 OR 福岡 OR 佐賀 OR 長崎 OR 熊本 OR 大分 OR 宮崎 OR 鹿児島 拠点 OR 支社 OR 支店 OR 営業所 OR 事業所 OR 事業部 OR 工場"
    
    if target_domains:
        # 公式ドメイン内のみで検索を実行（求人サイトなどのノイズを完全排除）
        q2_results = fetch_tavily_results(q2_keywords, api_key, include_domains=target_domains)
    else:
        # 公式サイトが特定できなかった場合は、ノイズ混入を防ぐためQ2は実行しない
        q2_results = []

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
            f"【Q2検索結果（公式ドメイン内 九州拠点用）】\n{q2_text if q2_text else '公式サイト特定不可のため未実行'}\n"
        )

    prompt = f"""
あなたは企業情報調査とDX営業提案の専門家です。

提供された検索結果だけを使って判断してください。
情報を推測・補完してはいけません。


==================================================
【最重要：正式法人名の照合】
==================================================

入力会社名と検索結果に現れる法人名を照合してください。

法人格は必ず考慮してください。

例えば、
株式会社ニデック
ニデック株式会社
は別法人です。

また、
株式会社〇〇
合同会社〇〇
〇〇株式会社
なども別法人です。

法人格の種類や位置（前株・後株）が違う場合、同じコア名称でも「✕ 不一致」です。


==================================================
【official_url】
==================================================

official_urlには、入力会社名の対象法人自身の「会社概要ページ」を記載してください。

【厳格な優先順位】
1. 会社概要・企業情報ページ（URLに /company, /about などが含まれるもの）を最優先。
2. トップページ（/ 終わり）は、他に会社概要ページが見当たらない場合のみ選ぶ。
3. 採用ページ（/recruit）、製品ページ（/product, /service）、ニュース、IRページは絶対に選ばないでください。

重要：対象法人自身の公式サイトを選んでください。Wikipediaや求人サイト、企業DBなどは除外してください。


==================================================
【九州拠点】（厳密な抽出）
==================================================

Q1検索結果（会社概要）および Q2検索結果（公式ドメイン内検索）の両方から、入力会社名と「完全に同一の法人」が直接保有している、九州地方（福岡、佐賀、長崎、熊本、大分、宮崎、鹿児島）の拠点（支社、支店、営業所、事業所、工場、事業部など）の名前を抽出してください。

【厳格なルール】
- 子会社、関連会社、グループ会社の拠点は「絶対に」除外してください。
  （例：入力が「株式会社ニトリ」の場合、「株式会社ニトリファシリティーズ」の拠点はNG）
- 一般向けの小売店舗、代理店、販売店などの情報も除外してください。
- 該当する拠点がない場合、または別法人の拠点しか見つからない場合は空配列 [] を設定してください。
- 見つかった場合は、記載されている拠点名（例：「福岡支店」「熊本工場」「法人事業部 福岡」など）を文字列の配列として出力してください。


==================================================
【company_match】
==================================================

"〇 一致" （正式法人名を確認でき、入力会社名と完全に一致）
"✕ 不一致" （法人格・法人格の位置・法人名が違う）
"⚠️確認できず" （正式法人名が確認できない）


==================================================
【部署別IT提案】
==================================================

対象企業の事業内容を踏まえ、IT営業で提案できる部署を最大4つ。1部署につき3～4個。
単なる事業内容ではなく、その部署に何をIT提案するかを書いてください。


==================================================
【特記事項】
==================================================

2023年8月14日以降の重要事項のみ（社名変更、M&A、組織再編、新規事業など）。なければ[]。


{prompt_targets}


==================================================
【JSON】
==================================================

[
  {{
    "company": "入力会社名",
    "official_url": "https://...",
    "company_match": "〇 一致",
    "kyushu_branches": ["福岡支店", "熊本営業所", "法人事業部 福岡"],
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
        st.error(f"AI分析バッチ処理エラー: {str(e)}")
        st.exception(e)
        return []

# ==========================================
# UI
# ==========================================
with st.form(key="batch_search_form"):
    raw_input = st.text_area(
        "会社名リストを入力（スプレッドシートからそのまま貼り付け可能）",
        placeholder="株式会社ニデック\nアステラス製薬株式会社\n株式会社ニトリ",
        height=180
    )
    submit_button = st.form_submit_button("一括検索・分析を実行", type="primary")

# ==========================================
# 実行
# ==========================================
if submit_button:
    if not raw_input.strip():
        st.warning("会社名を入力してください。")
    elif not tavily_api_key or not gemini_key:
        st.error("Streamlitの Secrets に TAVILY_API_KEY または GEMINI_API_KEY が設定されていません。")
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

        progress = st.progress(0)
        status = st.empty()

        # ==================================
        # Tavily
        # ==================================
        status.text("会社概要および九州拠点を検索中...")
        fetched_data = []

        for i, company in enumerate(company_list):
            search_data = search_company(company, tavily_api_key)
            fetched_data.append({
                "company": company,
                "q1_results": search_data["q1_results"],
                "q2_results": search_data["q2_results"],
                "official_candidates": search_data["official_candidates"]
            })
            progress.progress(((i + 1) / max(len(company_list), 1)) * 0.5)

        # ==================================
        # Gemini
        # ==================================
        status.text("AIによる会社概要・社名照合中...")
        company_map = {}
        chunk_size = 5

        for start in range(0, len(fetched_data), chunk_size):
            chunk = fetched_data[start:start + chunk_size]
            response_list = analyze_companies_batch(chunk, gemini_key)

            if isinstance(response_list, list):
                for result in response_list:
                    company = result.get("company")
                    if company:
                        company_map[company] = result

            progress.progress(0.5 + ((start + len(chunk)) / max(len(fetched_data), 1)) * 0.5)

        # ==================================
        # 最終整形
        # ==================================
        batch_results = []

        for company in company_list:
            fetched_item = next((item for item in fetched_data if item["company"] == company), None)
            if fetched_item is None:
                fetched_item = {"q1_results": [], "q2_results": [], "official_candidates": []}

            result = company_map.get(company, {})

            # 会社概要URL
            official_url = result.get("official_url")
            if official_url in ["", "null"]:
                official_url = None

            # 社名判定
            company_match = result.get("company_match", "⚠️確認できず")
            if company_match not in ["〇 一致", "✕ 不一致", "⚠️確認できず"]:
                company_match = "⚠️確認できず"

            # 九州拠点
            kyushu_branches = result.get("kyushu_branches", [])
            if not isinstance(kyushu_branches, list):
                kyushu_branches = []
            kyushu_text = "、".join(str(x) for x in kyushu_branches if str(x).strip()) if kyushu_branches else "なし"

            # 部署別IT提案
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

            # 特記事項
            notes = result.get("notes", [])
            if not isinstance(notes, list):
                notes = []
            notes_text = ", ".join(str(x) for x in notes)

            # 保存
            batch_results.append({
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
            })

        progress.progress(1.0)
        status.text("すべての処理が完了しました。")
        st.session_state["batch_results"] = batch_results

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
            
            # --------------------------------
            # 会社概要URL
            # --------------------------------
            if row.get("会社概要URL"):
                st.markdown(f"**会社概要URL:** [{row['会社概要URL']}]({row['会社概要URL']})")
            else:
                st.write("**会社概要URL:** 確認できず")

            # --------------------------------
            # 社名判定
            # --------------------------------
            if row["社名判定"] == "〇 一致":
                st.success("社名判定: 〇 一致")
            elif row["社名判定"] == "✕ 不一致":
                st.error("社名判定: ✕ 不一致")
            else:
                st.warning("社名判定: ⚠️確認できず")

            # --------------------------------
            # 九州拠点
            # --------------------------------
            if row.get("九州拠点") and row["九州拠点"] != "なし":
                st.info(f"**九州拠点:** {row['九州拠点']}")
            else:
                st.write("**九州拠点:** なし")

            # --------------------------------
            # 部署別IT提案
            # --------------------------------
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

            # --------------------------------
            # 特記事項
            # --------------------------------
            if row.get("_raw_notes"):
                st.info(f"**特記事項:** {row['_raw_notes']}")

            # ==================================
            # デバッグ：Q1 (会社概要)
            # ==================================
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

            # ==================================
            # デバッグ：Q2 (九州拠点)
            # ==================================
            with st.expander("🔎 デバッグ：九州拠点検索結果 (Q2)"):
                q2_results = row.get("_q2_results", [])
                if not q2_results:
                    st.write("検索結果なし")
                else:
                    for idx, result in enumerate(q2_results, start=1):
                        st.markdown(f"### Q2-{idx}")
                        st.write(f"**タイトル:** {result.get('title', '')}")
                        st.write(f"**URL:** {result.get('url', '')}")
                        st.write(f"**内容:** {result.get('snippet', '')}")
                        st.divider()

            # ==================================
            # デバッグ：公式候補
            # ==================================
            with st.expander("🔎 デバッグ：公式サイト候補"):
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
