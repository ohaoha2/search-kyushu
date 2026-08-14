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
# Q1：公式サイト取得
# Q2：取得した公式ドメイン内の九州検索
# ==========================================
def search_multi_queries(
    keyword: str,
    api_key: str
):

    # ==========================================
    # Q1：会社概要・公式サイト検索
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
    # Q2：公式サイト内検索
    # ==========================================
    if official_domains:

        domain = official_domains[0]

        q2_queries = [

            (
                f'site:{domain} '
                f'九州 福岡 佐賀 長崎 熊本 大分 宮崎 鹿児島 '
                f'支店 支社 営業所 事業所'
            ),

            (
                f'site:{domain} '
                f'九州 福岡 '
                f'事業部 営業部 法人営業 法人事業 '
                f'法人＆リフォーム リフォーム事業部 '
                f'営業拠点 拠点一覧 事業所一覧'
            ),

            (
                f'site:{domain} '
                f'九州 福岡 '
                f'会社情報 拠点 所在地 住所'
            )
        ]

        res2 = []

        seen_q2_urls = set()

        for q2 in q2_queries:

            current_results = fetch_tavily_results(
                q2,
                api_key,
                include_domains=[domain]
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

        # 公式ドメインが取れなければ
        # Q2の第三者検索は行わない
        res2 = []

    # ==========================================
    # Q1 / Q2を分離したまま返す
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

        q1_results = item.get(
            "q1_results",
            []
        )

        q2_results = item.get(
            "q2_results",
            []
        )

        # ------------------------------------------
        # Q1
        # 429対策：最大5件
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

                for r in q1_results[:5]
            ]
        )

        # ------------------------------------------
        # Q2
        # 429対策：最大10件
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

                for r in q2_results[:10]
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

            f"【公式ドメイン候補】\n"
            f"{official_domain}\n"

            f"【Q1：公式サイト候補検索】\n"
            f"{q1_text if q1_text else 'なし'}\n"

            f"【Q2：公式ドメイン内の九州拠点検索】\n"
            f"{q2_text if q2_text else 'なし'}\n"
        )

    template = """
あなたは企業の所在調査のプロフェッショナルです。
ハルシネーションを厳禁とします。

提供された検索結果から確認できない情報を
推測・補完してはいけません。

以下の複数企業について、
それぞれ厳密に調査し、
必ずJSON配列で返してください。

{prompt_targets}


【重要：公式サイトと拠点判定を分離する】

Q1：
公式サイトを特定するための検索結果です。

Q2：
Q1で特定候補となった公式ドメインを対象に、
その公式サイト内を検索した結果です。

九州拠点の判定ではQ2を最優先してください。


1. "company"

入力された会社名をそのまま格納してください。


2. "official_url"

対象企業自身の公式サイトのコーポレートサイトURLを記載してください。

Wikipedia、求人サイト、ニュースサイト、
企業情報サイト等の第三者サイトは除外してください。

Q1の検索結果から対象企業自身の公式サイトであることを確認してください。

確認できない場合は null としてください。

URLを推測して作らないでください。


3. "is_found"

以下の3つのいずれか。

"⭕️九州拠点あり"

対象企業自身が現在運営している九州内の具体的な拠点が、
対象企業自身の公式サイトから明確に確認できる場合。


"❌九州拠点なし"

対象企業自身の公式情報から、
現在の九州拠点がないことを明確に確認できる場合。


"❓判定不明"

上記のどちらとも明確に確認できない場合。


【公式情報の優先順位】

1. 現在の事業所一覧
2. 現在の拠点一覧
3. 現在の営業所一覧
4. 現在の事業部案内
5. 現在の法人事業案内
6. 現在のリフォーム事業案内
7. 公式ニュース
8. 公式リリース


【事業部も拠点として扱う】

対象企業自身の公式サイトに掲載されている
恒常的な営業・事業活動拠点なら対象としてください。

例：

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


【重要】

「支店」「営業所」という名称でなくても、
対象企業自身の恒常的な営業・事業活動拠点で、
九州内の住所が確認できれば対象。


【ニトリ型】

例えば公式サイトに、

「法人事業部福岡」
「法人＆リフォーム事業部福岡」

などが存在する場合、
九州の事業拠点として扱ってください。


【物流拠点】

物流センター、配送センター、DC、倉庫等も
対象企業自身の恒常的拠点であることが確認できれば対象。

ただし営業・事業拠点が確認できる場合は、
営業・事業拠点を優先。


【絶対に対象外】

以下は対象企業自身の九州拠点として扱わない。

- 子会社
- 関連会社
- グループ会社
- 別法人
- 代理店
- 販売店
- パートナー企業
- 協力会社
- 顧客先
- 施工現場
- 納入先
- 施工実績
- プロジェクト現場


例えば、

対象企業：
ニデック株式会社

検索結果：
ニデックテクノモータ株式会社 九州事業所

これは別法人なので除外。


【持株会社】

対象企業が持株会社の場合、
子会社・グループ会社の拠点を
持株会社自身の拠点として扱わない。


【現在性】

過去の情報だけでは「⭕️」にしない。

閉鎖済み、移転前、統合前、再編前など、
現在も稼働していることが確認できない場合は
「❓判定不明」。


【曖昧な拠点名は禁止】

以下のような抽象的表現をdetailsに入れない。

「九州エリア」
「九州各県」
「九州エリア店舗・事業所」
「九州店舗」
「福岡エリア」
「九州拠点」

具体的な拠点名称と住所が必要。


4. "details"

九州内の対象企業自身の具体的な拠点を記載。

形式：

{
    "name": "拠点名称",
    "address": "住所",
    "url": "その拠点を裏付ける公式URL"
}

住所は検索結果に記載されているものだけ使用。

推測して補完しない。

対象企業自身、
九州内、
現在稼働中

の3点が確認できる拠点だけ含める。


5. "sales_keywords"

企業の実際の事業内容から、
DX営業代行で使えるフックキーワードを10個。


6. "reason"

判定にかかわらず、
今回の判定に至った主な根拠を
1～2個、簡潔に記載。

⭕️の場合：

「大林組公式サイトの事業所一覧に九州支店が掲載されている」

など。

❌の場合：

「対象企業公式の現在の拠点情報に九州拠点がなく、子会社等の拠点のみ確認される」

など。

❓の場合：

「公式サイトは確認できるが、現在の九州拠点を確認できる公式情報が不足している」

など。

推測は禁止。


7. "notes"

2023年8月14日以降の重要トピックのみ。

- 社名変更
- 拠点新設
- 拠点移転
- 拠点拡張
- M&A
- グループ再編
- 組織変更
- 新規事業
- 大規模設備投資

関係のないニュースは入れない。

なければ[]。


必ず以下のJSON配列だけを返してください。

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
        # 会社名 → 検索結果
        # ======================================
        fetched_map = {
            item["company"]:
                item
            for item in fetched_data
        }

        # ======================================
        # Gemini一括分析
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

                    if not comp_name:
                        continue

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
                        r.get(
                            "details"
                        ),
                        list
                    ):

                        r["details"] = []

                    # ----------------------------------
                    # sales_keywords
                    # ----------------------------------
                    if not isinstance(
                        r.get(
                            "sales_keywords"
                        ),
                        list
                    ):

                        r[
                            "sales_keywords"
                        ] = []

                    # ----------------------------------
                    # reason
                    # ----------------------------------
                    if not isinstance(
                        r.get(
                            "reason"
                        ),
                        list
                    ):

                        r["reason"] = []

                    # ----------------------------------
                    # notes
                    # ----------------------------------
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
        # 九州拠点の後処理
        # ======================================
        batch_results = []

        for comp in company_list:

            fetched_item = fetched_map.get(
                comp,
                {
                    "company": comp,
                    "q1_results": [],
                    "q2_results": [],
                    "official_candidates": [],
                    "official_domains": []
                }
            )

            # ----------------------------------
            # Gemini結果
            # ----------------------------------
            res = company_map.get(
                comp,
                {
                    "official_url": None,
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
            # 九州住所のみ残す
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
                # 曖昧な拠点名を除外
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
                    d["name"],
                    d["address"],
                    d["url"]
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
            # ★ここは元の方式
            # ★GeminiがQ1から判断したURLを使用
            # ----------------------------------
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

            # ----------------------------------
            # 判定
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

                reason = []

            # ----------------------------------
            # 「あり」なのに詳細なし
            # ----------------------------------
            if (
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
            # sales_keywords
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
            # 公式候補
            # ----------------------------------
            official_candidates = fetched_item.get(
                "official_candidates",
                []
            )

            official_domains = fetched_item.get(
                "official_domains",
                []
            )

            # ----------------------------------
            # 保存
            # ----------------------------------
            batch_results.append({
                "会社名":
                    comp,

                "公式サイト":
                    official_url,

                "判定":
                    is_found,

                "九州拠点":
                    (
                        details_summary
                        if details_summary
                        else "なし"
                    ),

                "フックキーワード":
                    keywords_summary,

                "特記事項":
                    notes_text,

                # 内部データ
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
    # 各社詳細・カード表示
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
            # 公式サイト候補確認
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
            # Q1検索結果確認
            # ----------------------------------
            if r.get(
                "_q1_results"
            ):

                with st.expander(
                    "公式サイト候補の検索結果を確認"
                ):

                    for sr in r[
                        "_q1_results"
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

            # ----------------------------------
            # Q2検索結果確認
            # ----------------------------------
            if r.get(
                "_q2_results"
            ):

                with st.expander(
                    "公式サイト内の拠点検索結果を確認"
                ):

                    for sr in r[
                        "_q2_results"
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
