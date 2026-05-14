---
name: portfolio-analyzer
description: Calculates profit/loss for a user's portfolio given current prices and buy prices. Generates AI suggestions for portfolio improvement based on prediction scores. Use this when rendering the portfolio page or generating portfolio insights.
---

# Portfolio Analyzer Skill

## Overview
Computes live P&L for each portfolio position and generates actionable AI suggestions using the prediction engine scores.

## Steps
1. Fetch current prices for all symbols in portfolio (use stock-data-fetch skill)
2. For each position: calculate `current_value`, `invested_value`, `pnl`, `pnl_pct`
3. Run AI prediction on each stock (use ai-prediction-engine skill)
4. Generate suggestions:
   - SELL suggestion if recommendation='SELL' and pnl > 5% (lock in profits)
   - HOLD suggestion if recommendation='HOLD'
   - BUY MORE suggestion if recommendation='BUY' and pnl < -10% (average down)
5. Compute portfolio-level metrics: total invested, total current value, overall P&L

## Output
```json
{
  "positions": [
    {
      "symbol": "RELIANCE.NS",
      "qty": 10,
      "buy_price": 2200,
      "current_price": 2450,
      "invested_value": 22000,
      "current_value": 24500,
      "pnl": 2500,
      "pnl_pct": 11.36,
      "recommendation": "HOLD",
      "suggestion": "Consider booking partial profits"
    }
  ],
  "summary": {
    "total_invested": 100000,
    "total_current": 112000,
    "total_pnl": 12000,
    "total_pnl_pct": 12.0,
    "best_performer": "TCS.NS",
    "worst_performer": "IDEA.NS"
  }
}
```

## Scripts
See `scripts/portfolio.py`
