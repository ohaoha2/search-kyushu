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
# 0. セッションステート初期化
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
# 1. Tavily API 実行関数
# ※ここは元コードを維持
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
# ※ここは元コードを維持
# ==========================================
def extract_domain(url: str):

    try:

        parsed = urlparse(
            url
        )

        if not parsed.netloc:
            return None

        domain = parsed.netloc.lower()

        if domain.startswith("www."):
            domain = domain[4:]

        return domain

    except Exception:
        return None


# ==========================================
# 公式サイト候補のスコアリング
# ※ここは元コードを維持
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
# ※ここは元コードを維持
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

        domain = candidate["domain"]

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
#
# ★ q1は元コードのまま
# ★ q2だけ公式サイト内検索として強化
# ==========================================
def search_multi_queries(
    keyword: str,
    api_key: str
):

    # ==========================================
    # q1：会社概要・公式サイト検索
    # ★ 元コードそのまま
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
    # ★ 元コードそのまま
    # ==========================================
    official_candidates = find_official_domains(
        keyword,
        res1
    )

    # 「確実に公式候補とみなせるドメイン」
    official_domains = []

    for candidate in official_candidates:

        if candidate["score"] >= 10:

            official_domains.append(
                candidate["domain"]
            )

    # ==========================================
    # q2：九州拠点検索
    #
    # ★ ここだけ変更
    # ==========================================
    #
    # 会社名検索ではなく、
    # 取得した公式ドメイン内を検索する。
    #
    # ニトリなら
    # site:nitori.co.jp ...
    #
    # 大林組なら
    # site:obayashi.co.jp ...
    #
    # という形。
    #
    # ==========================================

    if official_domains:

        q2_queries = [

            (
                f'site:{domain} '
                f'九州 福岡 佐賀 長崎 熊本 大分 宮崎 鹿児島 '
                f'支店 支社 営業所 事業所'
            )

            for domain in official_domains[:1]
        ]

        q2_queries += [

            (
                f'site:{domain} '
                f'九州 福岡 '
                f'事業部 営業部 法人営業 法人事業 '
                f'法人＆リフォーム リフォーム事業部 '
                f'営業拠点 拠点一覧 事業所一覧'
            )

            for domain in official_domains[:1]
        ]

        q2_queries += [

            (
                f'site:{domain} '
                f'九州 福岡 '
                f'会社情報 拠点 所在地 住所'
            )

            for domain in official_domains[:1]
        ]

        res2 = []

        seen_q2_urls = set()

        for q2 in q2_queries:

            current_results = fetch_tavily_results(
                q2,
                api_key,
                include_domains=official_domains[:1]
            )

            for result in current_results:

                url = result.get(
                    "url",
                    ""
                )

                if (
                    url
                    and url not in seen_q2_urls
                ):

                    seen_q2_urls.add(
                        url
                    )

                    res2.append(
                        result
                    )

    else:

        # ------------------------------------------
        # 重要：
        # 公式ドメインが分からない場合は
        # 第三者検索をしない
        # ------------------------------------------
        res2 = []

    # ==========================================
    # q1 / q2 を分離して返す
    #
    # ★ ここが今回の重要ポイント
    # ==========================================
    #
    # 以前はq1とq2を混ぜてAIへ渡していた。
    #
    # 今回は、
    #
    # q1 = 公式サイト候補確認用
    # q2 = 九州拠点判定用
    #
    # と分ける。
    #
    # ==========================================

    return {
        "q1_results": res1,
        "q2_results": res2,
        "official_candidates": official_candidates,
        "official_domains": official_domains
    }


# ==========================================
# 3. JSONパース
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
# 4. Gemini一括分析
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

        q1_results = item.get(
            "q1_results",
            []
        )

        q2_results = item.get(
            "q2_results",
            []
        )

        # ------------------------------------------
        # q1は公式サイト確認用
        # ------------------------------------------
        q1_text = "\n".join(
            [
                (
                    f"- タイトル: "
                    f"{r.get('title', '')}\n"
                    f"  内容: "
                    f"{r.get('snippet', '')}\n"
                    f"  URL: "
                    f"{r.get('url', '')}"
                )

                for r in q1_results[:20]
            ]
        )

        # ------------------------------------------
        # q2は拠点判定用
        # ------------------------------------------
        q2_text = "\n".join(
            [
                (
                    f"- タイトル: "
                    f"{r.get('title', '')}\n"
                    f"  内容: "
                    f"{r.get('snippet', '')}\n"
                    f"  URL: "
                    f"{r.get('url', '')}"
                )

                for r in q2_results[:30]
            ]
        )

        official_domain = ""

        domains = item.get(
            "official_domains",
            []
        )

        if domains:

            official_domain = domains[0]

        prompt_targets += (

            f"\n=== 対象企業 {i + 1}: "
            f"{item['company']} ===\n"

            f"【公式ドメイン】\n"
            f"{official_domain}\n"

            f"【Q1：公式サイト候補検索】\n"
            f"{q1_text if q1_text else 'なし'}\n"

            f"【Q2：公式ドメイン内の九州拠点検索】\n"
            f"{q2_text if q2_text else 'なし'}\n"
        )

    template = """
あなたは企業の所在調査のプロフェッショナルです。
ハルシネーションを厳禁とします。

以下の複数企業について、
提供された検索結果だけを根拠として厳密に判定してください。

{prompt_targets}


【最重要ルール】

Q1は主に「公式サイトURLの確認」に使います。

Q2は、
取得された公式ドメイン内を検索した結果です。

九州拠点の判定では、
Q2を最優先してください。


1. "company"

入力された会社名をそのまま格納してください。


2. "official_url"

official_urlは、
【公式ドメイン】に対応するURLを記載してください。

Q1から確認できるURLを使ってください。

URLを推測して作ってはいけません。


3. "is_found"

以下の3つのいずれか。

"⭕️九州拠点あり"

対象企業自身の公式サイト内で、
現在の九州拠点が具体的に確認できる場合。


"❌九州拠点なし"

対象企業自身の公式サイト内の現在の情報から、
九州拠点がないことを明確に判断できる場合。


"❓判定不明"

上記のどちらとも明確に確認できない場合。


【公式サイトの優先順位】

最優先：

1. 現在の事業所一覧
2. 現在の拠点一覧
3. 現在の営業所一覧
4. 現在の事業部案内
5. 現在の法人事業案内
6. 現在のリフォーム事業案内
7. 公式ニュース・公式リリース


【事業部も拠点として扱う】

以下は対象企業自身の九州内の恒常的な営業・事業活動拠点なら対象。

- 支店
- 支社
- 営業所
- 事業所
- 営業部
- 営業本部
- 法人営業部
- 法人事業部
- 法人＆リフォーム事業部
- リフォーム事業部
- 営業拠点
- Hub
- その他の恒常的営業・事業拠点


【重要：ニトリ型を拾う】

例えば、

「法人事業部福岡」
「法人＆リフォーム事業部福岡」

などが公式サイトに掲載されている場合、
「支店」「営業所」という文字がなくても、
九州の事業拠点としてdetailsに含める。


【物流拠点】

物流センター、配送センター、DC、倉庫等も対象候補。

ただし、

法人事業部
法人営業部
リフォーム事業部
営業拠点

などの直接的な営業・事業活動拠点が確認できる場合は、
それを優先する。


【絶対に除外するもの】

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
- 施工実績
- プロジェクト現場


例えば、

対象：
ニデック株式会社

検索結果：
ニデックテクノモータ株式会社 九州事業所

これは別法人なので除外。


【持株会社】

対象企業が持株会社の場合、
子会社・グループ会社の九州拠点を
持株会社自身の拠点として扱わない。


【曖昧な表現は禁止】

以下はdetailsに入れない。

「九州エリア」
「九州各県」
「九州エリア店舗・事業所」
「九州店舗」
「福岡エリア」
「九州拠点」

必ず具体的な拠点名と住所があるものだけ。


4. "details"

九州内の対象企業自身の具体的な拠点。

以下の形式。

{
    "name": "拠点名称",
    "address": "住所",
    "url": "公式ページURL"
}

住所は検索結果に記載されているものを使用。

推測して住所を補完しない。


5. "sales_keywords"

企業の事業内容を踏まえて、
DX営業代行で使えるフックキーワードを10個。


6. "reason"

判定根拠を1～2個。

⭕️：
公式サイトの現在の拠点一覧等に掲載。

❌：
現在の公式サイトで九州拠点が確認できない等。

❓：
公式サイトは確認できたものの、
現在の九州拠点の有無を確定できない等。


7. "notes"

2023年8月14日以降の重要事項のみ。

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


必ず以下のJSON配列だけを返してください。

[
    {
        "company": "会社名",
        "official_url": "https://...",
        "is_found": "⭕️九州拠点あり",
        "reason": [
            "判定根拠"
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

is_foundは必ず以下のいずれか。

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

        # ------------------------------------------
        # 今回の実行ではキャッシュを使わない
        # ------------------------------------------
        st.session_state.result_cache = {}

        st.session_state.pop(
            "batch_results",
            None
        )

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

        progress_bar = st.progress(
            0
        )

        status_text = st.empty()

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

            search_data = search_multi_queries(
                comp,
                tavily_api_key
            )

            fetched_data.append({
                "company": comp,
                "q1_results":
                    search_data[
                        "q1_results"
                    ],
                "q2_results":
                    search_data[
                        "q2_results"
                    ],
                "official_candidates":
                    search_data[
                        "official_candidates"
                    ],
                "official_domains":
                    search_data[
                        "official_domains"
                    ]
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
        # Gemini
        # ======================================
        status_text.text(
            "AIによる一括分析を実行中..."
        )

        company_map = {}

        chunk_size = 10

        for i in range(
            0,
            len(fetched_data),
            chunk_size
        ):

            chunk = fetched_data[
                i:i + chunk_size
            ]

            # Geminiに渡す用
            gemini_chunk = []

            for item in chunk:

                q1_text = "\n".join(
                    [
                        (
                            f"- タイトル: "
                            f"{r.get('title', '')}\n"
                            f"  内容: "
                            f"{r.get('snippet', '')}\n"
                            f"  URL: "
                            f"{r.get('url', '')}"
                        )
                        for r in item[
                            "q1_results"
                        ][:20]
                    ]
                )

                q2_text = "\n".join(
                    [
                        (
                            f"- タイトル: "
                            f"{r.get('title', '')}\n"
                            f"  内容: "
                            f"{r.get('snippet', '')}\n"
                            f"  URL: "
                            f"{r.get('url', '')}"
                        )
                        for r in item[
                            "q2_results"
                        ][:30]
                    ]
                )

                official_domain = ""

                if item[
                    "official_domains"
                ]:

                    official_domain = item[
                        "official_domains"
                    ][0]

                gemini_chunk.append({
                    "company":
                        item["company"],
                    "context":
                        (
                            f"公式ドメイン: "
                            f"{official_domain}\n\n"
                            f"【Q1】\n"
                            f"{q1_text if q1_text else 'なし'}\n\n"
                            f"【Q2：公式サイト内検索】\n"
                            f"{q2_text if q2_text else 'なし'}"
                        ),
                    "q1_results":
                        item["q1_results"],
                    "q2_results":
                        item["q2_results"]
                })

            res_list = analyze_companies_batch(
                gemini_chunk,
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

                    if not comp_name:
                        continue

                    if (
                        r.get(
                            "is_found"
                        )
                        not in [
                            "⭕️九州拠点あり",
                            "❌九州拠点なし",
                            "❓判定不明"
                        ]
                    ):

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

                        r[
                            "sales_keywords"
                        ] = []

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
                    / max(
                        len(fetched_data),
                        1
                    )
                ) * 0.5
            )

        # ======================================
        # 最終整形
        # ======================================
        batch_results = []

        for comp in company_list:

            # ----------------------------------
            # 元の検索データ
            # ----------------------------------
            fetched_item = next(
                (
                    x
                    for x in fetched_data
                    if x[
                        "company"
                    ] == comp
                ),
                None
            )

            if fetched_item is None:

                fetched_item = {
                    "company": comp,
                    "q1_results": [],
                    "q2_results": [],
                    "official_candidates": [],
                    "official_domains": []
                }

            # ----------------------------------
            # Gemini結果
            # ----------------------------------
            res = company_map.get(
                comp,
                {
                    "is_found":
                        "❓判定不明",
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

                name = str(
                    d.get(
                        "name",
                        ""
                    )
                ).strip()

                address = str(
                    d.get(
                        "address",
                        ""
                    )
                ).strip()

                url = str(
                    d.get(
                        "url",
                        ""
                    )
                ).strip()

                if not name or not address:
                    continue

                if not any(
                    pref in address
                    for pref in kyushu_prefectures
                ):
                    continue

                # ----------------------------------
                # 曖昧な「拠点名」を除外
                # ----------------------------------
                vague_names = [
                    "九州エリア",
                    "九州各県",
                    "九州エリア店舗",
                    "九州エリア店舗・事業所",
                    "福岡エリア",
                    "九州の拠点",
                    "九州各地",
                    "九州拠点"
                ]

                if any(
                    vague in name
                    for vague in vague_names
                ):
                    continue

                valid_details.append({
                    "name": name,
                    "address": address,
                    "url": url
                })

            # ----------------------------------
            # 重複削除
            # ----------------------------------
            unique_details = []

            seen_details = set()

            for d in valid_details:

                key = (
                    d[
                        "name"
                    ],
                    d[
                        "address"
                    ],
                    d[
                        "url"
                    ]
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

            # ----------------------------------
            # 公式URL
            #
            # ★ GeminiのURLではなく、
            #   q1で取得したURLを採用
            # ----------------------------------
            official_url = None

            official_domains = fetched_item.get(
                "official_domains",
                []
            )

            official_candidates = fetched_item.get(
                "official_candidates",
                []
            )

            if official_domains:

                selected_domain = (
                    official_domains[0]
                )

                for candidate in official_candidates:

                    if (
                        candidate.get(
                            "domain"
                        )
                        == selected_domain
                    ):

                        official_url = (
                            candidate.get(
                                "url"
                            )
                        )

                        break

            # ----------------------------------
            # 判定整合性
            # ----------------------------------
            is_found = res.get(
                "is_found",
                "❓判定不明"
            )

            reason = res.get(
                "reason",
                []
            )

            if not isinstance(
                reason,
                list
            ):

                reason = [
                    str(reason)
                ]

            # 公式URLが取得できなかった場合
            if not official_domains:

                is_found = "❓判定不明"

                reason = [
                    "対象企業自身の公式サイトを特定できなかったため、九州拠点を確定できない"
                ]

            # 「あり」なのに具体的拠点がない
            elif (
                is_found
                == "⭕️九州拠点あり"
                and not valid_details
            ):

                is_found = (
                    "❓判定不明"
                )

                reason = [
                    "九州拠点ありと判定されたが、具体的な九州拠点の名称・住所を確認できない"
                ]

            # ----------------------------------
            # details
            # ----------------------------------
            details_summary = ", ".join(
                (
                    f"{d.get('name')} "
                    f"({d.get('address')})"
                )

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
            reason_text = " / ".join(
                str(x)
                for x in reason
                if str(x).strip()
            )

            # ----------------------------------
            # notes
            # ----------------------------------
            notes = res.get(
                "notes",
                []
            )

            if not isinstance(
                notes,
                list
            ):

                notes = []

            notes_text = ", ".join(
                str(x)
                for x in notes
            )

            # ----------------------------------
            # 保存
            # ----------------------------------
            batch_results.append({
                "会社名": comp,
                "公式サイト": official_url,
                "判定": is_found,
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

                "_q1_results":
                    fetched_item[
                        "q1_results"
                    ],

                "_q2_results":
                    fetched_item[
                        "q2_results"
                    ],

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
    "batch_results"
    in st.session_state
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
    # スプレッドシート用コピー
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
            # キーワード
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
            # q1確認
            # ----------------------------------
            if r.get(
                "_q1_results"
            ):

                with st.expander(
                    "公式サイト候補を確認"
                ):

                    for result in r[
                        "_q1_results"
                    ]:

                        st.markdown(
                            f"**{result.get('title', '')}**"
                        )

                        if result.get(
                            "snippet"
                        ):

                            st.write(
                                result.get(
                                    "snippet"
                                )
                            )

                        if result.get(
                            "url"
                        ):

                            st.markdown(
                                f"[URL]"
                                f"({result.get('url')})"
                            )

                        st.divider()

            # ----------------------------------
            # q2確認
            # ----------------------------------
            if r.get(
                "_q2_results"
            ):

                with st.expander(
                    "公式サイト内の拠点検索結果を確認"
                ):

                    for result in r[
                        "_q2_results"
                    ]:

                        st.markdown(
                            f"**{result.get('title', '')}**"
                        )

                        if result.get(
                            "snippet"
                        ):

                            st.write(
                                result.get(
                                    "snippet"
                                )
                            )

                        if result.get(
                            "url"
                        ):

                            st.markdown(
                                f"[URL]"
                                f"({result.get('url')})"
                            )

                        st.divider()
