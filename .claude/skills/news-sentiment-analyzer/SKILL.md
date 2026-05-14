---
name: news-sentiment-analyzer
description: Fetches recent news headlines for a stock symbol and classifies each headline as Positive, Negative, or Neutral using keyword-based sentiment analysis. Use this when displaying news on the stock detail page or dashboard.
---

# News Sentiment Analyzer Skill

## Overview
Fetches stock news via yfinance and applies a keyword lexicon-based sentiment classifier. Fast, offline, no LLM API cost.

## Steps
1. Fetch `yf.Ticker(symbol).news` (returns last 20 articles)
2. For each article, analyze the title using the sentiment lexicon in `scripts/sentiment.py`
3. Return structured list with `title`, `url`, `publisher`, `sentiment`, `sentiment_score`, `published_at`

## Sentiment Rules
- **Positive keywords**: profit, growth, record, surge, beat, strong, rally, upgrade, outperform, dividend, partnership, expansion
- **Negative keywords**: loss, decline, fall, crash, downgrade, miss, weak, debt, lawsuit, recall, layoff, bankruptcy
- Score = (positive_count - negative_count) / total_keyword_count
- Score > 0.1 → Positive | Score < -0.1 → Negative | else → Neutral

## Output
```json
[
  {
    "title": "Reliance Industries posts record Q3 profit",
    "url": "...",
    "publisher": "Economic Times",
    "sentiment": "Positive",
    "sentiment_score": 0.67,
    "sentiment_color": "green",
    "published_at": "2024-01-15"
  }
]
```
