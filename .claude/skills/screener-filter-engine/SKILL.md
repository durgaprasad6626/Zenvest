---
name: screener-filter-engine
description: Applies user-defined fundamental filters (e.g., PE < 20, ROE > 15%, Debt < 1) to a list of stocks and returns ranked results. Use this when the user runs the stock screener tool or when generating auto-recommendations.
---

# Screener Filter Engine Skill

## Overview
Takes a watchlist of stock symbols + filter criteria and returns only the stocks that match all filters, sorted by AI prediction score.

## Supported Filters
| Filter Key | Operator | Example |
|-----------|----------|---------|
| `pe_ratio` | `lt` | PE < 20 |
| `pb_ratio` | `lt` | PB < 3 |
| `roe` | `gt` | ROE > 15% |
| `debt_to_equity` | `lt` | D/E < 1.0 |
| `revenue_growth` | `gt` | Rev Growth > 10% |
| `profit_growth` | `gt` | Profit Growth > 10% |
| `market_cap` | `gt` | Market Cap > 5000Cr |
| `dividend_yield` | `gt` | Dividend > 1% |

## Steps
1. Receive filter dict from user (e.g., `{"pe_ratio": {"lt": 20}, "roe": {"gt": 15}}`)
2. For each stock in the universe (or user-provided list), fetch data via stock-data-fetch skill
3. Apply each filter: skip stock if condition not met
4. Run AI prediction on passing stocks
5. Sort by prediction score descending
6. Return top N results with full data

## Output
```json
{
  "filters_applied": {"pe_ratio": {"lt": 20}, "roe": {"gt": 15}},
  "total_matched": 12,
  "results": [
    {
      "symbol": "TCS.NS",
      "company_name": "Tata Consultancy Services",
      "pe_ratio": 18.5,
      "roe": 48.2,
      "score": 82,
      "recommendation": "BUY"
    }
  ]
}
```

## Scripts
See `scripts/screener.py`
