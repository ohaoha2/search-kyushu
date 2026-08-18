# ==========================================
# 会社検索 (Q1:会社概要, Q2:九州拠点)
# ==========================================
def search_company(company: str, api_key: str):
    
    # ① Q1: 会社概要の検索
    q1 = f'"{company}" 会社概要'
    q1_results = fetch_tavily_results(q1, api_key)

    # 公式サイト候補を抽出
    official_candidates = find_official_candidates(company, q1_results)
    
    best_domain = None
    if official_candidates:
        best_domain = official_candidates[0]["domain"]

    # ② Q2: 九州拠点の検索
    q2_results = []
    if best_domain:
        # 【超重要】 site:コマンドを使って検索エンジンの根元からドメインを絞り込む
        # これにより、Dodaやマイナビがトップ20を埋め尽くす現象を100%防ぎます
        q2_keywords = f'site:{best_domain} 九州 OR 福岡 OR 佐賀 OR 長崎 OR 熊本 OR 大分 OR 宮崎 OR 鹿児島 拠点 OR 支社 OR 支店 OR 営業所 OR 事業所 OR 事業部 OR 工場 OR Hub'
        
        raw_q2_results = fetch_tavily_results(q2_keywords, api_key)

        # 念のためのPython側の関所
        for r in raw_q2_results:
            domain = extract_domain(r["url"])
            if domain and (domain == best_domain or domain.endswith("." + best_domain)):
                q2_results.append(r)

    return {
        "q1_results": q1_results,
        "q2_results": q2_results,
        "official_candidates": official_candidates
    }
