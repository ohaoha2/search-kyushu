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
# セッションステート初期化
# ==========================================
if "search_history" not in st.session_state:
    st.session_state.search_history = []

if "result_cache" not in st.session_state:
    st.session_state.result_cache = {}


# ==========================================
# 九州都道府県
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


# ==========================================
# 明らかな第三者サイトを除外
# ==========================================
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


# ==========================================
# 1. Tavily API 実行関数
# ※公式URL取得のため、ここは従来版を維持
# ==========================================
def fetch_tavily_results(
    query: str,
    api_key: str,
    include_domains=None
):

    try:

        client = TavilyClient(
            api_key=api_key
        )

        search_kwargs = {
            "query": query.strip().replace("`", ""),
            "search_depth": "basic",
            "max_results": 20
        }

        if include_domains:
            search_kwargs[
                "include_domains"
            ] = include_domains

        response = client.search(
            **search_kwargs
        )

        results = []

        for item in response.get(
            "results",
            []
        ):

            results.append({
                "title": item.get(
                    "title",
                    ""
                ),
                "url": item.get(
                    "url",
                    ""
                ),
                "snippet": item.get(
                    "content",
                    ""
                )
            })

        return results

    except Exception:
        return []


# ==========================================
# URLからドメイン抽出
# ==========================================
def extract_domain(
    url: str
):

    try:

        parsed = urlparse(
            url
        )

        if not parsed.netloc:
            return None

        domain = parsed.netloc.lower()

        if domain.startswith(
            "www."
        ):
            domain = domain[4:]

        return domain

    except Exception:
        return None


# ==========================================
# 明らかな第三者サイトを除外
# ==========================================
def is_excluded_domain(
    domain: str
):

    if not domain:
        return True

    return any(
        domain == excluded
        or domain.endswith(
            "." + excluded
        )
        for excluded in excluded_domains
    )


# ==========================================
# 公式サイト候補のスコアリング
# ※ここも従来版を維持
# ==========================================
def score_official_candidate(
    company: str,
    result: dict
):

    title = result.get(
        "title",
        ""
    )

    snippet = result.get(
        "snippet",
        ""
    )

    url = result.get(
        "url",
        ""
    )

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
    # 会社名が本文にもある
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
    # URLから会社名を推測しやすい場合
    # ------------------------------------------
    company_clean = (
        company
        .replace(
            "株式会社",
            ""
        )
        .replace(
            "有限会社",
            ""
        )
        .replace(
            "合同会社",
            ""
        )
        .replace(
            "ホールディングス",
            ""
        )
        .replace(
            "ホールディング",
            ""
        )
        .replace(
            "HD",
            ""
        )
        .replace(
            " ",
            ""
        )
        .replace(
            "　",
            ""
        )
        .lower()
    )

    if company_clean:

        if company_clean in url_lower:
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
    # 明らかな第三者サイト
    # ------------------------------------------
    domain = extract_domain(
        url
    )

    if is_excluded_domain(
        domain
    ):
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
            result.get(
                "url",
                ""
            )
        )

        if not domain:
            continue

        if is_excluded_domain(
            domain
        ):
            continue

        score = score_official_candidate(
            company,
            result
        )

        candidates.append({
            "domain": domain,
            "score": score,
            "title": result.get(
                "title",
                ""
            ),
            "url": result.get(
                "url",
                ""
            )
        })

    candidates.sort(
        key=lambda x: x["score"],
        reverse=True
    )

    unique_candidates = []

    seen_domains = set()

    for candidate in candidates:

        domain = candidate[
            "domain"
        ]

        if domain in seen_domains:
            continue

        seen_domains.add(
            domain
        )

        unique_candidates.append(
            candidate
        )

    return unique_candidates[:3]


# ==========================================
# 2. 検索
# ※q1の公式URL取得は従来版そのまま
# ==========================================
def search_multi_queries(
    keyword: str,
    api_key: str
):

    # ==========================================
    # q1：会社概要・公式サイト
    # ==========================================
    q1 = (
        f'"{keyword}" 会社概要 公式サイト'
    )

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
    # q2：公式サイト内の九州拠点検索
    # ==========================================

    q2 = (
        f'"{keyword}" '
        f'九州 福岡 佐賀 長崎 熊本 大分 宮崎 鹿児島 '
        f'拠点 所在地 住所 '
        f'支店 支社 営業所 事業所 '
        f'事業部 営業部 法人営業 法人事業 '
        f'法人＆リフォーム リフォーム '
        f'営業拠点 拠点案内 事業所一覧 '
        f'拠点一覧 営業所一覧 会社情報'
    )

    # ==========================================
    # 重要：
    # 公式ドメインを取れた場合だけ
    # 公式ドメイン内検索
    #
    # 取れなかった場合は第三者検索しない
    # ==========================================
    if official_domains:

        res2 = fetch_tavily_results(
            q2,
            api_key,
            include_domains=official_domains
        )

    else:

        res2 = []

    # ==========================================
    # 検索結果を統合
    # ==========================================
    all_results = []

    seen_urls = set()

    for result in res1 + res2:

        url = result.get(
            "url",
            ""
        )

        if (
            url
            and url not in seen_urls
        ):

            seen_urls.add(
                url
            )

            all_results.append(
                result
            )

    if not all_results:

        return (
            "",
            [],
            official_candidates,
            official_domains
        )

    # ==========================================
    # AIに渡す検索コンテキスト
    # ==========================================
    context = "\n".join(
        [
            (
                f"- タイトル: {r['title']}\n"
                f"  内容: {r['snippet']}\n"
                f"  URL: {r['url']}"
            )
            for r in all_results[:20]
        ]
    )

    return (
        context,
        all_results,
        official_candidates,
        official_domains
    )


# ==========================================
# 3. JSONパース安全装置
# ==========================================
def safe_parse_json(
    text
):

    try:

        return json.loads(
            text
        )

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
# 4. 複数社を一括でAI分析
# ==========================================
def analyze_companies_batch(
    batch_data,
    gemini_key
):

    client = genai.Client(
        api_key=gemini_key
    )

    prompt_targets = ""

    for i, item in enumerate(
        batch_data
    ):

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

ただし、検索結果には公式サイトが含まれない場合があります。

その場合でも検索結果から推測してURLを作らないでください。

提供された検索結果から対象企業自身の公式サイトであることを確認できない場合はnull。


3. "is_found"

以下の3つのいずれかを設定してください。

"⭕️九州拠点あり"

対象企業自身が現在運営している九州内の具体的な拠点が、
対象企業の公式サイト検索結果から明確に確認できる場合。

"❌九州拠点なし"

対象企業自身に現在の九州拠点がないことを、
対象企業自身の公式情報から明確に確認できる場合。

"❓判定不明"

上記のどちらとも明確に確認できない場合。


重要：

「⭕️の根拠が見つからない」という理由だけで❌にしない。

ただし、今回提供されている検索結果が
対象企業の公式サイト内検索結果であり、
その検索結果の中に具体的な九州拠点が存在しない場合は、
❌を検討してよい。


【最優先】

対象企業自身の公式サイト内の

・事業所一覧
・拠点一覧
・営業所一覧
・会社情報
・事業部案内
・法人事業案内
・リフォーム事業案内

などの現在の情報。


【別法人除外】

以下は対象企業自身の拠点として扱わない。

- 子会社
- 関連会社
- グループ会社
- 別法人
- 代理店
- 販売店
- パートナー
- 協力会社
- 顧客先
- 施工現場
- 納入先


例えば、

対象企業：
ニデック株式会社

検索結果：
ニデックテクノモータ株式会社 九州事業所

これは別法人なので除外。


【重要：事業部を積極的に拾う】

対象企業自身の公式サイトに、

・法人事業部
・法人営業部
・法人＆リフォーム事業部
・リフォーム事業部
・営業部
・営業拠点
・事業部拠点

などが掲載されている場合、
「支店」「営業所」という名称でなくても対象とする。


4. "details"

九州内の対象企業自身の具体的な拠点を記載。

優先順位：

1. 本社
2. 支店
3. 支社
4. 営業所
5. 事業所
6. 法人営業部
7. 法人事業部
8. 法人＆リフォーム事業部
9. リフォーム事業部
10. 営業部
11. 営業拠点
12. その他の恒常的事業拠点

対象企業自身の公式サイトに現在掲載されているなら、
支店・営業所という名前に限定しない。


【物流施設】

物流センター、配送センター、DC、倉庫等も、
対象企業自身の公式拠点であることが確認できれば対象候補。

ただし、

「法人事業部 福岡」

などの営業・事業活動拠点が確認できる場合は、
物流拠点より営業・事業拠点を優先。


【禁止】

「九州エリア」
「九州各県」
「九州エリア店舗・事業所」
「九州の店舗」
などを1つの拠点としてまとめない。

具体的な拠点名と住所があるものだけ。


各拠点は以下。

{
    "name": "拠点名称",
    "address": "住所",
    "url": "公式ページURL"
}


5. "sales_keywords"

企業の実際の事業内容から、
DX営業代行で相手に刺さるフックキーワードを10個。


6. "reason"

判定にかかわらず、1～2個の簡潔な根拠を記載。

⭕️：
公式サイトの事業所一覧等に九州拠点が掲載されている。

❌：
公式サイト内の現在の拠点一覧等に九州拠点がなく、
検索結果上も具体的な九州拠点が確認できない。

❓：
公式サイトは確認できたが、
現在の九州拠点について十分な情報がない。

例：

["大林組公式サイトの事業所一覧に九州支店が掲載されている"]

["ニトリ公式の法人事業案内に福岡の法人＆リフォーム事業部が掲載されている"]

["ヤマダホールディングス公式サイトに同社自身の九州拠点を確認できず、九州の情報はグループ会社のものだった"]


7. "notes"

2023年8月14日以降の以下のみ。

- 社名変更
- 拠点新設
- 拠点移転
- 拠点拡張
- M&A
- グループ再編
- 組織変更
- 新規事業
- 大規模設備投資

なければ[]。


JSONのみ返してください。

[
    {
        "company": "会社名",
        "official_url": "https://...",
        "is_found": "⭕️九州拠点あり",
        "reason": [
            "判定の根拠"
        ],
        "details": [
            {
                "name": "拠点名",
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

is_found は必ず次のいずれか。

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
            ),
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
# 5. Streamlit UI
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


# ==========================================
# 6. 実行
# ==========================================
if submit_button:

    # ------------------------------------------
    # 今回はキャッシュを使用しない
    # ------------------------------------------
    st.session_state.result_cache = {}

    st.session_state.pop(
        "batch_results",
        None
    )

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
            "TAVILY_API_KEY または "
            "GEMINI_API_KEY が設定されていません。"
        )

    else:

        lines = raw_input.strip().split(
            "\n"
        )

        company_list = []

        for line in lines:

            parts = line.split(
                "\t"
            )

            comp = parts[0].strip()

            if (
                comp
                and comp not in company_list
            ):

                company_list.append(
                    comp
                )

        batch_results = []

        progress_bar = st.progress(
            0
        )

        status_text = st.empty()

        company_map = {}

        fetched_data = []

        # ======================================
        # Tavily検索
        # ======================================
        status_text.text(
            "検索中..."
        )

        for i, comp in enumerate(
            company_list
        ):

            (
                context,
                raw_results,
                official_candidates,
                official_domains
            ) = search_multi_queries(
                comp,
                tavily_api_key
            )

            fetched_data.append({
                "company": comp,
                "context": context,
                "raw_results": raw_results,
                "official_candidates":
                    official_candidates,
                "official_domains":
                    official_domains
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

        # ======================================
        # 会社名 → 検索結果
        # ======================================
        fetched_map = {
            item["company"]:
                item["raw_results"]
            for item in fetched_data
        }

        # ======================================
        # Gemini一括分析
        # ======================================
        chunk_size = 10

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

                    if not isinstance(
                        r.get(
                            "details"
                        ),
                        list
                    ):

                        r["details"] = []

                    if not isinstance(
                        r.get(
                            "sales_keywords"
                        ),
                        list
                    ):

                        r["sales_keywords"] = []

                    if not isinstance(
                        r.get(
                            "reason"
                        ),
                        list
                    ):

                        r["reason"] = []

                    if not isinstance(
                        r.get(
                            "notes"
                        ),
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

        # ======================================
        # 最終処理
        # ======================================
        for comp in company_list:

            res = company_map.get(
                comp,
                {
                    "is_found":
                        "❓判定不明",
                    "official_url":
                        None,
                    "details": [],
                    "sales_keywords":
                        [],
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

            # ----------------------------------
            # 九州住所のみ
            # ----------------------------------
            valid_details = []

            for d in raw_details:

                if not isinstance(
                    d,
                    dict
                ):
                    continue

                addr = str(
                    d.get(
                        "address",
                        ""
                    )
                )

                if not any(
                    pref in addr
                    for pref in kyushu_prefectures
                ):
                    continue

                valid_details.append(
                    d
                )

            # ----------------------------------
            # 重複除去
            # ----------------------------------
            unique_details = []

            seen_details = set()

            for d in valid_details:

                key = (
                    str(
                        d.get(
                            "name",
                            ""
                        )
                    ),
                    str(
                        d.get(
                            "address",
                            ""
                        )
                    ),
                    str(
                        d.get(
                            "url",
                            ""
                        )
                    )
                )

                if key in seen_details:
                    continue

                seen_details.add(
                    key
                )

                unique_details.append(
                    d
                )

            valid_details = unique_details

            res["details"] = (
                valid_details
            )

            # ----------------------------------
            # 「あり」なのに詳細なし
            # ----------------------------------
            if (
                res.get(
                    "is_found"
                )
                == "⭕️九州拠点あり"
                and not valid_details
            ):

                res["is_found"] = (
                    "❓判定不明"
                )

                res["reason"] = [
                    "九州拠点ありと判定されたが、具体的な九州拠点の詳細を確認できない"
                ]

            # ----------------------------------
            # 公式URL
            #
            # AI出力よりも、
            # q1で取得した公式URLを優先
            # ----------------------------------
            fetched_item = next(
                (
                    x
                    for x in fetched_data
                    if x["company"] == comp
                ),
                None
            )

            search_official_url = None

            if fetched_item:

                official_domains = (
                    fetched_item.get(
                        "official_domains",
                        []
                    )
                )

                if official_domains:

                    official_domain = (
                        official_domains[0]
                    )

                    # q1候補から、そのドメインのURL
                    # を取得
                    for candidate in (
                        fetched_item.get(
                            "official_candidates",
                            []
                        )
                    ):

                        if (
                            candidate.get(
                                "domain"
                            )
                            == official_domain
                        ):

                            search_official_url = (
                                candidate.get(
                                    "url"
                                )
                            )

                            break

            # AIが変なURLを返していても、
            # q1で取得したURLがあればそちらを使用
            official_url = (
                search_official_url
                if search_official_url
                else res.get(
                    "official_url"
                )
            )

            # ----------------------------------
            # details
            # ----------------------------------
            details_summary = ", ".join(
                f"{d.get('name')} "
                f"({d.get('address')})"
                for d in valid_details
            )

            # ----------------------------------
            # keywords
            # ----------------------------------
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

            # ----------------------------------
            # reason
            # ----------------------------------
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

            # ----------------------------------
            # notes
            # ----------------------------------
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

            # ----------------------------------
            # 検索結果
            # ----------------------------------
            raw_search_results = (
                fetched_map.get(
                    comp,
                    []
                )
            )

            official_candidates = []

            if fetched_item:

                official_candidates = (
                    fetched_item.get(
                        "official_candidates",
                        []
                    )
                )

            official_domains = []

            if fetched_item:

                official_domains = (
                    fetched_item.get(
                        "official_domains",
                        []
                    )
                )

            # ----------------------------------
            # 保存
            # ----------------------------------
            batch_results.append({
                "会社名": comp,
                "公式サイト": official_url,
                "判定": res.get(
                    "is_found",
                    "❓判定不明"
                ),
                "九州拠点": (
                    details_summary
                    if details_summary
                    else "なし"
                ),
                "フックキーワード":
                    keywords_summary,
                "特記事項":
                    notes_text,

                "_raw_details":
                    valid_details,
                "_raw_keywords":
                    keywords,
                "_raw_reason":
                    reason_text,
                "_raw_notes":
                    notes_text,
                "_raw_search_results":
                    raw_search_results,
                "_official_candidates":
                    official_candidates,
                "_official_domains":
                    official_domains
            })

        progress_bar.progress(
            1.0
        )

        status_text.text(
            "すべての処理が完了しました。"
        )

        st.session_state[
            "batch_results"
        ] = batch_results


# ==========================================
# 7. 一覧表示
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
            "公式サイト":
                st.column_config.LinkColumn(
                    "公式サイト",
                    help="クリックすると公式HPが開きます"
                )
        },
        use_container_width=True
    )

    # ======================================
    # TSV
    # ======================================
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

    # ======================================
    # CSV
    # ======================================
    csv_data = df_display.to_csv(
        index=False
    ).encode(
        "utf-8-sig"
    )

    st.download_button(
        label="結果をCSVでダウンロード",
        data=csv_data,
        file_name="kyushu_corporate_search_results.csv",
        mime="csv",
        type="primary"
    )

    # ======================================
    # カード
    # ======================================
    st.divider()

    st.subheader(
        "各社詳細・カード表示"
    )

    for r in results:

        with st.expander(
            f"{r['会社名']} ── 【 {r['判定']} 】"
        ):

            # ----------------------------------
            # 公式サイト
            # ----------------------------------
            if r.get(
                "公式サイト"
            ):

                st.markdown(
                    f"**公式サイト:** "
                    f"[{r['公式サイト']}]"
                    f"({r['公式サイト']})"
                )

            # ----------------------------------
            # 判定根拠
            # ----------------------------------
            if r.get(
                "_raw_reason"
            ):

                st.info(
                    f"**判定根拠:** "
                    f"{r['_raw_reason']}"
                )

            # ----------------------------------
            # 特記事項
            # ----------------------------------
            if r.get(
                "_raw_notes"
            ):

                st.info(
                    f"**特記事項:** "
                    f"{r['_raw_notes']}"
                )

            # ----------------------------------
            # フックキーワード
            # ----------------------------------
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

            # ----------------------------------
            # 九州拠点
            # ----------------------------------
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
                            and d.get("url")
                            != "null"
                        ):

                            st.markdown(
                                f"[詳細リンク]"
                                f"({d.get('url')})"
                            )

            # ----------------------------------
            # 公式サイト候補
            # ----------------------------------
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

            # ----------------------------------
            # Tavily検索結果
            # ----------------------------------
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
                                sr.get(
                                    "snippet"
                                )
                            )

                        if sr.get(
                            "url"
                        ):

                            st.markdown(
                                f"[URL]"
                                f"({sr.get('url')})"
                            )

                        st.divider()
