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
# 設定・初期化
# ==========================================
tavily_api_key = os.getenv("TAVILY_API_KEY") or st.secrets.get("TAVILY_API_KEY", "")
gemini_key = os.getenv("GEMINI_API_KEY") or st.secrets.get("GEMINI_API_KEY", "")
kyushu_prefectures = ["福岡", "佐賀", "長崎", "熊本", "大分", "宮崎", "鹿児島"]

def is_excluded_domain(domain: str):
    if not domain: return True
    excluded_domains = [
        "wikipedia.org", "yahoo.co.jp", "nikkei.com", "toyokeizai.net",
        "mynavi.jp", "rikunabi.com", "en-japan.com", "wantedly.com", "indeed.com",
        "onecareer.jp", "doda.jp", "bizreach.jp", "green-japan.com"
    ]
    if any(domain == excluded or domain.endswith("." + excluded) for excluded in excluded_domains):
        return True
    if any(ng in domain.lower() for ng in ["shukatsu", "tenshoku", "career", "job"]): return True
    return False

def extract_domain(url: str):
    try:
        parsed = urlparse(url)
        if not parsed.netloc: return None
        domain = parsed.netloc.lower()
        return domain[4:] if domain.startswith("www.") else domain
    except Exception:
        return None

# ==========================================
# 1. フレーズ検索（ダブルクォーテーション）を徹底した Tavily 検索機能
# ==========================================
def fetch_tavily_results(query: str, api_key: str, include_domains=None):
    try:
        client = TavilyClient(api_key=api_key)
        kwargs = {"query": query, "search_depth": "basic", "max_results": 10}
        if include_domains: kwargs["include_domains"] = include_domains
        response = client.search(**kwargs)
        return [{"title": r.get("title", ""), "url": r.get("url", ""), "snippet": r.get("content", "")} for r in response.get("results", [])]
    except Exception:
        return []

def search_company_info(company_name: str, api_key: str):
    clean_name = company_name.strip()
    
    # 前株・後株の構造をダブルクォーテーション（フレーズ検索）で厳格に固定し、複数クエリで多角的に収集
    queries = [
        f'"{clean_name}" 会社概要 企業情報',
        f'"{clean_name}" 拠点 支店 一覧',
        f'"{clean_name}" 公式サイト コーポレート'
    ]
    
    q1_results = []
    seen_urls = set()
    for q in queries:
        res = fetch_tavily_results(q, api_key)
        for r in res:
            if r['url'] not in seen_urls:
                seen_urls.add(r['url'])
                q1_results.append(r)
    
    official_domain = None
    valid_q1 = []
    for r in q1_results:
        domain = extract_domain(r["url"])
        if is_excluded_domain(domain): continue
        if domain and (domain.endswith(".co.jp") or domain.endswith(".com") or domain.endswith(".jp")):
            valid_q1.append(r)
            if not official_domain:
                official_domain = domain

    q1_filtered = []
    if official_domain:
        target_query = f'site:{official_domain} 会社概要 OR 企業情報 OR about OR outline'
        target_results = fetch_tavily_results(target_query, api_key, include_domains=[official_domain])
        q1_filtered = target_results if target_results else valid_q1
    else:
        q1_filtered = valid_q1

    q2_results = []
    if official_domain:
        q2_query = f'site:{official_domain} 拠点 OR 支店 OR 営業所 OR 事業所 OR 店舗 OR 福岡 OR 九州'
        q2_results = fetch_tavily_results(q2_query, api_key, include_domains=[official_domain])

    return {
        "official_domain": official_domain,
        "q1_results": q1_filtered,
        "q2_results": q2_results
    }

# ==========================================
# 2. 汎用 AI分析機能 (gemini-3.5-flash-lite)
# ==========================================
def analyze_companies_batch(batch_data, gemini_key):
    client = genai.Client(api_key=gemini_key)
    prompt_targets = ""

    for i, item in enumerate(batch_data):
        q1_text = "\n".join([f"- URL: {r['url']}\n  内容: {r['snippet']}" for r in item.get("q1_results", [])[:5]])
        q2_text = "\n".join([f"- URL: {r['url']}\n  内容: {r['snippet']}" for r in item.get("q2_results", [])[:5]])
        
        prompt_targets += (
            f"\n=== 対象企業 {i + 1} ===\n"
            f"【入力された会社名（前株・後株の構造に注意）】: {item['company_name']}\n"
            f"【抽出された公式ドメイン候補】: {item.get('official_domain', '不明')}\n"
            f"【会社概要ページ候補】\n{q1_text if q1_text else '情報なし'}\n"
            f"【拠点・店舗ページ候補】\n{q2_text if q2_text else '情報なし'}\n"
        )

    template = """
あなたは企業の所在調査とIT提案のプロフェッショナルです。
以下の複数企業について調査し、必ずJSON配列で返してください。

【最重要ルール：前株・後株の厳密な区別】
入力された会社名の「株式会社」の位置（前株：株式会社〇〇 / 後株：〇〇株式会社）は、法人が異なるかを識別する決定的な要素です。
有名企業の知名度に引っ張られず、入力された文字列の構造（前株・後株）と一致する正しい企業の公式サイトおよび会社概要を特定してください。

{prompt_targets}

1. "input_company": 入力された会社名。
2. "correct_company_name": 情報から確認できる正式名称（入力された前株・後株の構造に合致するもの）。
3. "profile_url": 【会社概要ページ候補】のURLリストの中から、指定された企業の公式サイト内の「会社概要」や「企業情報」に最も該当するURLを1つ記載。別法人のURLは絶対に選ばないこと。
4. "details": 九州内（福岡,佐賀,長崎,熊本,大分,宮崎,鹿児島）の拠点・支店・営業所・事業所・店舗情報。
【重要】公式情報に含まれる九州内の拠点や店舗（リフォーム事業部などの部門拠点も含む）を漏れなく抽出してください。
[{"name": "拠点名称", "address": "住所", "url": "その情報を裏付けるURL"}]
5. "reason": 九州拠点の有無についての判定理由（1文）。
6. "department_keywords": 対象企業の主要部署（最大4つ）に対し、ITツール（DX等）を提案するためのフックキーワードを3つずつ。
[{"department": "営業部", "keywords": ["SFA導入", "オンライン商談", "名刺管理"]}]
7. "notes": 特記事項。なければ[]。

必ず以下のJSON配列形式で返すこと。
[
    {
        "input_company": "入力された会社名",
        "correct_company_name": "正しい正式名称",
        "profile_url": "https://...",
        "reason": "拠点の判定理由",
        "details": [{"name": "拠点名", "address": "住所", "url": "https://..."}],
        "department_keywords": [{"department": "部署名", "keywords": ["IT提案1", "IT提案2", "IT提案3"]}],
        "notes": ["特記事項"]
    }
]
"""
    try:
        response = client.models.generate_content(
            model="gemini-3.5-flash-lite",
            contents=template.replace("{prompt_targets}", prompt_targets),
            config=types.GenerateContentConfig(response_mime_type="application/json")
        )
        text = re.sub(r"```json|```", "", response.text).strip()
        match = re.search(r"\[.*\]|\{.*\}", text, re.DOTALL)
        return json.loads(match.group(0)) if match else json.loads(text)
    except Exception as e:
        st.error(f"AI分析エラー: {str(e)}")
        return []

# ==========================================
# 3. Streamlit UI & 実行処理
# ==========================================
with st.form(key="batch_search_form"):
    st.markdown("**会社名リストを入力（1行に1社、前株・後株を正確に入力してください）**")
    raw_input = st.text_area("", placeholder="株式会社ニデック\nアステラス製薬株式会社\n株式会社ニトリ", height=150)
    submit_button = st.form_submit_button("一括検索・分析を実行", type="primary")

if submit_button:
    if not raw_input.strip() or not tavily_api_key or not gemini_key:
        st.warning("会社名の入力、またはAPIキーの設定を確認してください。")
    else:
        lines = raw_input.strip().split("\n")
        company_list = []
        for line in lines:
            comp = line.strip()
            if comp and comp not in company_list:
                company_list.append(comp)

        progress_bar = st.progress(0)
        status_text = st.empty()
        
        status_text.text("検索中...")
        fetched_data = []
        for i, comp_name in enumerate(company_list):
            fetched_data.append({"company_name": comp_name, **search_company_info(comp_name, tavily_api_key)})
            progress_bar.progress(((i + 1) / len(company_list)) * 0.5)

        status_text.text("AI分析中...")
        company_map = {}
        chunk_size = 5
        for i in range(0, len(fetched_data), chunk_size):
            chunk = fetched_data[i:i + chunk_size]
            res_list = analyze_companies_batch(chunk, gemini_key)
            if isinstance(res_list, list):
                for r in res_list:
                    if r.get("input_company"): company_map[r["input_company"]] = r
            progress_bar.progress(0.5 + ((i + len(chunk)) / len(fetched_data)) * 0.5)

        batch_results = []
        for comp_name in company_list:
            res = company_map.get(comp_name, {})

            correct_name = res.get("correct_company_name", comp_name)
            
            if comp_name == correct_name:
                name_judgement = "〇"
            else:
                name_judgement = f"✕ ({correct_name})"
            
            profile_url = res.get("profile_url", "")
            reason = res.get("reason", "")
            valid_details = [d for d in res.get("details", []) if isinstance(d, dict) and d.get("name") and any(p in d.get("address", "") for p in kyushu_prefectures)]
            details_summary = ", ".join(f"{d.get('name')} ({d.get('address')})" for d in valid_details)
            dept_kws = res.get("department_keywords", [])

            kw_summary = "\n".join([f"【{dk.get('department', '')}】 " + " / ".join(dk.get('keywords', [])) for dk in dept_kws if dk.get('department') and dk.get('keywords')])

            batch_results.append({
                "入力会社名": comp_name,
                "正式名称判定": name_judgement,
                "会社概要URL": profile_url,
                "九州拠点": details_summary if details_summary else "なし",
                "部署別IT提案": kw_summary,
                "特記事項": ", ".join(res.get("notes", [])),
                "_reason": reason,
                "_raw_details": valid_details,
                "_raw_keywords": dept_kws,
                "_correct_name": correct_name
            })

        progress_bar.progress(1.0)
        status_text.text("完了しました。")
        st.session_state["batch_results"] = batch_results

# ==========================================
# 4. 画面表示
# ==========================================
if "batch_results" in st.session_state and st.session_state["batch_results"]:
    results = st.session_state["batch_results"]
    st.divider()
    st.subheader("検索・分析結果一覧")
    
    df_table = pd.DataFrame(results)[["入力会社名", "正式名称判定", "会社概要URL", "九州拠点", "部署別IT提案", "特記事項"]]
    st.dataframe(df_table, column_config={"会社概要URL": st.column_config.LinkColumn("会社概要URL")}, use_container_width=True)

    with st.expander("スプレッドシート用コピー"):
        st.text_area("テキストをコピー", df_table.to_csv(sep="\t", index=False), height=200)

    st.divider()
    st.subheader("各社詳細・IT提案カンペ")
    for row in results:
        with st.container():
            st.markdown(f"### {row['_correct_name']} （入力: {row['入力会社名']}）")
            st.markdown(f"**会社概要URL:** {row['会社概要URL'] or '確認できず'}")
            st.info(f"**九州拠点の状況:** {row['_reason']}")
            
            if row['_raw_details']:
                st.markdown("**拠点詳細:**")
                for d in row['_raw_details']: st.markdown(f"- **{d['name']}** （{d['address']}）")
            
            st.markdown("**💡 部署別 IT提案キーワード:**")
            if row['_raw_keywords']:
                for dk in row['_raw_keywords']:
                    st.markdown(f"**【{dk.get('department', '不明')}】**")
                    for kw in dk.get('keywords', []): st.markdown(f"- {kw}")
            else:
                st.markdown("- キーワード取得不可")
            
            if row['特記事項']: st.markdown(f"**特記事項:** {row['特記事項']}")
            st.markdown("---")
