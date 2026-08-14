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
# 1. Tavily API 実行関数
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
# 明らかな第三者サイトを除外
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
        "navitime.co.jp"
    ]

    return any(
        domain == excluded
        or domain.endswith("." + excluded)
        for excluded in excluded_domains
    )


# ==========================================
# 公式サイト候補のスコアリング
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

    # ------------------------------------------
    # 会社名がタイトルにある
    # ------------------------------------------
    if company.lower() in title_lower:
        score += 10

    # ------------------------------------------
    # 会社名が本文にある
    # ------------------------------------------
    if company.lower() in snippet_lower:
        score += 5

    # ------------------------------------------
    # 公式サイトらしいタイトル
    # ------------------------------------------
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

    # ------------------------------------------
    # URLに会社名の主要部分が含まれる
    # ------------------------------------------
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

    # ------------------------------------------
    # 公式サイトでよく使われるパス
    # ------------------------------------------
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

    # ------------------------------------------
    # 明らかな第三者サイトは大幅減点
    # ------------------------------------------
    domain = extract_domain(url)

    if is_excluded_domain(domain):
        score -= 50

    return score


# ==========================================
# 公式ドメイン候補を取得
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
    # ==========================================
    # 1回目：会社概要・公式サイト検索
    # ==========================================
    q1 = f'"{keyword}" 会社概要 公式サイト'

    res1 = fetch_tavily_results(
        q1,
        api_key
    )

    # ==========================================
    # 公式ドメイン候補
    # ==========================================
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

    # ==========================================
    # 2回目：九州拠点検索
    # ==========================================
    q2 = (
        f'"{keyword}" 九州 福岡 '
        f'拠点 事業所 事業部 '
        f'法人事業 法人営業 '
        f'支店 営業所'
    )

    # ------------------------------------------
    # 公式ドメインを特定できた場合は
    # 公式サイト内を検索
    # ------------------------------------------
    if official_domains:
        res2 = fetch_tavily_results(
            q2,
            api_key,
            include_domains=official_domains
        )

    else:
        # --------------------------------------
        # 公式ドメインを確実に特定できない場合
        # 間違った第三者ドメインに限定しない
        # --------------------------------------
        res2 = fetch_tavily_results(
            q2,
            api_key
        )

    # ==========================================
    # 検索結果統合
    # ==========================================
    all_results = []
    seen_urls = set()

    for result in res1 + res2:
        url = result.get("url", "")

        if url and url not in seen_urls:
            seen_urls.add(url)
            all_results.append(result)

    if not all_results:
        return "", [], official_candidates

    # ==========================================
    # AIに渡す検索コンテキスト
    # ==========================================
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
# 2. JSONパース安全装置
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
# 3. 複数社を一括でAI分析
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
あなたは企業の所在調査のプロフェッショナルです。
ハルシネーションを厳禁とします。

提供された検索結果から確認できない情報を推測・補完してはいけません。

以下の複数の企業について、それぞれ提供された検索結果を基に厳密に調査し、
結果を必ずJSONの配列（リスト）で返してください。

{prompt_targets}


各企業ごとの共通指示:


1. "company"

入力された会社名をそのまま格納してください。


2. "official_url"

対象企業自身の公式サイトのコーポレートサイトURLを記載してください。

Wikipedia、求人サイト、ニュースサイト、企業情報サイト、
まとめサイト等は公式サイトとして扱わないでください。

検索結果から対象企業自身の公式サイトであることを確認できない場合は
null としてください。


3. "is_found"

以下の3つのいずれかを設定してください。


"⭕️九州拠点あり"

対象企業自身が現在運営している九州内の直営拠点が、
対象企業自身の公式サイトまたは公式発表等で明確に確認できる場合。


"❌九州拠点なし"

対象企業自身の公式サイトまたは公式発表等を確認しても、
現在の九州内拠点が確認できない場合。

今回の実務上の判定では、

「対象企業自身の公式情報で現在の九州拠点を確認できる」
→ 「⭕️九州拠点あり」

「対象企業自身の公式情報で現在の九州拠点を確認できない」
→ 原則「❌九州拠点なし」

としてください。


第三者情報にのみ九州拠点が掲載されている場合は、
対象企業自身の公式情報で確認できないため、
原則「❌九州拠点なし」としてください。


"❓判定不明"

以下のような例外的な場合のみ使用してください。

- 対象企業自身の現在の公式情報同士が明確に矛盾している
- 対象企業自身の公式ページはあるが、拠点の運営主体が対象企業か別法人か判別できない
- その他、公式情報だけでは合理的に⭕️・❌のどちらにも確定できない特殊なケース

単に第三者情報が残っている、
古い住所が見つかる、
検索結果が不十分、
という理由だけで❓にしないでください。

原則として、

公式に現在の九州拠点あり
→ ⭕️

公式に現在の九州拠点確認できず
→ ❌

です。


【最重要：九州拠点の根拠URL】

九州拠点を⭕️と判定する場合、
必ずその拠点自体を確認できる対象企業自身の公式URLを根拠としてください。

「official_url」が対象企業の公式サイトであることと、
「details」の拠点が公式に掲載されていることは別々に確認してください。

対象企業の会社概要やトップページだけが公式であっても、
そこに九州拠点が書かれていなければ、
それだけで九州拠点ありとは判定しないでください。

九州拠点の根拠URLが第三者サイトしかない場合は、
「⭕️九州拠点あり」と判定してはいけません。

第三者サイトにのみ記載された住所・支店名・営業所名は、
現在の自社拠点の根拠として採用しないでください。


【住所の扱い】

住所が実在することと、
対象企業が現在そこに拠点を置いていることは別です。

例えば、
「福岡県福岡市博多区下川端町2-1」
という住所が第三者サイトに存在していても、
その住所が対象企業の現在の公式拠点一覧等に掲載されていなければ、
対象企業の現在の九州拠点として採用してはいけません。

建物名や住所に企業名が含まれているだけでも、
対象企業自身の現在の拠点とは判定しないでください。


【最重要：子会社・関連会社・グループ会社】

入力された会社名とは別法人の子会社・関連会社・グループ会社の拠点は、
入力された会社自身の九州拠点として扱わないでください。

例えば、

「ニデック株式会社」

に対して、

「ニデックテクノモータ株式会社 九州事業所」

が確認された場合、

ニデックテクノモータ株式会社は別法人なので、
ニデック株式会社自身の九州拠点として扱ってはいけません。

この拠点はdetailsにも記載しないでください。

別法人の九州拠点しか確認できず、
対象企業自身の公式九州拠点が確認できない場合は
「❌九州拠点なし」としてください。


【重要：持株会社】

対象企業が持株会社の場合、
子会社・グループ会社の九州拠点を持株会社自身の拠点として扱わないでください。

例えば、

「株式会社ヤマダホールディングス」

に対して、

「ヤマダホームズ 九州北支店」
「ヤマダデンキ 九州テックランド」

などが確認されても、

それらは株式会社ヤマダホールディングス自身の拠点ではありません。


【情報源の優先順位】

以下の順で情報を重視してください。

1. 対象企業自身の現在の公式サイト
2. 対象企業自身の公式発表・公式ニュースリリース
3. 官公庁・自治体などの公的情報
4. その他の第三者情報


公式サイト内の現在の

- 事業所一覧
- 拠点一覧
- 営業所一覧
- 支店一覧
- 国内拠点一覧
- 事業部一覧
- 法人営業拠点一覧

などを最優先してください。


【第三者情報の扱い】

Yahoo!、求人サイト、企業情報サイト、
ニュースサイト、Baseconnect、Metoree、Wikipedia、
まとめサイト等の第三者情報だけでは、
九州拠点ありと判定してはいけません。

第三者情報に九州拠点が掲載されていても、
対象企業自身の公式情報で現在の九州拠点を確認できなければ、
原則「❌九州拠点なし」としてください。

公式情報と第三者情報が矛盾する場合は、
原則として公式情報を優先してください。


【現在性】

過去の拠点情報、
閉鎖済み拠点、
移転前の拠点、
統合・再編前の拠点だけでは「⭕️」にしないでください。

対象企業自身の現在の公式サイトで、
現在も営業・事業活動を行っていることが確認できる情報を優先してください。

過去の公式情報しかなく、
現在の公式情報で継続を確認できない場合は
原則「❌九州拠点なし」としてください。

ただし、
現在の公式情報同士が矛盾している場合は
「❓判定不明」としてください。


4. "details"

九州内の対象企業自身が現在運営している事業拠点を記載してください。

特に以下の営業・事業活動の拠点を優先してください。

- 本社
- 支店
- 支社
- 営業所
- 事業所
- 法人営業部門
- 法人事業部
- 営業部門
- 営業本部
- リフォーム事業部
- その他の恒常的な営業・事業拠点


拠点名称に「支店」「営業所」「事業所」等が含まれていなくても、
対象企業自身が運営する恒常的な営業・事業拠点であることが
確認できれば対象としてください。


公式サイト内に

「事業所一覧」
「拠点一覧」
「営業所一覧」
「法人事業一覧」

等のページがある場合は、
個別の古いニュース記事や第三者サイトよりも、
現在の一覧ページを優先してください。


【営業・事業拠点を優先】

以下の順で優先してください。

1. 法人事業部・法人営業部
2. 営業所・営業拠点
3. 支店・支社
4. 事業所
5. リフォーム事業部等の恒常的な事業拠点
6. 物流センター・配送センター・DC


物流センター、配送センター、DC、倉庫等も、
対象企業自身が現在運営する恒常的な事業拠点であることが
公式情報から確認できる場合は対象候補としてください。

ただし、
営業・事業部・法人営業拠点が確認できる場合は、
物流・配送拠点よりもそちらを優先してください。


別法人が運営する物流施設・配送施設は対象外です。


【⭕️の場合の必須条件】

「⭕️九州拠点あり」と判定した場合は、
detailsに必ず少なくとも1件、
その判定根拠となった九州拠点を記載してください。

detailsのurlには、
その拠点を対象企業自身が運営していることを
確認できる公式URLを記載してください。

住所が完全に確認できない場合でも、
検索結果から確認できる範囲で記載してください。

情報がない部分を推測・補完してはいけません。

例えば住所が「福岡県」までしか確認できなければ、

"address": "福岡県"

としてください。


detailsに含めるには、以下をすべて満たしてください。

- 対象企業自身の拠点である
- 九州内にある
- 現在稼働している
- 公式情報で確認できる


第三者サイトしか根拠がない拠点はdetailsに含めないでください。

別法人の子会社・関連会社・グループ会社の拠点はdetailsに含めないでください。

確実な拠点が確認できない場合は[]としてください。

ただし、
「⭕️九州拠点あり」と判定した場合は、
必ず少なくとも1件detailsを記載してください。


5. "sales_keywords"

DX営業代行で相手に刺さるフックキーワードを10個のリストで返してください。

企業の事業内容や検索結果から確認できる特徴を踏まえてください。


6. "reason"

判定にかかわらず、
今回の判定に至った主な根拠を1～2個、簡潔に記載してください。

提供された検索結果から確認できる事実に基づいてください。

⭕️の場合：
対象企業自身の公式情報に九州拠点が掲載されていること。

❌の場合：
対象企業自身の公式情報で九州拠点が確認できないこと。
第三者情報にだけ九州拠点がある場合は、
その事実も簡潔に記載してください。

❓の場合：
公式情報同士が矛盾するなど、
なぜ通常の⭕️・❌では判定できないのかを記載してください。

必ず1～2個の簡潔な根拠を記載してください。


7. "notes"

提供された検索結果の中に、
ここ3年以内（2023年8月14日以降）の以下のいずれかの重要トピックが
明確に確認できる場合のみ、
日付と短い名詞句で簡潔に記載してください。

それ以外は必ず[]としてください。

- 社名変更・商号変更
- 拠点新設、移転、拡張
- M&A、グループ再編、組織変更
- 新規事業立ち上げ
- 大規模な設備投資

関係のないニュースや上場情報などはnotesに入れないでください。


必ず以下のJSON配列フォーマットのみで回答してください：

[
    {
        "company": "会社名",
        "official_url": "https://...",
        "is_found": "⭕️九州拠点あり",
        "reason": [
            "判定の主な根拠"
        ],
        "details": [
            {
                "name": "拠点名称",
                "address": "住所",
                "url": "https://..."
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

is_found は必ず次のいずれかにしてください：

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
                company_list.append(
                    comp
                )

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

                        # ----------------------------------
                        # official_url
                        # ----------------------------------
                        if (
                            not r.get(
                                "official_url"
                            )
                            or r.get(
                                "official_url"
                            ) in [
                                "null",
                                ""
                            ]
                        ):
                            r["official_url"] = None

                        # ----------------------------------
                        # is_found
                        # ----------------------------------
                        if r.get(
                            "is_found"
                        ) not in [
                            "⭕️九州拠点あり",
                            "❌九州拠点なし",
                            "❓判定不明"
                        ]:

                            r["is_found"] = (
                                "❓判定不明"
                            )

                        # ----------------------------------
                        # details
                        # ----------------------------------
                        if not isinstance(
                            r.get("details"),
                            list
                        ):

                            r["details"] = []

                        # ----------------------------------
                        # sales_keywords
                        # ----------------------------------
                        if not isinstance(
                            r.get("sales_keywords"),
                            list
                        ):

                            r["sales_keywords"] = []

                        # ----------------------------------
                        # reason
                        # ----------------------------------
                        if not isinstance(
                            r.get("reason"),
                            list
                        ):

                            r["reason"] = []

                        # ----------------------------------
                        # notes
                        # ----------------------------------
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
            # 九州住所のみ残す
            # ------------------------------------------
            valid_details = []

            for d in raw_details:

                if not isinstance(
                    d,
                    dict
                ):
                    continue

                addr = d.get(
                    "address",
                    ""
                )

                if any(
                    pref in addr
                    for pref in kyushu_prefectures
                ):

                    valid_details.append(
                        d
                    )

            res["details"] = valid_details

            # ------------------------------------------
            # ⭕️なのに詳細拠点がない場合
            # AIの判定をそのまま信用せず、
            # 判定不明に落とす
            # ------------------------------------------
            if (
                res.get("is_found")
                == "⭕️九州拠点あり"
                and not valid_details
            ):

                res["is_found"] = (
                    "❓判定不明"
                )

                res["reason"] = [
                    "九州拠点ありと判定されたが、具体的な九州内の自社拠点をdetailsとして確認できない"
                ]

            # ------------------------------------------
            # is_found
            # ------------------------------------------
            is_found_str = res.get(
                "is_found",
                "❓判定不明"
            )

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
            # details
            # ------------------------------------------
            details_summary = ", ".join(
                f"{d.get('name')} "
                f"({d.get('address')})"
                for d in valid_details
            )

            # ------------------------------------------
            # sales_keywords
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
            # Tavily検索結果
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
            # 公式サイト候補
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
            # 結果格納
            # ------------------------------------------
            batch_results.append({
                "会社名": comp,
                "公式サイト": official_url,
                "判定": is_found_str,
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
    and st.session_state[
        "batch_results"
    ]
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
    # スプレッドシート用コピー
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
    # CSVダウンロード
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
    # 各社詳細・カード表示
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
            if r.get(
                "公式サイト"
            ):

                st.markdown(
                    f"**公式サイト:** "
                    f"[{r['公式サイト']}]"
                    f"({r['公式サイト']})"
                )

            # --------------------------------------
            # 判定根拠
            # --------------------------------------
            if r.get(
                "_raw_reason"
            ):

                st.info(
                    f"**判定根拠:** "
                    f"{r['_raw_reason']}"
                )

            # --------------------------------------
            # 特記事項
            # --------------------------------------
            if r.get(
                "_raw_notes"
            ):

                st.info(
                    f"**特記事項:** "
                    f"{r['_raw_notes']}"
                )

            # --------------------------------------
            # フックキーワード
            # --------------------------------------
            if r.get(
                "_raw_keywords"
            ):

                st.markdown(
                    "**フックキーワード:**"
                )

                st.markdown(
                    " ".join(
                        f"`{kw}`"
                        for kw in r[
                            "_raw_keywords"
                        ]
                    )
                )

            # --------------------------------------
            # 九州拠点
            # --------------------------------------
            if r.get(
                "_raw_details"
            ):

                st.markdown(
                    "**拠点詳細:**"
                )

                for d in r[
                    "_raw_details"
                ]:

                    with st.container(
                        border=True
                    ):

                        st.markdown(
                            f"**{d.get('name')}**"
                        )

                        st.write(
                            f"住所: "
                            f"{d.get('address')}"
                        )

                        if (
                            d.get("url")
                            and d.get("url") != "null"
                        ):

                            st.markdown(
                                f"[詳細リンク]"
                                f"({d.get('url')})"
                            )

            # --------------------------------------
            # 公式サイト候補確認
            # --------------------------------------
            if r.get(
                "_official_candidates"
            ):

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

                        if candidate.get(
                            "url"
                        ):

                            st.markdown(
                                f"[URL]"
                                f"({candidate.get('url')})"
                            )

                        st.divider()

            # --------------------------------------
            # Tavily検索結果確認
            # --------------------------------------
            if r.get(
                "_raw_search_results"
            ):

                with st.expander(
                    "Tavily検索結果を確認"
                ):

                    for sr in r[
                        "_raw_search_results"
                    ]:

                        st.markdown(
                            f"**{sr.get('title', '')}**"
                        )

                        if sr.get(
                            "snippet"
                        ):

                            st.write(
                                sr.get("snippet")
                            )

                        if sr.get(
                            "url"
                        ):

                            st.markdown(
                                f"[URL]("
                                f"{sr.get('url')}"
                                f")"
                            )

                        st.divider()
