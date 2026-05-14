import sys
sys.path.insert(0, '.claude/skills/stock-data-fetch/scripts')
from fetch_stock import fetch_stock_data

for sym in ['^NSEI', '^BSESN', '^NSEBANK']:
    d = fetch_stock_data(sym)
    if not d.get('error'):
        print(f"{sym}: price={d['price']:.2f}, change_pct={d['change_pct']:.2f}%, from_cache={d.get('from_cache')}")
    else:
        print(f"{sym}: ERROR - {d['error']}")
