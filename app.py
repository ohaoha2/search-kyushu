# ==========================================
# 会社検索 (Q1:会社概要, Q2:九州拠点 - 厳密ドメイン検索)
# ==========================================
def search_company(company: str, api_key: str):
    
    # ① Q1: 会社概要の検索（Web全体から公式サイトを探す）
    q1 = f'"{company}" 会社概要'
    q1_results = fetch_tavily_results(q1, api_key)

    # 公式サイト候補を抽出
    official_candidates = find_official_candidates(company, q1_results)
    
    target_domains = None
    if official_candidates:
        # 一番スコアの高い公式サイトのドメインを取得
        best_domain = official_candidates[0]["domain"]
        target_domains = [best_domain]

    # ② Q2: 九州拠点の検索（特定した公式サイト内のみを検索）
    # ★ご指摘の通り、ドメインが絞られているため「会社名」は不要（除外）★
    q2_keywords = "九州 OR 福岡 OR 佐賀 OR 長崎 OR 熊本 OR 大分 OR 宮崎 OR 鹿児島 拠点 OR 支社 OR 支店 OR 営業所 OR 事業所 OR 事業部 OR 工場"
    
    if target_domains:
        # 公式ドメイン内のみで検索を実行（会社名を入れずに拠点名だけで探す）
        q2_results = fetch_tavily_results(q2_keywords, api_key, include_domains=target_domains)
    else:
        # 公式サイトが特定できなかった場合は実行しない
        q2_results = []

    return {
        "q1_results": q1_results,
        "q2_results": q2_results,
        "official_candidates": official_candidates
    }
