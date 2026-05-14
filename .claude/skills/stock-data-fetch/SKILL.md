---
name: stock-data-fetch
description: Fetches real-time and historical stock data from Yahoo Finance using yfinance. Use this skill when you need to retrieve current price, OHLCV history, financials, fundamentals (PE, ROE, EPS), or company info for any stock symbol. Handles caching to avoid rate limits. Supports both Indian (.NS) and US stocks.
---

# Stock Data Fetch Skill

## Overview
This skill fetches comprehensive stock data from Yahoo Finance via the `yfinance` Python library and caches results in SQLite to avoid excessive API calls.

## When to Use
- User searches for a stock symbol or company name
- Stock detail page needs to load price, fundamentals, financials
- Screener needs to evaluate stocks against filters
- AI prediction engine needs input signals

## Steps

1. **Check cache first** — Query `stock_cache` table in SQLite for data fetched within the last 15 minutes
2. **Fetch from yfinance** if cache miss:
   - `yf.Ticker(symbol).info` → company info, PE, ROE, market cap
   - `yf.Ticker(symbol).history(period='1y')` → OHLCV data
   - `yf.Ticker(symbol).financials` → revenue, profit
3. **Store in cache** — Save to `stock_cache` with `fetched_at` timestamp
4. **Return structured dict** with: `price`, `change_pct`, `pe_ratio`, `pb_ratio`, `roe`, `revenue_growth`, `market_cap`, `52_week_high`, `52_week_low`, `eps`, `dividend_yield`

## Error Handling
- If yfinance returns empty data: return `{"error": "Symbol not found or data unavailable"}`
- If rate limited: return cached data even if stale, add `"stale": true` flag
- For Indian stocks: append `.NS` suffix if not present (e.g., `RELIANCE` → `RELIANCE.NS`)

## Scripts
See `scripts/fetch_stock.py` for the implementation.
