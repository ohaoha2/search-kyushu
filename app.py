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
# APIキーの自動取得（Secrets優先）
# ==========================================
tavily_api_key = os.getenv("TAVILY_API_KEY") or st.secrets.get("TAVILY_API_KEY", "")
gemini_key = os.getenv("GEMINI_API_KEY") or st.secrets.get("GEMINI_API_KEY", "")

# ==========================================
# 九州都道府県
# ==========================================
kyushu_prefectures = ["福岡", "佐賀", "長崎", "熊本", "大分", "宮崎", "鹿児島"]

# ==========================================
# 明らかな第三者サイトを除外
# ==========================================
def is_excluded_domain(domain: str):
    if not domain:
        return True
    excluded_domains = [
        # 基本・ニュース系
        "wikipedia.org", "yahoo.co.jp", "news.yahoo.co.jp", "nikkei.com", "toyokeizai.net",
        # 求人・就活系
        "mynavi.jp", "rikunabi.com", "en-japan.com", "wantedly.com", "indeed.com",
        "onecareer.jp", "doda.jp", "type.jp", "bizreach.jp", "green-japan.com",
        # 企業口コミ系
        "openwork.jp", "vorkers.com", "jobtalk.jp", "en-hyouban.com", "syukatsu-kaigi.jp",
        # 企業DB・プレスリリース系
        "metoree.com", "baseconnect.in", "houjin-bangou.nta.go.jp", "salesnow.jp",
        "irbank.net", "strainer.jp", "prtimes.jp", "navitime.co.jp"
    ]
    return any(domain == excluded or domain.endswith("." + excluded) for excluded in excluded_domains)
# ==========================================
# 1. Tavily API 実行関数
# ==========================================
def fetch_tavily_results(query: str, api_key: str, include_domains=None):
    try:
        client = TavilyClient(api_key=api_key)
        search_kwargs = {"query": query.strip().replace("`", ""), "search_depth": "basic", "max_results": 20}
        if include_domains:
            search_kwargs["include_domains"] = include_domains
        response = client.search(**search_kwargs)
        
        results = []
        for item in response.get("results", []):
            results.append({
                "title": item.get("title", ""),
                "url": item.get("url", ""),
                "snippet": item.get("content", "")
            })
        return results
    except Exception:
        return []

# URLからドメイン抽出
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

# 公式サイト候補のスコアリング
def score_official_candidate(company: str, result: dict):
    title_lower = result.get("title", "").lower()
    snippet_lower = result.get("snippet", "").lower()
    url_lower = result.get("url", "").lower()
    score = 0

    if company.lower() in title_lower: score += 10
    if company.lower() in snippet_lower: score += 5

    official_title_words = ["公式", "会社概要", "会社情報", "企業情報", "コーポレート", "corporate", "company", "about"]
    for word in official_title_words:
        if word.lower() in title_lower: score += 5

    company_clean = company.replace("株式会社", "").replace("有限会社", "").replace("合同会社", "").replace("ホールディングス", "").replace("HD", "").replace(" ", "").replace(" ", "").lower()
    if company_clean and company_clean in url_lower: score += 10

    official_path_words = ["/about", "/about_us", "/company", "/corporate", "/profile"]
    for word in official_path_words:
        if word in url_lower: score += 5

    domain = extract_domain(url_lower)
    if is_excluded_domain(domain): score -= 50

    return score

# 公式ドメイン候補を取得
def find_official_domains(company: str, results: list):
    candidates = []
    for result in results:
        domain = extract_domain(result.get("url", ""))
        if not domain or is_excluded_domain(domain): continue
        
        score = score_official_candidate(company, result)
        candidates.append({"domain": domain, "score": score, "title": result.get("title", ""), "url": result.get("url", "")})

    candidates.sort(key=lambda x: x["score"], reverse=True)
    
    unique_candidates = []
    seen_domains = set()
    for candidate in candidates:
        domain = candidate["domain"]
        if domain not in seen_domains:
            seen_domains.add(domain)
            unique_candidates.append(candidate)
            
    return unique_candidates[:3]

# ==========================================
# 2. 検索（同名対策対応）
# ==========================================
def search_multi_queries(company_info: dict, api_key: str):
    company_name = company_info["name"]
    context = company_info["context"] # 所在地などの追加情報

    # Q1：会社概要・企業情報ページの検索（同名対策でcontextを付与）
    q1_query = f'"{company_name}" {context} 会社概要 OR 企業情報 公式サイト'
    res1 = fetch_tavily_results(q1_query, api_key)

    official_candidates = find_official_domains(company_name, res1)
    official_domains = [c["domain"] for c in official_candidates if c["score"] >= 10]

    # Q2：公式サイト内検索
    res2 = []
    if official_domains:
        domain = official_domains[0]
        q2_queries = [
            f'site:{domain} 九州 福岡 佐賀 長崎 熊本 大分 宮崎 鹿児島 支店 支社 営業所 事業所',
            f'site:{domain} 九州 福岡 営業部 法人営業 営業拠点 拠点一覧 事業所一覧',
            f'site:{domain} 九州 福岡 会社情報 拠点 所在地 住所'
        ]
        seen_q2_urls = set()
        for q2 in q2_queries:
            current_results = fetch_tavily_results(q2, api_key, include_domains=[domain])
            for result in current_results:
                url = result.get("url", "")
                if url and url not in seen_q2_urls:
                    seen_q2_urls.add(url)
                    res2.append(result)

    return {"q1_results": res1, "q2_results": res2, "official_candidates": official_candidates, "official_domains": official_domains}

# ==========================================
# 3. JSONパース
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
# 4. 複数社を一括でAI分析
# ==========================================
def analyze_companies_batch(batch_data, gemini_key):
    client = genai.Client(api_key=gemini_key)
    prompt_targets = ""

    for i, item in enumerate(batch_data):
        q1_text = "\n".join([f"- タイトル: {r.get('title', '')}\n  URL: {r.get('url', '')}\n  内容: {r.get('snippet', '')}" for r in item.get("q1_results", [])[:5]])
        q2_text = "\n".join([f"- タイトル: {r.get('title', '')}\n  URL: {r.get('url', '')}\n  内容: {r.get('snippet', '')}" for r in item.get("q2_results", [])[:10]])
        official_domain = item.get("official_domains", [""])[0] if item.get("official_domains") else ""

        prompt_targets += (
            f"\n=== 対象企業 {i + 1}: {item['company']['name']} ({item['company']['context']}) ===\n"
            f"【公式ドメイン候補】\n{official_domain}\n"
            f"【Q1：会社概要ページ候補検索】\n{q1_text if q1_text else 'なし'}\n"
            f"【Q2：公式ドメイン内の九州拠点検索】\n{q2_text if q2_text else 'なし'}\n"
        )

    template = """
あなたは企業の所在調査とIT提案のプロフェッショナルです。
以下の複数企業について厳密に調査し、必ずJSON配列で返してください。

{prompt_targets}

1. "input_company"
入力された会社名をそのまま格納してください。

2. "correct_company_name"
Q1の検索結果（会社概要など）から確認できる、正しい正式な会社名。
（株）などの略称が使われていた場合や、入力ミスがある場合はここで正式名称（例：株式会社〇〇）に補正してください。

3. "profile_url"
対象企業の公式サイトのうち、「トップページ」ではなく、「会社概要」または「企業情報」が載っているページのURLを記載してください。
確認できない場合は null にしてください。

4. "details"
九州内の対象企業自身の具体的な拠点を記載。現在稼働中であること。
[{"name": "拠点名称", "address": "住所", "url": "その拠点を裏付ける公式URL"}]

5. "reason"
なぜ九州拠点がある・ない・不明と判断したかの「理由」を1文で簡潔に記載してください。
例: 「公式サイトの事業所一覧に福岡支店が掲載されているため」

6. "department_keywords" （※重要：ITツールの提案観点）
対象企業の事業内容から、主要な部署（例: 営業部、人事・総務部、製造部、情報システム部など）を最大3〜4つ推測してください。
それぞれの部署に対して、「ITツールやシステムを提案する（DX、業務効率化、クラウド化など）」という観点で、アポ取りや提案に使える具体的なフックキーワードを**必ず3つずつ**挙げてください。

出力例:
"department_keywords": [
    {
        "department": "営業部",
        "keywords": ["SFA/CRMによる顧客管理", "オンライン商談ツールの導入", "名刺管理クラウド化"]
    },
    {
        "department": "人事・総務部",
        "keywords": ["勤怠管理システムの刷新", "タレントマネジメント導入", "ペーパーレス化・電子契約"]
    }
]

7. "notes"
直近の社名変更や移転、M&Aなどの特記事項。なければ空配列[]。

必ず以下のJSON配列形式で返してください。
[
    {
        "input_company": "入力された会社名",
        "correct_company_name": "正しい正式名称",
        "profile_url": "https://.../about",
        "reason": "九州拠点の判定理由",
        "details": [
            {
                "name": "拠点名",
                "address": "住所",
                "url": "https://..."
            }
        ],
        "department_keywords": [
            {
                "department": "部署名",
                "keywords": ["IT提案1", "IT提案2", "IT提案3"]
            }
        ],
        "notes": ["特記事項"]
    }
]
"""
    prompt = template.replace("{prompt_targets}", prompt_targets)
    try:
        response = client.models.generate_content(
            model="gemini-3.5-flash-lite",
            contents=prompt,
            config=types.GenerateContentConfig(response_mime_type="application/json")
        )
        return safe_parse_json(response.text.strip())
    except Exception as e:
        st.error(f"AI分析バッチ処理エラー: {str(e)}")
        return []

# ==========================================
# 5. Streamlit UI
# ==========================================
with st.form(key="batch_search_form"):
    st.markdown("""
    **会社名リストを入力（スプレッドシートからそのまま貼り付け可能）**  
    ※同名企業を避けるため、スプレッドシートで「会社名」の隣の列に「都道府県」や「事業内容」を並べてからコピー＆ペーストすると精度が上がります。（例：`株式会社アシスト` `福岡県`）
    """)
    raw_input = st.text_area("", placeholder="株式会社〇〇\t福岡県\n株式会社△△", height=150)
    submit_button = st.form_submit_button("一括検索・分析を実行", type="primary")

# ==========================================
# 6. 実行
# ==========================================
if submit_button:
    if not raw_input.strip():
        st.warning("会社名を入力してください。")
    elif not tavily_api_key or not gemini_key:
        st.error("APIキーが設定されていません。")
    else:
        st.session_state.pop("batch_results", None)
        lines = raw_input.strip().split("\n")
        
        company_list = []
        for line in lines:
            parts = line.split("\t")
            comp = parts[0].strip()
            # 2列目以降があれば所在地等のコンテキストとして取得（同名対策）
            context = " ".join([p.strip() for p in parts[1:]]) if len(parts) > 1 else ""
            if comp and comp not in [c["name"] for c in company_list]:
                company_list.append({"name": comp, "context": context})

        progress_bar = st.progress(0)
        status_text = st.empty()
        fetched_data = []

        status_text.text("検索中...")
        for i, comp_info in enumerate(company_list):
            search_data = search_multi_queries(comp_info, tavily_api_key)
            fetched_data.append({
                "company": comp_info,
                "q1_results": search_data["q1_results"],
                "q2_results": search_data["q2_results"],
                "official_domains": search_data["official_domains"]
            })
            progress_bar.progress(((i + 1) / max(len(company_list), 1)) * 0.5)

        status_text.text("AIによる一括分析を実行中（IT提案キーワード生成中）...")
        company_map = {}
        chunk_size = 5 # 処理が少し重くなるためチャンクサイズを調整
        
        for i in range(0, len(fetched_data), chunk_size):
            chunk = fetched_data[i:i + chunk_size]
            res_list = analyze_companies_batch(chunk, gemini_key)
            
            if isinstance(res_list, list):
                for r in res_list:
                    comp_name = r.get("input_company")
                    if comp_name: company_map[comp_name] = r
            
            progress_bar.progress(0.5 + ((i + len(chunk)) / max(len(fetched_data), 1)) * 0.5)

        batch_results = []
        for comp_info in company_list:
            comp_name = comp_info["name"]
            res = company_map.get(comp_name, {})

            # 九州拠点の処理
            raw_details = res.get("details", [])
            valid_details = []
            for d in raw_details:
                if isinstance(d, dict) and d.get("name") and d.get("address"):
                    if any(pref in d.get("address", "") for pref in kyushu_prefectures):
                        valid_details.append(d)
                        
            details_summary = ", ".join(f"{d.get('name')} ({d.get('address')})" for d in valid_details)
            
            # IT提案キーワードの整形 (部署ごとに改行して見やすく)
            dept_keywords = res.get("department_keywords", [])
            keyword_texts = []
            for dk in dept_keywords:
                dept = dk.get("department", "")
                kws = dk.get("keywords", [])
                if dept and kws:
                    keyword_texts.append(f"【{dept}】 " + " / ".join(kws))
            keyword_summary = "\n".join(keyword_texts)

            # 保存
            batch_results.append({
                "入力会社名": comp_name,
                "正式名称": res.get("correct_company_name", comp_name),
                "会社概要URL": res.get("profile_url", ""),
                "九州拠点": details_summary if details_summary else "なし",
                "部署別IT提案": keyword_summary,
                "特記事項": ", ".join(res.get("notes", [])),
                "_reason": res.get("reason", ""),
                "_raw_details": valid_details,
                "_raw_keywords": dept_keywords
            })

        progress_bar.progress(1.0)
        status_text.text("すべての処理が完了しました。")
        st.session_state["batch_results"] = batch_results

# ==========================================
# 7. 一覧表示
# ==========================================
if "batch_results" in st.session_state and st.session_state["batch_results"]:
    results = st.session_state["batch_results"]
    st.divider()
    st.subheader("検索・分析結果一覧")

    df_display = pd.DataFrame(results)
    
    # 表示用カラムを限定（「判定」は除外）
    display_columns = ["入力会社名", "正式名称", "会社概要URL", "九州拠点", "部署別IT提案", "特記事項"]
    df_table = df_display[display_columns].copy()

    st.dataframe(
        df_table,
        column_config={
            "会社概要URL": st.column_config.LinkColumn("会社概要URL", help="会社概要・企業情報ページが開きます")
        },
        use_container_width=True
    )

    # ======================================
    # スプレッドシート用コピー
    # ======================================
    tsv_text = df_table.to_csv(sep="\t", index=False)
    with st.expander("スプレッドシート用の一括コピー（タブ区切りテキスト）"):
        st.text_area("以下のテキストをコピーしてください", tsv_text, height=200)

    # ======================================
    # 各社詳細カード表示
    # ======================================
    st.divider()
    st.subheader("各社詳細・IT提案カンペ")
    
    for row in results:
        with st.container():
            st.markdown(f"### {row['正式名称']} （入力: {row['入力会社名']}）")
            st.markdown(f"**会社概要URL:** {row['会社概要URL'] if row['会社概要URL'] else '確認できず'}")
            
            # 判定結果は出さず、理由だけを記載
            st.info(f"**九州拠点の状況:** {row['_reason']}")
            
            if row['_raw_details']:
                st.markdown("**拠点詳細:**")
                for d in row['_raw_details']:
                    st.markdown(f"- **{d['name']}** （{d['address']}）")
            
            st.markdown("**💡 部署別 IT提案キーワード:**")
            if row['_raw_keywords']:
                for dept_info in row['_raw_keywords']:
                    st.markdown(f"**【{dept_info.get('department', '部署名不明')}】**")
                    for kw in dept_info.get('keywords', []):
                        st.markdown(f"- {kw}")
            else:
                st.markdown("- キーワードを取得できませんでした。")
            
            if row['特記事項']:
                st.markdown(f"**特記事項:** {row['特記事項']}")
                
            st.markdown("---")
