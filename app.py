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
tavily_api_key = os.getenv("TAVILY_API_KEY") or st.secrets.get("TAVILY_API_KEY", "")
gemini_key = os.getenv("GEMINI_API_KEY") or st.secrets.get("GEMINI_API_KEY", "")

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
    "株式会社", "有限会社", "合同会社", "合資会社", "合名会社",
    "一般社団法人", "一般財団法人", "公益社団法人", "公益財団法人",
    "学校法人", "医療法人", "社会福祉法人", "宗教法人", "特定非営利活動法人",
    "NPO法人", "独立行政法人", "国立大学法人", "地方独立行政法人",
    "相互会社", "信用金庫", "信用組合",
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

    return any(domain == excluded or domain.endswith("." + excluded) for excluded in excluded_domains)

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
    # 全角・半角スペース除去
    name = re.sub(r"[\s ]+", "", name)
    return name.strip()

# ==========================================
# 法人格情報を抽出
# ==========================================
def parse_legal_entity(name: str):
    normalized = normalize_company_name(name)
    
    if not normalized:
        return {"original": "", "core": "", "legal_form": None, "position": "unknown"}

    # 長い法人格から先に確認
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

    # --------------------------------------
    # 【重要】前株・後株の逆パターンを厳密排除
    # --------------------------------------
    if input_core and input_form:
        opposite_company = ""
        if input_info["position"] == "front":
            opposite_company = f"{input_core}{input_form}"
        elif input_info["position"] == "back":
            opposite_company = f"{input_form}{input_core}"
        
        # 候補テキストの中に「逆位置の社名」があり、かつ「正しい社名」が無い場合は確定で別法人
        if opposite_company and (opposite_company in text) and (input_original not in text):
            return "mismatch"

    # 入力会社名そのものが書かれている
    if input_original and input_original in text:
        return "match"

    if not input_core:
        return "unknown"

    # 候補テキスト中に同じコア＋法人格が存在するか調べる
    for form in sorted(LEGAL_FORMS, key=len, reverse=True):
        front_pattern = re.escape(form) + re.escape(input_core)
        back_pattern = re.escape(input_core) + re.escape(form)

        has_front = re.search(front_pattern, text)
        has_back = re.search(back_pattern, text)

        if not has_front and not has_back:
            continue

        # 入力会社と同一法人格・同一位置
        if form == input_form and ((input_info["position"] == "front" and has_front) or (input_info["position"] == "back" and has_back)):
            return "match"

        # 同じコア名だが、法人格または位置が異なる
        return "mismatch"

    return "unknown"

# ==========================================
# Tavily検索
# ==========================================
def fetch_tavily_results(query: str, api_key: str):
    try:
        client = TavilyClient(api_key=api_key)

        # APIレベルで除外するドメインリスト
        exclude_list = [
            "wikipedia.org", "yahoo.co.jp", "news.yahoo.co.jp", "nikkei.com",
            "toyokeizai.net", "mynavi.jp", "rikunabi.com", "en-japan.com",
            "wantedly.com", "indeed.com", "onecareer.jp", "doda.jp",
            "bizreach.jp", "green-japan.com", "metoree.com", "navitime.co.jp",
            "irbank.net", "xn--pckua2a7gp15o89zb.com", "pr.mono.ipros.com", 
            "ipros.com", "atengineer.com", "baseconnect.in", "houjin.jp", "prtimes.jp"
        ]

        response = client.search(
            query=query.strip().replace("`", ""),
            search_depth="basic",
            max_results=20,
            exclude_domains=exclude_list
        )

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

    # 入力会社名そのもの
    normalized_company = normalize_company_name(company).lower()
    if normalized_company in title_lower:
        score += 25
    if normalized_company in snippet_lower:
        score += 15

    # 法人格を含む会社名候補
    relation = candidate_entity_relation(company, title + "\n" + snippet)
    if relation == "match":
        score += 30
    elif relation == "mismatch":
        score -= 100

    # 会社概要らしいタイトル
    official_words = [
        "会社概要", "会社情報", "企業情報", "企業概要", "company profile",
        "corporate profile", "about us", "about", "profile", "outline",
        "corporate", "company"
    ]
    for word in official_words:
        if word.lower() in title_lower:
            score += 5

    # 会社概要らしいURL
    official_paths = [
        "/company", "/corporate", "/about", "/about-us", "/about_us", "/profile", "/outline"
    ]
    for word in official_paths:
        if word in url_lower:
            score += 3

    # 明らかな第三者サイト
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

        candidate_text = f"{title}\n{snippet}"
        relation = candidate_entity_relation(company, candidate_text)

        # 明確な不一致は候補から除外
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

    # URL重複除去
    unique_candidates = []
    seen_urls = set()

    for candidate in candidates:
        if candidate["url"] not in seen_urls:
            seen_urls.add(candidate["url"])
            unique_candidates.append(candidate)

    return unique_candidates[:10]

# ==========================================
# 会社検索
# ==========================================
def search_company(company: str, api_key: str):
    # 前株・後株を厳格にするため "{company}" を維持
    q1 = f'"{company}" 会社概要'

    q1_results = fetch_tavily_results(q1, api_key)
    official_candidates = find_official_candidates(company, q1_results)

    return {
        "q1_results": q1_results,
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
        q1_text = "\n".join([
            f"- タイトル: {r.get('title', '')}\n  URL: {r.get('url', '')}\n  内容: {r.get('snippet', '')}\n  システム判定: {r.get('entity_relation', 'unknown')}"
            for r in item.get("q1_results", [])[:20]
        ])

        candidates_text = json.dumps(item.get("official_candidates", []), ensure_ascii=False, indent=2)

        prompt_targets += (
            f"\n=== 対象企業 {i + 1} ===\n"
            f"【入力会社名】\n{item['company']}\n\n"
            f"【公式サイト候補】\n{candidates_text}\n\n"
            f"【Q1検索結果】\n{q1_text if q1_text else 'なし'}\n"
        )

    prompt = f"""
あなたは企業情報調査とDX営業提案の専門家です。
提供された検索結果だけを使って判断してください。情報を推測・補完してはいけません。

==================================================
【最重要：正式法人名の照合】
==================================================
入力会社名と検索結果に現れる法人名を照合してください。
法人格は必ず考慮してください。
例えば、
株式会社ニデック
ニデック株式会社
は別法人です。

==================================================
【official_url】
==================================================
official_urlには、入力会社名の対象法人自身の「会社概要ページ」を記載してください。
重要：official_urlは、検索結果に実際に存在するURLだけを使用してください。推測作成は禁止。
第三者サイト（Wikipedia、業界団体、求人サイト等）は除外してください。

==================================================
【会社概要URLを選択するときの最重要手順】
==================================================
① URLのドメインが対象法人自身の公式サイトか
② そのURLが会社概要・会社情報・企業情報等のページか
③ 検索結果中の法人名が入力会社名と一致しているか
上記を満たす候補を優先してください。

==================================================
【候補のシステム判定について】
==================================================
「システム判定」が mismatch の候補は採用禁止です。
「システム判定」が match の候補を最優先してください。
「unknown」の場合は、検索結果を確認した上で慎重に判断してください。

==================================================
【company_match】
==================================================
以下の3つだけを使用してください。
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
2023年8月14日以降の重要事項のみ（社名変更、M&A、組織再編、新規事業等）。なければ[]。

{prompt_targets}

==================================================
【JSON】
==================================================
[
  {{
    "company": "入力会社名",
    "official_url": "https://...",
    "company_match": "〇 一致",
    "department_keywords": [
      {{
        "department": "営業部",
        "keywords": [
          "SFA導入",
          "顧客管理DX",
          "商談進捗管理"
        ]
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
            config=types.GenerateContentConfig(response_mime_type="application/json")
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
            company = line.split("\t")[0].strip()
            if company and company not in company_list:
                company_list.append(company)

        progress = st.progress(0)
        status = st.empty()

        # ==================================
        # Tavily検索フェーズ
        # ==================================
        status.text("会社概要を検索中...")
        fetched_data = []

        for i, company in enumerate(company_list):
            search_data = search_company(company, tavily_api_key)
            fetched_data.append({
                "company": company,
                "q1_results": search_data["q1_results"],
                "official_candidates": search_data["official_candidates"]
            })
            progress.progress(((i + 1) / max(len(company_list), 1)) * 0.5)

        # ==================================
        # Gemini分析フェーズ
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
                fetched_item = {"q1_results": [], "official_candidates": []}

            result = company_map.get(company, {})

            official_url = result.get("official_url")
            if official_url in ["", "null"]:
                official_url = None

            company_match = result.get("company_match", "⚠️確認できず")
            if company_match not in ["〇 一致", "✕ 不一致", "⚠️確認できず"]:
                company_match = "⚠️確認できず"

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
                
                if keywords:
                    department_summary.append(f"【{department}】 " + " / ".join(keywords))

            department_text = "\n".join(department_summary)

            notes = result.get("notes", [])
            if not isinstance(notes, list):
                notes = []
            notes_text = ", ".join(str(x) for x in notes)

            batch_results.append({
                "会社名": company,
                "社名判定": company_match,
                "会社概要URL": official_url,
                "九州拠点": "一旦調査対象外",
                "部署別IT提案": department_text,
                "特記事項": notes_text,
                "_raw_keywords": department_keywords,
                "_raw_notes": notes_text,
                "_q1_results": fetched_item["q1_results"],
                "_official_candidates": fetched_item["official_candidates"]
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
    expected_columns = ["会社名", "社名判定", "会社概要URL", "九州拠点", "部署別IT提案", "特記事項"]

    for col in expected_columns:
        if col not in df_display.columns:
            df_display[col] = ""

    df_display = df_display[expected_columns]

    st.dataframe(
        df_display,
        column_config={
            "会社概要URL": st.column_config.LinkColumn("会社概要URL", help="会社概要ページを開きます")
        },
        use_container_width=True
    )

    # TSV出力 (スプレッドシート貼付用)
    tsv_text = df_display.to_csv(sep="\t", index=False)
    with st.expander("スプレッドシート用の一括コピー（タブ区切りテキスト）"):
        st.code(tsv_text, language="text")

    # CSVダウンロード
    csv_data = df_display.to_csv(index=False).encode("utf-8-sig")
    st.download_button(
        label="結果をCSVでダウンロード",
        data=csv_data,
        file_name="corporate_search_results.csv",
        mime="text/csv",
        type="primary"
    )

    st.divider()
    st.subheader("各社詳細・カード表示")

    for row in results:
        with st.expander(f"{row['会社名']} ── 【{row['社名判定']}】"):
            
            # 判定カラー表示
            if row["社名判定"] == "〇 一致":
                st.success("社名判定: 〇 一致")
            elif row["社名判定"] == "✕ 不一致":
                st.error("社名判定: ✕ 不一致")
            else:
                st.warning("社名判定: ⚠️確認できず")

            # 会社概要URL
            if row.get("会社概要URL"):
                st.markdown(f"**会社概要URL:** [{row['会社概要URL']}]({row['会社概要URL']})")
            else:
                st.write("**会社概要URL:** 確認できず")

            st.info("九州拠点の調査は一旦保留しています。")

            # 部署別IT提案
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

            # 特記事項
            if row.get("_raw_notes"):
                st.info(f"**特記事項:** {row['_raw_notes']}")

            # デバッグ：Q1
            with st.expander("🔎 デバッグ：会社概要・公式サイト検索結果"):
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

            # デバッグ：公式候補
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
