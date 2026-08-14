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
tavily_api_key = (
    os.getenv("TAVILY_API_KEY")
    or st.secrets.get("TAVILY_API_KEY", "")
)

gemini_key = (
    os.getenv("GEMINI_API_KEY")
    or st.secrets.get("GEMINI_API_KEY", "")
)


# ==========================================
# 1. Tavily API
# ==========================================
def fetch_tavily_results(
    query: str,
    api_key: str,
    include_domains=None
):
    try:
        client = TavilyClient(api_key=api_key)

        search_kwargs = {
            "query": query.strip().replace("`", ""),
            "search_depth": "basic",
            "max_results": 20
        }

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


# ==========================================
# URLからドメイン抽出
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
# 明らかな第三者サイト
# ==========================================
def is_excluded_domain(domain: str):
    if not domain:
        return True

    excluded_domains = [
        "wikipedia.org",
        "yahoo.co.jp",
        "news.yahoo.co.jp",
        "mynavi.jp",
        "rikunabi.com",
        "en-japan.com",
        "wantedly.com",
        "indeed.com",
        "metoree.com",
        "navitime.co.jp",
        "baseconnect.in",
        "alarmbox.jp",
        "bigcompany.jp"
    ]

    return any(
        domain == excluded
        or domain.endswith("." + excluded)
        for excluded in excluded_domains
    )


# ==========================================
# 公式サイト候補スコアリング
# ==========================================
def score_official_candidate(
    company: str,
    result: dict
):
    title = result.get("title", "")
    snippet = result.get("snippet", "")
    url = result.get("url", "")

    title_lower = title.lower()
    snippet_lower = snippet.lower()
    url_lower = url.lower()

    score = 0

    if company.lower() in title_lower:
        score += 10

    if company.lower() in snippet_lower:
        score += 5

    official_title_words = [
        "公式",
        "会社概要",
        "会社情報",
        "企業情報",
        "コーポレート",
        "corporate",
        "company"
    ]

    for word in official_title_words:
        if word.lower() in title_lower:
            score += 5

    company_clean = (
        company
        .replace("株式会社", "")
        .replace("有限会社", "")
        .replace("合同会社", "")
        .replace("ホールディングス", "")
        .replace("ホールディング", "")
        .replace("HD", "")
        .replace(" ", "")
        .replace("　", "")
        .lower()
    )

    if company_clean and company_clean in url_lower:
        score += 10

    official_path_words = [
        "/about",
        "/about_us",
        "/company",
        "/corporate",
        "/profile"
    ]

    for word in official_path_words:
        if word in url_lower:
            score += 2

    domain = extract_domain(url)

    if is_excluded_domain(domain):
        score -= 50

    return score


# ==========================================
# 公式サイト候補取得
# ==========================================
def find_official_domains(
    company: str,
    results: list
):
    candidates = []

    for result in results:
        domain = extract_domain(
            result.get("url", "")
        )

        if not domain:
            continue

        if is_excluded_domain(domain):
            continue

        score = score_official_candidate(
            company,
            result
        )

        candidates.append({
            "domain": domain,
            "score": score,
            "title": result.get("title", ""),
            "url": result.get("url", "")
        })

    candidates.sort(
        key=lambda x: x["score"],
        reverse=True
    )

    unique_candidates = []
    seen_domains = set()

    for candidate in candidates:
        domain = candidate["domain"]

        if domain in seen_domains:
            continue

        seen_domains.add(domain)
        unique_candidates.append(candidate)

    return unique_candidates[:3]


# ==========================================
# 検索
# ==========================================
def search_multi_queries(
    keyword: str,
    api_key: str
):
    # ------------------------------------------
    # q1：公式サイト探索
    # ------------------------------------------
    q1 = f'"{keyword}" 会社概要 公式サイト'

    res1 = fetch_tavily_results(
        q1,
        api_key
    )

    official_candidates = find_official_domains(
        keyword,
        res1
    )

    official_domains = []

    for candidate in official_candidates:
        if candidate["score"] >= 10:
            official_domains.append(
                candidate["domain"]
            )

    # ------------------------------------------
    # q2：九州拠点・事業部探索
    # ------------------------------------------
    q2 = (
        f'"{keyword}" 九州 福岡 '
        f'支店 支社 営業所 事業所 '
        f'事業部 法人営業 法人事業 拠点'
    )

    if official_domains:
        res2 = fetch_tavily_results(
            q2,
            api_key,
            include_domains=official_domains
        )
    else:
        res2 = fetch_tavily_results(
            q2,
            api_key
        )

    # ------------------------------------------
    # 結合
    # ------------------------------------------
    all_results = []
    seen_urls = set()

    for result in res1 + res2:
        url = result.get("url", "")

        if url and url not in seen_urls:
            seen_urls.add(url)
            all_results.append(result)

    if not all_results:
        return "", [], official_candidates

    # AIに渡すのは最大20件
    context = "\n".join(
        [
            f"- タイトル: {r['title']}\n"
            f"  内容: {r['snippet']}\n"
            f"  URL: {r['url']}"
            for r in all_results[:20]
        ]
    )

    return (
        context,
        all_results,
        official_candidates
    )


# ==========================================
# JSONパース
# ==========================================
def safe_parse_json(text):
    try:
        return json.loads(text)

    except json.JSONDecodeError:
        text = re.sub(
            r"```json|```",
            "",
            text
        ).strip()

        match = re.search(
            r"\[.*\]|\{.*\}",
            text,
            re.DOTALL
        )

        if match:
            return json.loads(
                match.group(0)
            )

        raise


# ==========================================
# AI分析
# ==========================================
def analyze_companies_batch(
    batch_data,
    gemini_key
):
    client = genai.Client(
        api_key=gemini_key
    )

    prompt_targets = ""

    for i, item in enumerate(batch_data):
        prompt_targets += (
            f"\n=== 対象企業 {i + 1}: "
            f"{item['company']} ===\n"
            f"【検索結果】\n"
            f"{item['context']}\n"
        )

    template = """
あなたは企業の拠点情報を厳密に抽出する専門家です。
ハルシネーションを厳禁とします。

最重要事項：
あなたの仕事は、検索結果にある情報を正確に抽出・整理することです。
企業の拠点を推測・補完・一般化してはいけません。

以下の企業について、提供された検索結果だけを使用してJSONを返してください。

{prompt_targets}


【会社】

companyには入力された会社名をそのまま入れてください。


【official_url】

対象企業自身の公式コーポレートサイトURLを入れてください。

Wikipedia、求人サイト、ニュースサイト、企業情報サイト、
Baseconnect、Metoree、Yahoo!、まとめサイト等はofficial_urlにしないでください。

対象企業自身の公式サイトと確認できない場合はnullにしてください。


【九州拠点の判定】

判定基準は非常に単純です。

「⭕️九州拠点あり」
→ 対象企業自身の現在の公式サイト・公式発表等に、
   九州内の具体的な自社拠点が確認できる場合。

「❌九州拠点なし」
→ 対象企業自身の現在の公式サイト・公式発表等に、
   九州内の具体的な自社拠点が確認できない場合。

第三者サイトにだけ九州拠点がある場合は❌です。

「❓判定不明」
→ 公式情報同士が明確に矛盾する場合など、
   極めて例外的な場合のみ。

単に検索結果が不足している場合や、
第三者サイトに拠点が存在する場合だけで❓にしないでください。


【公式情報を最優先】

以下の順序で判断してください。

1. 対象企業自身の現在の公式サイト
2. 対象企業自身の公式ニュース・公式発表
3. 官公庁・自治体等
4. 第三者情報

特に公式サイト内の、

- 事業所一覧
- 拠点一覧
- 支店一覧
- 営業所一覧
- 国内拠点一覧
- 法人事業部一覧
- 法人営業拠点一覧

を最優先してください。


【別法人の除外】

入力された企業と別法人の、

- 子会社
- 関連会社
- グループ会社
- 子会社の事業所
- 関連会社の事業所

は、入力企業の拠点として扱ってはいけません。

例：

入力企業：
ニデック株式会社

検索結果：
ニデックテクノモータ株式会社 九州事業所

これは別法人なので、ニデック株式会社のdetailsには絶対に入れないでください。


【住所の扱い】

住所が実在するだけでは企業拠点とは認定しないでください。

第三者サイトに、

「福岡県福岡市博多区下川端町2-1」

と書いてあったとしても、

対象企業自身の現在の公式情報でその住所を自社拠点として確認できなければ、
detailsには入れないでください。

建物名・住所に企業名が含まれているだけでも不十分です。


【detailsは「具体的な拠点の転記」のみ】

detailsには、検索結果に明確に記載されている
具体的な拠点だけを入れてください。

禁止：

- 「九州エリア」
- 「九州各県」
- 「福岡エリア」
- 「九州の店舗・事業所」
- 「九州地区の拠点」
- 「九州を中心とした拠点」
- 複数拠点をまとめた表現
- AIによる要約
- AIによる拠点名の創作
- AIによる住所の補完

例えば検索結果に、

「法人事業部福岡」

と書いてあれば、
nameは「法人事業部福岡」としてください。

「九州エリア店舗・事業所」
などとは言い換えないでください。


【name】

検索結果・公式ページに記載されている正式な拠点名を、
可能な限りそのまま転記してください。


【address】

検索結果・公式ページに記載されている住所を、
そのまま転記してください。

住所が「福岡県」までしか確認できなければ、
「福岡県」としてください。

不足している住所を推測してはいけません。


【url】

その拠点が掲載されている対象企業自身の公式URLを入れてください。

会社トップページだけでなく、
可能な限り拠点一覧・事業所一覧などの直接URLを使用してください。


【営業・事業拠点を優先】

九州拠点が複数ある場合は以下を優先してください。

1. 法人事業部
2. 法人営業部
3. 営業所
4. 営業拠点
5. 支店
6. 支社
7. 事業所
8. リフォーム事業部
9. その他の恒常的な事業拠点
10. 物流センター・配送センター・DC・倉庫

物流・配送拠点だけが確認できる場合は対象候補ですが、
営業・事業部拠点が確認できるならそちらを優先してください。


【店舗】

対象企業自身が運営する店舗は、
店舗自体が営業・事業拠点として明確に公式掲載されている場合のみ対象としてください。

ただし、今回の目的は「営業・事業拠点」の把握なので、
同じ企業に法人事業部・営業所等が確認できる場合は、
そちらを優先してください。


【現在性】

現在の公式サイトに掲載されている拠点を優先してください。

過去の移転告知だけで、
現在の公式情報で継続が確認できない場合はdetailsに入れないでください。

第三者サイトに古い拠点が残っていても無視してください。


【⭕️の必須条件】

「⭕️九州拠点あり」にする場合、
必ずdetailsを1件以上記載してください。

detailsには必ず、

- 具体的な拠点名
- 九州の住所
- 対象企業自身の公式URL

が必要です。

これらを確認できない場合は⭕️にしないでください。


【❌】

対象企業自身の現在の公式情報に
具体的な九州拠点が確認できなければ、
原則として❌にしてください。

第三者情報だけに九州支店などが残っていても❌です。


【reason】

判定理由を1～2件だけ記載してください。

⭕️：
「対象企業公式の事業所一覧に九州支店が掲載」
など。

❌：
「対象企業公式の現在の拠点一覧に九州拠点が掲載されていない」
「確認できた九州拠点は別法人のもの」
など。

❓：
公式情報同士が矛盾する具体的な理由。


【notes】

2023年8月14日以降の重要トピックだけを入れてください。

対象：
- 社名変更
- 拠点新設
- 拠点移転
- 拠点拡張
- M&A
- グループ再編
- 組織変更
- 新規事業
- 大規模設備投資

関係ないニュースや上場情報は入れないでください。


必ず以下のJSONだけを返してください。

[
    {
        "company": "会社名",
        "official_url": "https://...",
        "is_found": "⭕️九州拠点あり",
        "reason": [
            "判定理由"
        ],
        "details": [
            {
                "name": "具体的な拠点名",
                "address": "具体的な住所",
                "url": "対象企業自身の公式URL"
            }
        ],
        "sales_keywords": [
            "キーワード1",
            "キーワード2",
            "キーワード3",
            "キーワード4",
            "キーワード5",
            "キーワード6",
            "キーワード7",
            "キーワード8",
            "キーワード9",
            "キーワード10"
        ],
        "notes": []
    }
]

is_foundは必ず以下のいずれか：

「⭕️九州拠点あり」
「❌九州拠点なし」
「❓判定不明」
"""

    prompt = template.replace(
        "{prompt_targets}",
        prompt_targets
    )

    try:
        response = client.models.generate_content(
            model="gemini-3.5-flash-lite",
            contents=prompt,
            config=types.GenerateContentConfig(
                response_mime_type="application/json"
            )
        )

        return safe_parse_json(
            response.text.strip()
        )

    except Exception as e:
        st.error(
            f"AI分析バッチ処理エラー: {str(e)}"
        )
        return []


# ==========================================
# 4. Streamlit UI
# ==========================================
with st.form(
    key="batch_search_form"
):

    raw_input = st.text_area(
        "会社名リストを入力（スプレッドシートからそのまま貼り付け可能）",
        placeholder="株式会社〇〇\n株式会社△△",
        height=150
    )

    submit_button = st.form_submit_button(
        "一括検索・分析を実行",
        type="primary"
    )


if submit_button:

    if not raw_input.strip():

        st.warning(
            "会社名を入力してください。"
        )

    elif (
        not tavily_api_key
        or not gemini_key
    ):

        st.error(
            "Streamlitの Secrets に "
            "TAVILY_API_KEY または GEMINI_API_KEY "
            "が設定されていません。"
        )

    else:

        lines = raw_input.strip().split("\n")

        company_list = []

        for line in lines:

            parts = line.split("\t")
            comp = parts[0].strip()

            if (
                comp
                and comp not in company_list
            ):
                company_list.append(comp)

        batch_results = []

        progress_bar = st.progress(0)
        status_text = st.empty()

        company_map = {}

        # ==========================================
        # Tavily検索
        # ==========================================
        status_text.text(
            "検索中..."
        )

        fetched_data = []

        for i, comp in enumerate(
            company_list
        ):

            (
                context,
                raw_results,
                official_candidates
            ) = search_multi_queries(
                comp,
                tavily_api_key
            )

            fetched_data.append({
                "company": comp,
                "context": context,
                "raw_results": raw_results,
                "official_candidates": official_candidates
            })

            progress_bar.progress(
                (
                    (i + 1)
                    / max(
                        len(company_list),
                        1
                    )
                ) * 0.5
            )

        # ==========================================
        # Gemini一括分析
        # ==========================================
        chunk_size = 10

        if fetched_data:

            status_text.text(
                "AIによる一括分析を実行中..."
            )

            for i in range(
                0,
                len(fetched_data),
                chunk_size
            ):

                chunk = fetched_data[
                    i:i + chunk_size
                ]

                res_list = analyze_companies_batch(
                    chunk,
                    gemini_key
                )

                if isinstance(
                    res_list,
                    list
                ):

                    for r in res_list:

                        comp_name = r.get(
                            "company"
                        )

                        if (
                            not r.get("official_url")
                            or r.get(
                                "official_url"
                            ) in [
                                "null",
                                ""
                            ]
                        ):
                            r["official_url"] = None

                        if r.get(
                            "is_found"
                        ) not in [
                            "⭕️九州拠点あり",
                            "❌九州拠点なし",
                            "❓判定不明"
                        ]:
                            r["is_found"] = "❓判定不明"

                        if not isinstance(
                            r.get("details"),
                            list
                        ):
                            r["details"] = []

                        if not isinstance(
                            r.get("sales_keywords"),
                            list
                        ):
                            r["sales_keywords"] = []

                        if not isinstance(
                            r.get("reason"),
                            list
                        ):
                            r["reason"] = []

                        if not isinstance(
                            r.get("notes"),
                            list
                        ):
                            r["notes"] = []

                        company_map[
                            comp_name
                        ] = r

                progress_bar.progress(
                    0.5
                    + (
                        (
                            i + len(chunk)
                        )
                        / len(fetched_data)
                    ) * 0.5
                )

        # ==========================================
        # 結果後処理
        # ==========================================
        kyushu_prefectures = [
            "福岡",
            "佐賀",
            "長崎",
            "熊本",
            "大分",
            "宮崎",
            "鹿児島"
        ]

        for comp in company_list:

            res = company_map.get(
                comp,
                {
                    "is_found": "❓判定不明",
                    "official_url": None,
                    "details": [],
                    "sales_keywords": [],
                    "reason": [],
                    "notes": []
                }
            )

            raw_details = res.get(
                "details",
                []
            )

            if not isinstance(
                raw_details,
                list
            ):
                raw_details = []

            # ------------------------------------------
            # 九州住所だけ残す
            # ------------------------------------------
            valid_details = []

            for d in raw_details:

                if not isinstance(
                    d,
                    dict
                ):
                    continue

                name = str(
                    d.get("name", "")
                ).strip()

                address = str(
                    d.get("address", "")
                ).strip()

                url = str(
                    d.get("url", "")
                ).strip()

                # --------------------------------------
                # 必須項目チェック
                # --------------------------------------
                if not name:
                    continue

                if not address:
                    continue

                if not url:
                    continue

                # --------------------------------------
                # 九州住所
                # --------------------------------------
                if not any(
                    pref in address
                    for pref in kyushu_prefectures
                ):
                    continue

                # --------------------------------------
                # URLドメイン
                # --------------------------------------
                domain = extract_domain(url)

                if not domain:
                    continue

                # 明らかな第三者URLを除外
                if is_excluded_domain(domain):
                    continue

                valid_details.append({
                    "name": name,
                    "address": address,
                    "url": url
                })

            # 重複削除
            unique_details = []
            seen_detail_keys = set()

            for d in valid_details:

                key = (
                    d["name"],
                    d["address"],
                    d["url"]
                )

                if key in seen_detail_keys:
                    continue

                seen_detail_keys.add(key)
                unique_details.append(d)

            valid_details = unique_details

            res["details"] = valid_details

            # ------------------------------------------
            # Python側で最終判定
            # ------------------------------------------
            current_status = res.get(
                "is_found"
            )

            # 具体的な公式拠点あり
            if valid_details:

                res["is_found"] = (
                    "⭕️九州拠点あり"
                )

            else:

                # 具体的な公式拠点がない場合
                # AIが⭕️と回答していても、そのまま使わない
                if current_status == "❓判定不明":
                    res["is_found"] = (
                        "❌九州拠点なし"
                    )

                elif current_status == "⭕️九州拠点あり":
                    res["is_found"] = (
                        "❌九州拠点なし"
                    )

                else:
                    res["is_found"] = (
                        "❌九州拠点なし"
                    )

            # ------------------------------------------
            # reasonを整理
            # ------------------------------------------
            if res["is_found"] == "⭕️九州拠点あり":

                if not res.get("reason"):
                    res["reason"] = [
                        "対象企業自身の公式情報に具体的な九州拠点が掲載されている"
                    ]

            else:

                if not res.get("reason"):

                    res["reason"] = [
                        "対象企業自身の公式情報から具体的な九州拠点を確認できない"
                    ]

            # ------------------------------------------
            # official_url
            # ------------------------------------------
            official_url = res.get(
                "official_url"
            )

            if (
                not official_url
                or official_url in [
                    "null",
                    ""
                ]
            ):
                official_url = None

            # ------------------------------------------
            # details summary
            # ------------------------------------------
            details_summary = ", ".join(
                f"{d['name']} ({d['address']})"
                for d in valid_details
            )

            # ------------------------------------------
            # keywords
            # ------------------------------------------
            keywords = res.get(
                "sales_keywords",
                []
            )

            if not isinstance(
                keywords,
                list
            ):
                keywords = []

            keywords_summary = ", ".join(
                str(x)
                for x in keywords
            )

            # ------------------------------------------
            # reason
            # ------------------------------------------
            reason = res.get(
                "reason",
                []
            )

            if isinstance(
                reason,
                list
            ):
                reason_text = " / ".join(
                    str(x)
                    for x in reason
                )
            else:
                reason_text = (
                    str(reason)
                    if reason
                    else ""
                )

            # ------------------------------------------
            # notes
            # ------------------------------------------
            notes = res.get(
                "notes",
                []
            )

            if isinstance(
                notes,
                list
            ):
                notes_text = ", ".join(
                    str(x)
                    for x in notes
                )
            else:
                notes_text = (
                    str(notes)
                    if notes
                    else ""
                )

            # ------------------------------------------
            # 検索結果
            # ------------------------------------------
            raw_search_results = []

            for item in fetched_data:

                if item["company"] == comp:

                    raw_search_results = item.get(
                        "raw_results",
                        []
                    )

                    break

            # ------------------------------------------
            # 公式候補
            # ------------------------------------------
            official_candidates = []

            for item in fetched_data:

                if item["company"] == comp:

                    official_candidates = item.get(
                        "official_candidates",
                        []
                    )

                    break

            # ------------------------------------------
            # 結果保存
            # ------------------------------------------
            batch_results.append({
                "会社名": comp,
                "公式サイト": official_url,
                "判定": res["is_found"],
                "九州拠点": (
                    details_summary
                    if details_summary
                    else "なし"
                ),
                "フックキーワード": keywords_summary,
                "特記事項": notes_text,

                "_raw_details": valid_details,
                "_raw_keywords": keywords,
                "_raw_reason": reason_text,
                "_raw_notes": notes_text,
                "_raw_search_results": raw_search_results,
                "_official_candidates": official_candidates
            })

        progress_bar.progress(1.0)

        status_text.text(
            "すべての処理が完了しました。"
        )

        st.session_state[
            "batch_results"
        ] = batch_results


# ==========================================
# 5. 一覧表示
# ==========================================
if (
    "batch_results" in st.session_state
    and st.session_state["batch_results"]
):

    results = st.session_state[
        "batch_results"
    ]

    st.divider()

    st.subheader(
        "検索・分析結果一覧"
    )

    df_display = pd.DataFrame(
        results
    )

    expected_columns = [
        "会社名",
        "公式サイト",
        "判定",
        "九州拠点",
        "フックキーワード",
        "特記事項"
    ]

    for col in expected_columns:

        if col not in df_display.columns:
            df_display[col] = ""

    df_display = df_display[
        expected_columns
    ]

    st.dataframe(
        df_display,
        column_config={
            "公式サイト": st.column_config.LinkColumn(
                "公式サイト",
                help="クリックすると公式HPが開きます"
            )
        },
        use_container_width=True
    )

    # ==========================================
    # TSVコピー
    # ==========================================
    tsv_text = df_display.to_csv(
        sep="\t",
        index=False
    )

    with st.expander(
        "スプレッドシート用の一括コピー（タブ区切りテキスト）"
    ):

        st.markdown(
            "下の枠内のテキストをコピーして、"
            "スプレッドシートにそのまま貼り付けることができます。"
        )

        st.code(
            tsv_text,
            language="text"
        )

    # ==========================================
    # CSV
    # ==========================================
    csv_data = df_display.to_csv(
        index=False
    ).encode("utf-8-sig")

    st.download_button(
        label="結果をCSVでダウンロード",
        data=csv_data,
        file_name="kyushu_corporate_search_results.csv",
        mime="csv",
        type="primary"
    )

    # ==========================================
    # カード表示
    # ==========================================
    st.divider()

    st.subheader(
        "各社詳細・カード表示"
    )

    for r in results:

        with st.expander(
            f"{r['会社名']} ── 【 {r['判定']} 】"
        ):

            # --------------------------------------
            # 公式サイト
            # --------------------------------------
            if r.get("公式サイト"):

                st.markdown(
                    f"**公式サイト:** "
                    f"[{r['公式サイト']}]"
                    f"({r['公式サイト']})"
                )

            # --------------------------------------
            # 判定根拠
            # --------------------------------------
            if r.get("_raw_reason"):

                st.info(
                    f"**判定根拠:** "
                    f"{r['_raw_reason']}"
                )

            # --------------------------------------
            # 特記事項
            # --------------------------------------
            if r.get("_raw_notes"):

                st.info(
                    f"**特記事項:** "
                    f"{r['_raw_notes']}"
                )

            # --------------------------------------
            # キーワード
            # --------------------------------------
            if r.get("_raw_keywords"):

                st.markdown(
                    "**フックキーワード:**"
                )

                st.markdown(
                    " ".join(
                        f"`{kw}`"
                        for kw in r["_raw_keywords"]
                    )
                )

            # --------------------------------------
            # 拠点詳細
            # --------------------------------------
            if r.get("_raw_details"):

                st.markdown(
                    "**拠点詳細:**"
                )

                for d in r["_raw_details"]:

                    with st.container(
                        border=True
                    ):

                        st.markdown(
                            f"**{d['name']}**"
                        )

                        st.write(
                            f"住所: {d['address']}"
                        )

                        st.markdown(
                            f"[詳細リンク]"
                            f"({d['url']})"
                        )

            # --------------------------------------
            # 公式サイト候補
            # --------------------------------------
            if r.get("_official_candidates"):

                with st.expander(
                    "公式サイト候補を確認"
                ):

                    for candidate in r[
                        "_official_candidates"
                    ]:

                        st.write(
                            f"スコア: "
                            f"{candidate.get('score')}"
                        )

                        st.write(
                            f"ドメイン: "
                            f"{candidate.get('domain')}"
                        )

                        st.write(
                            f"タイトル: "
                            f"{candidate.get('title')}"
                        )

                        if candidate.get("url"):

                            st.markdown(
                                f"[URL]"
                                f"({candidate.get('url')})"
                            )

                        st.divider()

            # --------------------------------------
            # Tavily検索結果
            # --------------------------------------
            if r.get("_raw_search_results"):

                with st.expander(
                    "Tavily検索結果を確認"
                ):

                    for sr in r[
                        "_raw_search_results"
                    ]:

                        st.markdown(
                            f"**{sr.get('title', '')}**"
                        )

                        if sr.get("snippet"):

                            st.write(
                                sr.get("snippet")
                            )

                        if sr.get("url"):

                            st.markdown(
                                f"[URL]("
                                f"{sr.get('url')}"
                                f")"
                            )

                        st.divider()
