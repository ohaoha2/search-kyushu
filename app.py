import concurrent.futures
import json
import os
import re
from urllib.parse import urlparse
from google import genai
from google.genai import types
import pandas as pd
import requests
import streamlit as st

st.set_page_config(page_title="企業情報一括検索ツール", layout="wide")

st.title("企業情報一括検索ツール")

serper_api_key = (
    os.getenv("SERPER_API_KEY") or st.secrets.get("SERPER_API_KEY", "")
)
gemini_key = os.getenv("GEMINI_API_KEY") or st.secrets.get("GEMINI_API_KEY", "")

if "search_history" not in st.session_state:
  st.session_state.search_history = []
if "result_cache" not in st.session_state:
  st.session_state.result_cache = {}

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
    "社団法人",
    "財団法人",
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


def parse_input_company(raw_text: str):
  """入力文字列をタブ・読点で「社名本体」と「補足キーワード」に分離し、法人格前後のスペースのみを除去する"""
  text = raw_text.strip()
  if not text:
    return "", ""

  parts = re.split(r'[\t、]+', text)
  raw_company = parts[0].strip()
  extra_keywords = " ".join([p.strip() for p in parts[1:]]) if len(parts) > 1 else ""

  legal_forms_pattern = "|".join(map(re.escape, LEGAL_FORMS))
  
  base_company = re.sub(r'[\s ]+(' + legal_forms_pattern + ')', r'\1', raw_company)
  base_company = re.sub(r'(' + legal_forms_pattern + ')[\s ]+', r'\1', base_company)

  return base_company, extra_keywords


def is_excluded_domain(domain: str):
  if not domain:
    return True
  excluded_domains = [
      "wikipedia.org",
      "irbank.net",
      "compalyze.co.jp",
      "houjin.jp",
      "xn--pckua2a7gp15o89zb.com",
      "baseconnect.in",
  ]
  return any(
      domain == excluded or domain.endswith("." + excluded)
      for excluded in excluded_domains
  )


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
  if not domain:
    return ""
  parts = domain.split(".")
  if len(parts) >= 3 and parts[-2] in [
      "co",
      "com",
      "ne",
      "or",
      "go",
      "ac",
      "lg",
  ]:
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
      core = normalized[len(form) :]
      if core:
        return {
            "original": normalized,
            "core": core,
            "legal_form": form,
            "position": "front",
        }
    if normalized.endswith(form):
      core = normalized[: -len(form)]
      if core:
        return {
            "original": normalized,
            "core": core,
            "legal_form": form,
            "position": "back",
        }
  return {
      "original": normalized,
      "core": normalized,
      "legal_form": None,
      "position": "unknown",
  }


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
    if (
        opposite_company
        and (opposite_company in text)
        and (input_original not in text)
    ):
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
    if form == input_form and (
        (input_info["position"] == "front" and has_front)
        or (input_info["position"] == "back" and has_back)
    ):
      return "match"
    return "mismatch"
  return "unknown"


def fetch_serper_results(query: str, api_key: str):
  url = "https://google.serper.dev/search"
  payload = {"q": query, "gl": "jp", "hl": "ja", "num": 40}
  headers = {"X-API-KEY": api_key.strip(), "Content-Type": "application/json"}
  response = requests.post(url, headers=headers, json=payload, timeout=20)
  if response.status_code != 200:
    raise Exception(
        f"Serper API エラー (HTTP {response.status_code}): {response.text}"
    )
  data = response.json()
  results = []
  for item in data.get("organic", []):
    results.append({
        "title": item.get("title", ""),
        "url": item.get("link", ""),
        "snippet": item.get("snippet", ""),
    })
  return results


def scrape_page_text(url: str):
  try:
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
    response = requests.get(url, headers=headers, timeout=5)
    response.encoding = response.apparent_encoding
    if response.status_code == 200:
      html = response.text
      html = re.sub(
          r"<(script|style)[^>]*>.*?</\1>",
          " ",
          html,
          flags=re.DOTALL | re.IGNORECASE,
      )
      text = re.sub(r"<[^>]+>", " ", html)
      text = re.sub(r"\s+", " ", text).strip()
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

  norm_company = normalize_company_name(company).lower()
  if norm_company in title.lower():
    score += 25
  if norm_company in snippet.lower():
    score += 15

  relation = candidate_entity_relation(company, title + "\n" + snippet)
  if relation == "match":
    score += 30

  official_words = [
      "会社概要",
      "会社情報",
      "企業情報",
      "企業概要",
      "company profile",
      "corporate profile",
      "about us",
      "about",
      "profile",
      "outline",
      "corporate",
      "company",
      "沿革",
      "ir",
  ]
  for word in official_words:
    if word.lower() in title.lower():
      score += 50

  official_paths = [
      "/company",
      "/corporate",
      "/about",
      "/about-us",
      "/about_us",
      "/profile",
      "/outline",
      "company.html",
      "about.html",
      "profile.html",
      "/ir/",
      "/history/",
  ]
  for path in official_paths:
    if path in url.lower():
      score += 50

  parsed_url = urlparse(url)
  if parsed_url.path in ["", "/", "/index.html", "/index.php"]:
    score -= 10

  if domain and re.match(r"^[a-z0-9]+-[a-z0-9]+\.", domain):
    score -= 40

  news_paths = ["/news", "/press", "/release", "news.html", "press.html", "/topics"]
  for path in news_paths:
    if path in url.lower():
      score -= 50

  spam_domains = [
      "metoree.com",
      "salesnow.jp",
      "syukatsu-kaigi.jp",
      "jobtalk.jp",
      "openwork.jp",
      "en-hyouban.com",
      "bizmaps.jp",
      "atengineer.com",
      "ipros.com",
      "the-shashi.com",
      "data-max.co.jp",
      "job.mynavi.jp",
      "tenshoku.mynavi.jp",
      "baito.mynavi.jp",
      "job.rikunabi.com",
      "next.rikunabi.com",
      "employment.en-japan.com",
      "type.jp",
      "mapion.co.jp",
      "navitime.co.jp",
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

    score = score_official_candidate(company, result, idx)
    relation = candidate_entity_relation(company, title + "\n" + snippet)

    candidates.append({
        "score": score,
        "domain": domain,
        "title": title,
        "url": url,
        "snippet": snippet,
        "entity_relation": relation,
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


def search_company(company_input: str, api_key: str):
  base_company, extra_keywords = parse_input_company(company_input)

  q1 = f"{base_company} {extra_keywords} 会社概要 公式".strip()
  q1_results = fetch_serper_results(q1, api_key)
  official_candidates = find_official_candidates(base_company, q1_results)

  best_domain = None
  if official_candidates:
    best_domain = official_candidates[0]["domain"]

  q2_results = []
  scraped_texts = []

  if best_domain:
    q2_keywords = f"{best_domain} 拠点一覧 支店 営業所 事業所 国内拠点 アクセス ネットワーク"
    raw_q2_results = fetch_serper_results(q2_keywords, api_key)

    for r in raw_q2_results:
      domain = extract_domain(r["url"])
      if domain and domain == best_domain:
        q2_results.append(r)

    inferred_results = []
    added_urls = set([r["url"] for r in q2_results])
    for r in q2_results:
      url = r["url"]
      parsed = urlparse(url)
      path = parsed.path

      m = re.search(
          r"^(.*?/(?:network|office|offices|location|locations|access|base|branch|kyoten)[/])",
          path,
          re.IGNORECASE,
      )
      if m:
        inferred_url = f"{parsed.scheme}://{parsed.netloc}{m.group(1)}"
        if inferred_url not in added_urls and inferred_url != url:
          added_urls.add(inferred_url)
          inferred_results.append({
              "title": "【拠点一覧トップページ候補】",
              "url": inferred_url,
              "snippet": (
                  "システムがURL階層から自動推測した拠点・事業所一覧のトップページです。ここを最優先で選んでください。"
              ),
          })

    q2_results = inferred_results + q2_results

    for r in q2_results[:3]:
      url = r["url"]
      if not url.lower().endswith(".pdf"):
        scraped = scrape_page_text(url)
        if scraped:
          scraped_texts.append(
              f"【{r['title']}】(URL: {url}) の直読みデータ:\n{scraped}"
          )

  return {
      "company_input": company_input,
      "base_company": base_company,
      "q1_results": q1_results,
      "q2_results": q2_results,
      "official_candidates": official_candidates,
      "scraped_text": "\n\n".join(scraped_texts),
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
    q1_text = "\n".join([
        f"- タイトル: {r.get('title', '')}\n  URL: {r.get('url', '')}\n  内容:"
        f" {r.get('snippet', '')}\n  システム判定:"
        f" {r.get('entity_relation', 'unknown')}"
        for r in item.get("q1_results", [])[:20]
    ])
    q2_text = "\n".join([
        f"- タイトル: {r.get('title', '')}\n  URL: {r.get('url', '')}\n  内容:"
        f" {r.get('snippet', '')}"
        for r in item.get("q2_results", [])[:15]
    ])
    candidates_text = json.dumps(
        item.get("official_candidates", []), ensure_ascii=False, indent=2
    )

    prompt_targets += (
        f"\n=== 対象企業 index: {i} ===\n"
        f"【判定用基本社名】\n{item['base_company']}\n"
        f"【補足キーワード含む入力】\n{item['company_input']}\n\n"
        f"【公式サイト候補】\n{candidates_text}\n\n"
        f"【Q1検索結果（会社概要・社名変更用）】\n{q1_text if q1_text else 'なし'}\n\n"
        f"【Q2検索結果（拠点一覧ページ用）】\n{q2_text if q2_text else '公式サイト内に該当する拠点ページが見つかりませんでした'}\n\n"
        f"【Q2ページ本文 直読みデータ（詳細情報）】\n{item.get('scraped_text', '取得失敗 または 該当ページなし')}\n"
    )

  prompt = f"""
あなたは企業情報調査とDX営業提案の専門家です。
提供された検索結果（およびページ直読みデータ）だけを使って判断してください。

==================================================
【official_url】（会社概要の選定）
==================================================
対象企業の「会社概要・企業情報ページ」のURLを記載してください。
旧社名で入力された場合でも、検索結果にある現在の新社名のコーポレートサイト/会社概要/IRページのURLを設定してください。
【絶対ルール】：プレスリリース、ニュース記事、お知らせページ（/news/や/press/が含まれるもの）は絶対に選ばないでください。

==================================================
【拠点一覧】（拠点一覧ページのURL抽出と賢い推測）
==================================================
対象法人の国内の拠点（支社、支店、営業所、工場など）やネットワークが一覧で掲載されているページの「URL」を1つだけ抽出してください。
- 会社概要（official_url）と同じドメインのURLを最優先で選んでください。
- Q2検索結果の中に「【拠点一覧トップページ候補】」というタイトルのURLがある場合は、それが全国の拠点を網羅したトップ階層である可能性が高いため、最優先で選択してください。
- 特定の1拠点だけを紹介している個別ページ（例：「〇〇事業所」単体のページ）は絶対に選ばないでください。
- 該当する一覧ページが見つからない場合は、空文字 "" を設定してください。

==================================================
【company_match】（社名判定の厳格ルール）
==================================================
STEP 1: 検索結果テキスト全体から対象企業の【現在の最新の正式法人名】を特定する。
STEP 2: 【判定用基本社名】と【現在の最新の正式法人名】を比較する。

・【判定用基本社名】が【現在の最新の正式法人名】と「法人格（株式会社など）も含めて完全に一致」している場合のみ：
  → **絶対に「〇」** とだけ出力してください。
  ※ユーザー入力に地名や業種などの補足キーワードが含まれていても、【判定用基本社名】自体が完全一致していれば「〇」と判定してください。
  ※入力が「〇〇」で正式名称が「株式会社〇〇」のように、法人格が抜けている場合や位置が異なる場合は完全一致とはみなしません。「〇」にしてはいけません。

・【判定用基本社名】が『過去の旧社名』『グループ再編・統合・合併前の社名』である場合のみ：
  → 必ず以下のマークダウンリンク形式で出力してください。
  フォーマット： [✕ 変更年月日 『現在の新社名』へ変更](社名変更の根拠URL)
  ※【リンクURLの指定】: Q1検索結果にある社名変更のお知らせ、沿革、プレスリリース、または新会社の会社概要URLを設定すること。
  ※【装飾の禁止】: バッククォート（`）やアスタリスク（**）などの装飾記号は出力に一切入れないでください。純粋な `[テキスト](URL)` のみ。

・入力会社名が全く異なる別法人の場合 → 「✕ 不一致」
・正式法人名が確認できない場合 → 「⚠️確認できず」（ただし検索結果に新社名への移行が明記されている場合は『✕ 新社名へ変更』とすること）

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
    "index": 0,
    "official_url": "https://.../company/profile/",
    "company_match": "〇",
    "branch_list_url": "https://.../company/office/",
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
        ),
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
      "会社名リストを入力（補足キーワードは読点「、」で区切ってください。法人格の前後のスペースは自動除去されます）",
      placeholder="株式会社〇〇〇〇、栃木\n株式会社△△△\n\n※Excel等から複数列（会社名・キーワード）をコピー＆ペーストした場合も自動で認識します。",
      height=180,
  )
  submit_button = st.form_submit_button("検索", type="primary")

# ==========================================
# 実行
# ==========================================
if submit_button:
  if not raw_input.strip() or raw_input.strip() == "株式会社○○○○":
    st.warning("会社名を入力してください。")
  elif not serper_api_key or not gemini_key:
    st.error(
        "Streamlitの Secrets に SERPER_API_KEY または GEMINI_API_KEY"
        " が設定されていません。"
    )
  else:
    st.session_state.result_cache = {}
    st.session_state.pop("batch_results", None)

    lines = raw_input.strip().split("\n")
    company_list = []
    for line in lines:
      comp = line.strip()
      if comp and comp not in company_list:
        company_list.append(comp)

    companies_to_fetch = company_list
    final_results = []

    progress = st.progress(0)
    status = st.empty()

    if companies_to_fetch:
      status.text("検索中...")
      fetched_data = []

      def fetch_wrapper(comp):
        return search_company(comp, serper_api_key)

      with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
        futures = {
            executor.submit(fetch_wrapper, comp): comp
            for comp in companies_to_fetch
        }
        completed = 0
        for future in concurrent.futures.as_completed(futures):
          comp_name = futures[future]
          try:
            fetched_data.append(future.result())
          except Exception as e:
            st.error(
                f"【検索失敗】 {comp_name}のデータ取得中にエラーが発生しました:"
                f" {str(e)}"
            )
            base_c, _ = parse_input_company(comp_name)
            fetched_data.append({
                "company_input": comp_name,
                "base_company": base_c,
                "q1_results": [],
                "q2_results": [],
                "official_candidates": [],
                "scraped_text": "",
            })
          completed += 1
          progress.progress((completed / len(companies_to_fetch)) * 0.4)

      status.text("分析中...")
      company_map = {}
      chunk_size = 5
      chunks = [
          fetched_data[i : i + chunk_size]
          for i in range(0, len(fetched_data), chunk_size)
      ]

      def gemini_wrapper(chunk):
        res = analyze_companies_batch(chunk, gemini_key)
        return chunk, res

      with concurrent.futures.ThreadPoolExecutor(max_workers=3) as executor:
        futures = [
            executor.submit(gemini_wrapper, chunk) for chunk in chunks
        ]
        completed_chunks = 0
        for future in concurrent.futures.as_completed(futures):
          try:
            chunk, res_list = future.result()
            if isinstance(res_list, list):
              for idx_in_chunk, item in enumerate(chunk):
                res_item = next(
                    (r for r in res_list if r.get("index") == idx_in_chunk),
                    None,
                )
                if not res_item and idx_in_chunk < len(res_list):
                  res_item = res_list[idx_in_chunk]

                if res_item:
                  company_map[item["company_input"]] = res_item
          except Exception as e:
            st.error(f"【AI分析エラー】: {str(e)}")
          completed_chunks += 1
          progress.progress(0.4 + (completed_chunks / len(chunks)) * 0.5)

      status.text("結果を整形中...")
      for company in companies_to_fetch:
        fetched_item = next(
            (
                item
                for item in fetched_data
                if item["company_input"] == company
            ),
            None,
        )
        if fetched_item is None:
          base_c, _ = parse_input_company(company)
          fetched_item = {
              "company_input": company,
              "base_company": base_c,
              "q1_results": [],
              "q2_results": [],
              "official_candidates": [],
              "scraped_text": "",
          }

        result = company_map.get(company, {})

        official_url = result.get("official_url")
        if official_url in ["", "null"]:
          official_url = None

        company_match = str(result.get("company_match", "⚠️確認できず")).strip()
        company_match = (
            company_match.replace("**", "").replace("`", "").strip()
        )

        base_comp = fetched_item["base_company"]
        norm_input = normalize_company_name(base_comp)

        if not (company_match == "〇" or company_match.startswith("〇")):
          if "へ変更" in company_match or "に変更" in company_match:
            new_name_matches = re.findall(
                r"[『「]([^『「』」]+?)[』」]\s*?[へに]変更", company_match
            )
            if new_name_matches:
              extracted_new_name = normalize_company_name(new_name_matches[-1])
              if norm_input == extracted_new_name:
                company_match = "〇"

        if "確認できず" in company_match:
          for r in fetched_item.get("q1_results", []):
            snip = r.get("snippet", "") + " " + r.get("title", "")
            if "社名変更" in snip or "へ変更" in snip or "に変更" in snip:
              m = re.search(
                  r"([『「]?[A-Za-z0-9一-龠々-〇\s]+?(?:株式会社|法人)?[』」]?)\s*?へ(?:社名)?変更",
                  snip,
              )
              if m:
                new_c = (
                    m.group(1).replace("『", "").replace("』", "").strip()
                )
                if new_c and new_c != base_comp:
                  url = r.get("url", "")
                  date_m = re.search(
                      r"(\d{4}年\d{1,2}月(?:\d{1,2}日)?)", snip
                  )
                  date_str = f"{date_m.group(1)}に " if date_m else ""
                  company_match = f"[✕ {date_str}『{new_c}』へ変更]({url})"
                  if not official_url:
                    official_url = url
                  break
        
        # ★【追加機能】スペースが除去されて「〇」になったことを明示する
        raw_company_part = re.split(r'[\t、]+', company)[0].strip()
        if company_match.startswith("〇") and base_comp != raw_company_part:
            company_match = "〇（スペース除去済）"

        branch_list_url_raw = str(result.get("branch_list_url", "")).strip()
        branch_list_url = ""
        url_match = re.search(r'https?://[^\s)\]"\']+', branch_list_url_raw)
        if url_match:
          branch_list_url = url_match.group(0)

        if branch_list_url and branch_list_url.lower() != "null":
          if official_url:
            off_dom = extract_domain(official_url)
            br_dom = extract_domain(branch_list_url)
            if off_dom and br_dom and off_dom != br_dom:
              branch_list_url = ""

        if branch_list_url and official_url:
          if branch_list_url.rstrip("/") == official_url.rstrip("/"):
            branch_list_url = ""

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

          department_summary.append(
              f"【{department}】\n" + "\n".join(f"・{kw}" for kw in keywords)
          )

        department_text = "\n\n".join(department_summary)

        final_row = {
            "会社名": base_comp,
            "会社概要URL": official_url,
            "社名判定": company_match,
            "拠点一覧": branch_list_url if branch_list_url else "なし",
            "部署別IT": department_text,
            "_company_input": company,
            "_raw_keywords": department_keywords,
            "_branch_list_url": branch_list_url,
            "_q1_results": fetched_item.get("q1_results", []),
            "_q2_results": fetched_item.get("q2_results", []),
            "_official_candidates": fetched_item.get(
                "official_candidates", []
            ),
            "_scraped_text": fetched_item.get("scraped_text", ""),
        }

        st.session_state.result_cache[company] = final_row
        final_results.append(final_row)

    progress.progress(1.0)
    status.text("すべての処理が完了しました。")

    ordered_results = []
    for comp in company_list:
      row = next(
          (r for r in final_results if r.get("_company_input") == comp), None
      )
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
  expected_columns = ["会社名", "会社概要URL", "社名判定", "拠点一覧", "部署別IT"]

  for col in expected_columns:
    if col not in df_display.columns:
      df_display[col] = ""

  df_display = df_display[expected_columns]

  md_table = "| 会社名 | 会社概要URL | 社名判定 | 拠点一覧 | 部署別IT |\n"
  md_table += "|---|---|---|---|---|\n"

  for row in results:
    company_md = row.get("会社名", "").replace("\n", " ")

    url = row.get("会社概要URL")
    url_md = f'<a href="{url}" target="_blank">{url}</a>' if url else "確認できず"

    match_md = str(row.get("社名判定", ""))
    match_md = re.sub(
        r"\[(.*?)\]\((.*?)\)", r'<a href="\2" target="_blank">\1</a>', match_md
    )

    branch_val = str(row.get("拠点一覧", ""))
    if branch_val.startswith("http"):
      branch_md = f'<a href="{branch_val}" target="_blank">{branch_val}</a>'
    else:
      branch_md = branch_val

    it_prop_md = str(row.get("部署別IT", "")).replace("\n", "<br>")

    md_table += (
        f"| {company_md} | {url_md} | {match_md} | {branch_md} |"
        f" {it_prop_md} |\n"
    )

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
      type="primary",
  )

  # ======================================
  # カード
  # ======================================
  st.divider()
  st.subheader("各社詳細・カード表示")

  for row in results:
    with st.expander(f"{row['会社名']} ── 【{row['社名判定']}】"):

      if row.get("会社概要URL"):
        st.markdown(
            f"**会社概要URL:** [{row['会社概要URL']}]({row['会社概要URL']})"
        )
      else:
        st.write("**会社概要URL:** 確認できず")

      match_str = str(row["社名判定"])
      if match_str == "〇" or match_str.startswith("〇"):
        st.success(f"社名判定: {match_str}")
      elif (
          match_str.startswith("✕")
          or "✕" in match_str
          or "<a" in match_str
          or "[" in match_str
      ):
        st.error(f"社名判定: {match_str}")
      else:
        st.warning(f"社名判定: {match_str}")

      if row.get("拠点一覧") and row["拠点一覧"] != "なし":
        st.markdown(f"**拠点一覧:** [{row['拠点一覧']}]({row['拠点一覧']})")
      else:
        st.write("**拠点一覧:** なし")

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

      with st.expander("🔎 デバッグ：拠点一覧検索結果 (Q2)"):
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
