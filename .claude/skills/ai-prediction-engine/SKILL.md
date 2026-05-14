---
name: ai-prediction-engine
description: Scores a stock using fundamental and technical signals to produce a BUY/SELL/HOLD recommendation with growth probability percentage and human-readable explanation. Use this skill when generating AI predictions for a stock or building the Top 10 recommendation list.
---

# AI Prediction Engine Skill

## Overview
A rule-based scoring model that combines fundamental + technical signals to generate investment predictions. Each signal is normalized to 0–100 and weighted, producing a composite score that maps to BUY/SELL/HOLD.

## Input Signals & Weights

| Signal | Weight | Good Value |
|--------|--------|-----------|
| PE Ratio vs Sector Avg | 20% | PE < 20 |
| PB Ratio | 10% | PB < 3 |
| ROE (Return on Equity) | 20% | ROE > 15% |
| Revenue Growth YoY | 15% | > 10% |
| Profit Growth YoY | 15% | > 10% |
| Debt-to-Equity | 10% | < 1.0 |
| 52-Week Momentum | 10% | Price > 50-day MA |

## Scoring Formula
```
composite_score = Σ(weight_i × normalized_signal_i)
growth_probability = composite_score  (0–100%)
expected_growth = growth_probability * 0.25  (% over 3 months)
```

## Output
```json
{
  "recommendation": "BUY",
  "growth_probability": 78,
  "expected_growth_pct": 14.5,
  "expected_growth_label": "~14.5% in 3 months",
  "confidence": "High",
  "score": 78,
  "signals": [
    {"name": "PE Ratio", "value": 15.2, "score": 85, "impact": "Positive"},
    ...
  ],
  "summary": "Strong fundamentals with low PE, high ROE, and positive revenue momentum."
}
```

## Thresholds
- Score ≥ 70 → **BUY** 🟢 (High confidence if ≥ 80)
- Score 45–69 → **HOLD** 🟡
- Score < 45 → **SELL** 🔴

## Scripts
See `scripts/predict.py`
