import streamlit as st
import json
import os
import re
import pandas as pd
from urllib.parse import urlparse
from tavily import TavilyClient
from google import genai
from google.genai import types


# =========================================================
# 基本設定
# =========================================================
st.set_page_config(
    page_title="企業情報一括検索ツール",
    layout="wide"
)

st.title("企業情報一括検索ツール")


# =========================================================
# APIキー
# =========================================================
tavily_api_key = (
    os.getenv("TAVILY_API_KEY")
    or st.secrets.get("TAVILY_API_KEY", "")
)

gemini_key = (
    os.getenv("GEMINI_API_KEY")
    or st.secrets.get("GEMINI_API_KEY", "")
)


# =========================================================
# 九州
# =========================================================
kyushu_prefectures = [
    "福岡",
    "佐賀",
    "長崎",
    "熊本",
    "大分",
    "宮崎",
    "鹿児島"
]


# =========================================================
# セッションステート
# =========================================================
if "result_cache" not in st.session_state:
    st.session_state.result_cache = {}


# =========================================================
# 第三者サイト除外
# =========================================================
def is_excluded_domain(domain: str):

    if not domain:
        return True

    excluded_domains = [
        "wikipedia.org",
        "yahoo.co.jp",
        "news.yahoo.co.jp",
        "nikkei.com",
        "toyokeizai.net",
        "mynavi.jp",
        "rikunabi.com",
        "en-japan.com",
        "wantedly.com",
        "indeed.com",
        "onecareer.jp",
        "doda.jp",
        "bizreach.jp",
        "green-japan.com",
        "metoree.com",
        "navitime.co.jp"
    ]

    return any(
        domain == excluded
        or domain.endswith("." + excluded)
        for excluded in excluded_domains
    )


# =========================================================
# URL → ドメイン
# =========================================================
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


# =========================================================
# 会社名を解析
#
# 株式会社ニデック
#    → core = ニデック / front
#
# ニデック株式会社
#    → core = ニデック / back
# =========================================================
def parse_company_name(company: str):

    company = company.strip()

    if company.startswith("株式会社"):

        return {
            "core": company[len("株式会社"):],
            "position": "front"
        }

    if company.endswith("株式会社"):

        return {
            "core": company[:-len("株式会社")],
            "position": "back"
        }

    return {
        "core": company,
        "position": "unknown"
    }


# =========================================================
# 会社名の正規化
#
# 空白・全角空白だけ吸収
# 法人格そのものは変更しない
# =========================================================
def normalize_company_name(text: str):

    if not text:
        return ""

    return (
        str(text)
        .replace(" ", "")
        .replace("　", "")
        .strip()
    )


# =========================================================
# 入力会社名と検索結果テキストを照合
#
# True  = 入力会社名と一致する表記がある
# False = 明確に前株・後株が逆
# None  = 判断できない
# =========================================================
def company_name_match_in_text(
    input_company: str,
    text: str
):

    input_company = normalize_company_name(
        input_company
    )

    text_normalized = normalize_company_name(
        text
    )

    if not input_company:
        return None

    # 完全一致した場合
    if input_company in text_normalized:
        return True

    info = parse_company_name(
        input_company
    )

    core = normalize_company_name(
        info["core"]
    )

    position = info["position"]

    if not core:
        return None

    # -----------------------------------------
    # 株式会社ニデック
    # → ニデック株式会社 は別法人
    # -----------------------------------------
    if position == "front":

        wrong_name = (
            core + "株式会社"
        )

        if wrong_name in text_normalized:
            return False

    # -----------------------------------------
    # ニデック株式会社
    # → 株式会社ニデック は別法人
    # -----------------------------------------
    if position == "back":

        wrong_name = (
            "株式会社" + core
        )

        if wrong_name in text_normalized:
            return False

    return None


# =========================================================
# 公式候補スコアリング
#
# 前株・後株の判定そのものはAIにさせない。
# 検索候補段階で明確な逆法人だけ除外する。
# =========================================================
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

    title_match = company_name_match_in_text(
        company,
        title
    )

    snippet_match = company_name_match_in_text(
        company,
        snippet
    )

    # 明らかに逆法人
    if title_match is False:
        return -1000

    score = 0

    # -----------------------------------------
    # 正式名称がタイトルにある
    # -----------------------------------------
    if title_match is True:
        score += 30

    # -----------------------------------------
    # 正式名称が本文にある
    # -----------------------------------------
    if snippet_match is True:
        score += 15

    title_lower = title.lower()
    url_lower = url.lower()

    # -----------------------------------------
    # 会社概要系タイトル
    # -----------------------------------------
    official_title_words = [
        "会社概要",
        "会社情報",
        "企業情報",
        "企業概要",
        "コーポレート",
        "公式",
        "corporate",
        "company",
        "profile",
        "about"
    ]

    for word in official_title_words:

        if word.lower() in title_lower:
            score += 6

    # -----------------------------------------
    # URLが会社名っぽい
    # -----------------------------------------
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

    # -----------------------------------------
    # 会社概要系パス
    # -----------------------------------------
    official_path_words = [
        "/about",
        "/about_us",
        "/company",
        "/corporate",
        "/profile",
        "/outline"
    ]

    for word in official_path_words:

        if word in url_lower:
            score += 3

    # -----------------------------------------
    # 第三者サイト
    # -----------------------------------------
    domain = extract_domain(url)

    if is_excluded_domain(domain):
        score -= 100

    return score


# =========================================================
# 公式ドメイン候補
# =========================================================
def find_official_domains(
    company: str,
    results: list
):

    candidates = []

    for result in results:

        url = result.get(
            "url",
            ""
        )

        domain = extract_domain(
            url
        )

        if not domain:
            continue

        if is_excluded_domain(
            domain
        ):
            continue

        title = result.get(
            "title",
            ""
        )

        snippet = result.get(
            "snippet",
            ""
        )

        combined_text = (
            title
            + "\n"
            + snippet
        )

        # -----------------------------------------
        # 明確に別法人と分かる場合だけ除外
        # -----------------------------------------
        if company_name_match_in_text(
            company,
            combined_text
        ) is False:

            continue

        score = score_official_candidate(
            company,
            result
        )

        candidates.append({
            "domain": domain,
            "score": score,
            "title": title,
            "url": url
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

    return unique_candidates[:5]


# =========================================================
# Tavily検索
# =========================================================
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
                "title":
                    item.get(
                        "title",
                        ""
                    ),

                "url":
                    item.get(
                        "url",
                        ""
                    ),

                "snippet":
                    item.get(
                        "content",
                        ""
                    )
            })

        return results

    except Exception:
        return []


# =========================================================
# 会社検索
#
# ここは「うまくいっていた頃」の構造を維持
# =========================================================
def search_multi_queries(
    keyword: str,
    api_key: str
):

    # =====================================================
    # Q1
    # =====================================================
    q1 = (
        f'"{keyword}" '
        f'会社概要 公式サイト'
    )

    res1 = fetch_tavily_results(
        q1,
        api_key
    )

    # =====================================================
    # 公式候補
    # =====================================================
    official_candidates = find_official_domains(
        keyword,
        res1
    )

    official_domains = []

    for candidate in official_candidates:

        # 一定以上の候補だけ採用
        if candidate["score"] >= 10:

            official_domains.append(
                candidate["domain"]
            )

    # 念のため最上位候補を残す
    if (
        not official_domains
        and official_candidates
    ):

        official_domains = [
            official_candidates[0]["domain"]
        ]

    # =====================================================
    # Q2：公式サイト内の九州検索
    # =====================================================
    res2 = []

    if official_domains:

        domain = official_domains[0]

        q2_queries = [

            # ---------------------------------------------
            # 一般拠点
            # ---------------------------------------------
            (
                f'site:{domain} '
                f'九州 福岡 佐賀 長崎 熊本 '
                f'大分 宮崎 鹿児島 '
                f'支店 支社 営業所 事業所'
            ),

            # ---------------------------------------------
            # 営業・法人・事業部
            # ---------------------------------------------
            (
                f'site:{domain} '
                f'福岡 佐賀 長崎 熊本 '
                f'大分 宮崎 鹿児島 '
                f'営業部 営業本部 '
                f'法人営業 法人事業 '
                f'法人事業部 法人＆リフォーム '
                f'リフォーム事業部 '
                f'営業拠点 拠点一覧 事業所一覧'
            ),

            # ---------------------------------------------
            # 拠点情報
            # ---------------------------------------------
            (
                f'site:{domain} '
                f'会社情報 拠点 所在地 住所'
            ),

            # ---------------------------------------------
            # 店舗・事業拠点
            # ---------------------------------------------
            (
                f'site:{domain} '
                f'福岡支店 福岡営業所 '
                f'九州支店 九州営業所 '
                f'福岡事業所 九州事業所 '
                f'福岡営業部 九州営業部'
            )
        ]

        seen_urls = set()

        for q2 in q2_queries:

            current_results = fetch_tavily_results(
                q2,
                api_key,
                include_domains=[
                    domain
                ]
            )

            for result in current_results:

                url = result.get(
                    "url",
                    ""
                )

                if (
                    url
                    and url in seen_urls
                ):
                    continue

                if url:
                    seen_urls.add(url)

                res2.append(
                    result
                )

    return {
        "q1_results":
            res1,

        "q2_results":
            res2,

        "official_candidates":
            official_candidates,

        "official_domains":
            official_domains
    }


# =========================================================
# JSONパース
# =========================================================
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


# =========================================================
# Gemini分析
#
# AIに任せる：
# ・会社概要URLの最終選択
# ・九州拠点
# ・部署別IT提案
#
# AIに任せない：
# ・前株/後株そのものの判定
# =========================================================
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

                for r in item.get(
                    "q1_results",
                    []
                )[:20]
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

                for r in item.get(
                    "q2_results",
                    []
                )[:20]
            ]
        )

        official_domain = ""

        if item.get(
            "official_domains"
        ):

            official_domain = (
                item[
                    "official_domains"
                ][0]
            )

        prompt_targets += (

            f"\n=== 対象企業 {i + 1} ===\n"

            f"【入力会社名】\n"
            f"{item['company']}\n"

            f"【最有力公式ドメイン】\n"
            f"{official_domain or 'なし'}\n"

            f"【Q1：会社概要・公式サイト候補】\n"
            f"{q1_text if q1_text else 'なし'}\n"

            f"【Q2：公式ドメイン内の九州拠点候補】\n"
            f"{q2_text if q2_text else 'なし'}\n"
        )

    template = """
あなたは企業情報調査とDX営業提案の専門家です。

ハルシネーションを厳禁とします。
提供された検索結果から確認できない情報を補完・推測してはいけません。


【最重要】

入力会社名と別法人を混同してはいけません。

特に、

「株式会社ニデック」
「ニデック株式会社」

は別法人です。

ただし、前株・後株の最終照合はシステム側でも行います。
あなたは検索結果から、
「どの会社の公式ページなのか」を慎重に判断してください。


==================================================
1. official_url
==================================================

会社概要URLを最優先してください。

優先順位：

1. 会社概要
2. 会社情報
3. 企業情報
4. 企業概要
5. Corporate Profile
6. About Us
7. 公式トップページ

対象企業自身の公式サイトであることを確認してください。

求人サイト、Wikipedia、ニュースサイト、
企業情報まとめサイト等は使用禁止です。

検索結果に存在するURLだけ使用してください。

URLを推測して生成してはいけません。


==================================================
2. 九州拠点
==================================================

以下の3つのいずれか：

"⭕️九州拠点あり"
"❌九州拠点なし"
"❓判定不明"

⭕️：

対象企業自身が現在運営している
具体的な九州内拠点が確認できる場合。

❌：

対象企業自身の公式情報から
九州拠点がないことを明確に確認できる場合。

❓：

判断材料が不足する場合。

「見つからない」だけで❌にしてはいけません。


==================================================
3. details
==================================================

以下は対象：

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
- 恒常的な事業活動拠点
- 店舗

具体的な名称・住所が確認できる場合だけ記載。

「九州エリア」
「九州各県」
「九州拠点」
などの曖昧な表現は除外。

以下は除外：

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
- プロジェクト
- 納入先


==================================================
4. ニトリ型の事業部拠点
==================================================

次のような拠点は積極的に拾ってください。

例：

「法人＆リフォーム事業部 福岡」
「法人事業部 福岡」
「リフォーム事業部 博多」
「法人営業部 福岡」

「支店」「営業所」という名称でなくても、
対象企業自身の恒常的な営業・事業活動拠点なら対象です。


==================================================
5. HPがない小規模企業
==================================================

小規模企業、個人事業主など、
公式ホームページを持っていない企業も対象です。

公式HPが確認できない場合、
無理にURLを作らないでください。

official_url：
null

九州拠点：
❓判定不明

として構いません。


==================================================
6. reason
==================================================

1～2個の簡潔な判定根拠。


==================================================
7. 部署別IT提案
==================================================

会社の事業内容そのものではなく、

「その部署に何のIT提案をすれば刺さるか」

を返してください。

最大4部署。

各部署3キーワード。

例：

[
  {
    "department": "営業部",
    "keywords": [
      "SFA導入",
      "顧客管理DX",
      "商談進捗管理"
    ]
  }
]

企業の実際の事業内容・組織・業務から
現実的なIT提案を考えてください。


==================================================
8. notes
==================================================

2023年8月14日以降の重要事項のみ。

- 社名変更
- 拠点移転
- 拠点新設
- M&A
- 組織再編
- 新規事業
- 大規模設備投資

なければ[]。


==================================================

{prompt_targets}


必ず以下のJSON配列のみ返してください。

[
  {
    "company": "入力会社名",
    "official_url": "https://...",
    "is_found": "⭕️九州拠点あり",
    "reason": [
      "判定根拠"
    ],
    "details": [
      {
        "name": "拠点名称",
        "address": "住所",
        "url": "公式URL"
      }
    ],
    "department_keywords": [
      {
        "department": "営業部",
        "keywords": [
          "SFA導入",
          "顧客管理DX",
          "商談進捗管理"
        ]
      }
    ],
    "notes": []
  }
]
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


# =========================================================
# UI
# =========================================================
with st.form(
    key="batch_search_form"
):

    raw_input = st.text_area(
        "会社名リストを入力（スプレッドシートからそのまま貼り付け可能）",
        placeholder=(
            "株式会社ニデック\n"
            "アステラス製薬株式会社\n"
            "株式会社ニトリ"
        ),
        height=180
    )

    submit_button = st.form_submit_button(
        "一括検索・分析を実行",
        type="primary"
    )


# =========================================================
# 実行
# =========================================================
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
            "TAVILY_API_KEY または GEMINI_API_KEY が設定されていません。"
        )

    else:

        # ------------------------------------------
        # キャッシュなし
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

        # ======================================
        # Tavily
        # ======================================
        status_text.text(
            "検索中..."
        )

        fetched_data = []

        for i, comp in enumerate(
            company_list
        ):

            search_data = search_multi_queries(
                comp,
                tavily_api_key
            )

            fetched_data.append({

                "company":
                    comp,

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

        # 無料枠対策
        chunk_size = 5

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
                            "department_keywords"
                        ),
                        list
                    ):

                        r[
                            "department_keywords"
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
                    "q1_results": [],
                    "q2_results": [],
                    "official_candidates": []
                }

            res = company_map.get(
                comp,
                {
                    "official_url": None,
                    "is_found":
                        "❓判定不明",
                    "reason": [],
                    "details": [],
                    "department_keywords": [],
                    "notes": []
                }
            )

            # ==================================
            # 公式URL
            #
            # ★ Geminiの選択を採用
            # ==================================
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

            # ==================================
            # 九州拠点
            # ==================================
            raw_details = res.get(
                "details",
                []
            )

            if not isinstance(
                raw_details,
                list
            ):

                raw_details = []

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

                # 九州住所か
                if not any(
                    pref in address
                    for pref in kyushu_prefectures
                ):
                    continue

                # 曖昧な拠点名を除外
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

            # 重複削除
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

            # ==================================
            # 判定
            # ==================================
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

            # ⭕️なのに具体的拠点なし
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

            # ==================================
            # 拠点概要
            # ==================================
            details_summary = ", ".join(
                (
                    f"{d['name']} "
                    f"({d['address']})"
                )

                for d in valid_details
            )

            # ==================================
            # 部署別IT提案
            # ==================================
            department_keywords = res.get(
                "department_keywords",
                []
            )

            if not isinstance(
                department_keywords,
                list
            ):

                department_keywords = []

            dept_summary = []

            for item in department_keywords:

                if not isinstance(
                    item,
                    dict
                ):
                    continue

                department = str(
                    item.get(
                        "department",
                        ""
                    )
                ).strip()

                keywords = item.get(
                    "keywords",
                    []
                )

                if not department:
                    continue

                if not isinstance(
                    keywords,
                    list
                ):

                    keywords = []

                keywords = [
                    str(x)
                    for x in keywords
                    if str(x).strip()
                ]

                if not keywords:
                    continue

                dept_summary.append(
                    f"【{department}】 "
                    + " / ".join(
                        keywords
                    )
                )

            department_summary = "\n".join(
                dept_summary
            )

            # ==================================
            # 特記事項
            # ==================================
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

            # ==================================
            # 根拠
            # ==================================
            reason_text = " / ".join(
                str(x)
                for x in reason
                if str(x).strip()
            )

            # ==================================
            # 保存
            # ==================================
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

                "部署別IT提案":
                    department_summary,

                "特記事項":
                    notes_text,

                "_raw_reason":
                    reason_text,

                "_raw_details":
                    valid_details,

                "_raw_keywords":
                    department_keywords,

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
                    fetched_item[
                        "official_candidates"
                    ]
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


# =========================================================
# 一覧表示
# =========================================================
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

    # -----------------------------------------------------
    # ★ 表示項目は元のシンプルな5項目
    # -----------------------------------------------------
    expected_columns = [
        "会社名",
        "公式サイト",
        "判定",
        "九州拠点",
        "部署別IT提案",
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

    # =====================================================
    # TSV
    # =====================================================
    tsv_text = df_display.to_csv(
        sep="\t",
        index=False
    )

    with st.expander(
        "スプレッドシート用の一括コピー（タブ区切りテキスト）"
    ):

        st.markdown(
            "下のテキストをコピーして、"
            "スプレッドシートへそのまま貼り付けできます。"
        )

        st.code(
            tsv_text,
            language="text"
        )

    # =====================================================
    # CSV
    # =====================================================
    csv_data = df_display.to_csv(
        index=False
    ).encode(
        "utf-8-sig"
    )

    st.download_button(
        label="結果をCSVでダウンロード",
        data=csv_data,
        file_name="kyushu_corporate_search_results.csv",
        mime="text/csv",
        type="primary"
    )

    # =====================================================
    # 各社詳細
    # =====================================================
    st.divider()

    st.subheader(
        "各社詳細・カード表示"
    )

    for r in results:

        with st.expander(
            f"{r['会社名']} ── 【 {r['判定']} 】"
        ):

            # ---------------------------------------------
            # 公式サイト
            # ---------------------------------------------
            if r.get(
                "公式サイト"
            ):

                st.markdown(
                    f"**公式サイト:** "
                    f"[{r['公式サイト']}]"
                    f"({r['公式サイト']})"
                )

            else:

                st.write(
                    "**公式サイト:** 確認できず"
                )

            # ---------------------------------------------
            # 判定根拠
            # ---------------------------------------------
            if r.get(
                "_raw_reason"
            ):

                st.info(
                    f"**判定根拠:** "
                    f"{r['_raw_reason']}"
                )

            # ---------------------------------------------
            # 特記事項
            # ---------------------------------------------
            if r.get(
                "_raw_notes"
            ):

                st.info(
                    f"**特記事項:** "
                    f"{r['_raw_notes']}"
                )

            # ---------------------------------------------
            # 部署別IT提案
            # ---------------------------------------------
            if r.get(
                "_raw_keywords"
            ):

                st.markdown(
                    "**部署別IT提案:**"
                )

                for item in r[
                    "_raw_keywords"
                ]:

                    if not isinstance(
                        item,
                        dict
                    ):
                        continue

                    department = item.get(
                        "department",
                        ""
                    )

                    keywords = item.get(
                        "keywords",
                        []
                    )

                    if not department:
                        continue

                    st.markdown(
                        f"**【{department}】**"
                    )

                    for keyword in keywords:

                        st.markdown(
                            f"- {keyword}"
                        )

            # ---------------------------------------------
            # 九州拠点
            # ---------------------------------------------
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

            # ---------------------------------------------
            # 公式サイト候補
            # ---------------------------------------------
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

            # ---------------------------------------------
            # Q1
            # ---------------------------------------------
            if r.get(
                "_q1_results"
            ):

                with st.expander(
                    "Tavily検索結果を確認"
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

            # ---------------------------------------------
            # Q2
            # ---------------------------------------------
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
