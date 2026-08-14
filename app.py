# test_search.py として保存して実行してみてください
from duckduckgo_search import DDGS
try:
    with DDGS() as ddg:
        results = [r for r in ddg.text("株式会社さわやか", max_results=3)]
        print(results)
except Exception as e:
    print(f"エラー: {e}")
