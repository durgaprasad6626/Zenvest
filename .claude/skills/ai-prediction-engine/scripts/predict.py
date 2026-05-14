import json
import re
import pandas as pd
import numpy as np

# ── Technical Indicator Helpers ───────────────────────────────────────────────

def calculate_rsi(closes, period=14):
    if len(closes) <= period: return 50.0
    gains = losses = 0.0
    for i in range(1, period + 1):
        diff = closes[-period + i - 1] - closes[-period + i - 2]
        if diff > 0: gains += diff
        else: losses -= diff
    if losses == 0: return 100.0 if gains > 0 else 50.0
    rs = gains / losses
    return round(100 - (100 / (1 + rs)), 2)

def calculate_sma(closes, period):
    if len(closes) < period:
        return closes[-1] if closes else 0.0
    return sum(closes[-period:]) / period

# ── Rule-Based Scoring Engine (fast fallback for bulk calls) ──────────────────

def _rule_based_score(stock_data: dict, sector_pe_avg: float = 25) -> dict:
    """
    Transparent weighted scoring engine — no fake training data.
    Used for bulk operations (recommendations, screener, portfolio).
    Score: 0=Strong Sell, 50=Neutral, 100=Strong Buy.
    """
    pe     = stock_data.get('pe_ratio', 0) or 0
    roe    = stock_data.get('roe', 0) or 0
    de     = stock_data.get('debt_to_equity', 0) or 0
    rev_g  = stock_data.get('revenue_growth', 0) or 0
    price  = stock_data.get('price', 0) or 0
    high52 = stock_data.get('week_52_high', price) or price
    low52  = stock_data.get('week_52_low', price) or price
    ohlcv  = stock_data.get('ohlcv', [])
    closes = [d['close'] for d in ohlcv]

    rsi   = calculate_rsi(closes, 14)
    sma20 = calculate_sma(closes, 20)
    sma50 = calculate_sma(closes, 50)

    score = 50  # Start neutral

    # ── Fundamental signals ──────────────────────────────────────────────────
    if pe > 0 and sector_pe_avg > 0:
        ratio = pe / sector_pe_avg
        if ratio < 0.7:   score += 20
        elif ratio < 0.9: score += 12
        elif ratio < 1.1: score += 5
        elif ratio < 1.3: score -= 5
        else:             score -= 15

    if roe > 25:   score += 15
    elif roe > 15: score += 8
    elif roe < 0:  score -= 10

    if rev_g > 20:   score += 12
    elif rev_g > 10: score += 6
    elif rev_g < 0:  score -= 8

    if de < 0.3:   score += 8
    elif de < 0.8: score += 4
    elif de > 2.0: score -= 10

    # ── Technical signals ────────────────────────────────────────────────────
    if rsi < 30:   score += 18
    elif rsi < 45: score += 8
    elif rsi > 75: score -= 18
    elif rsi > 60: score -= 5

    if price > 0 and sma20 > 0 and sma50 > 0:
        if price > sma20 > sma50:   score += 15
        elif price < sma20 < sma50: score -= 15

    if high52 > low52 and price > 0:
        pct = (price - low52) / (high52 - low52)
        if pct < 0.25:  score += 12
        elif pct > 0.9: score -= 8

    score = max(0, min(100, round(score)))

    if score >= 65:   rec = 'BUY'
    elif score <= 38: rec = 'SELL'
    else:             rec = 'HOLD'

    reasons = []
    if rec == 'BUY':
        if rsi < 45: reasons.append("oversold RSI conditions")
        if pe > 0 and pe < sector_pe_avg: reasons.append("undervalued P/E vs sector")
        if roe > 15: reasons.append("strong Return on Equity")
        if price > 0 and price > sma20 > sma50: reasons.append("bullish MA trend")
    elif rec == 'SELL':
        if rsi > 65: reasons.append("overbought RSI conditions")
        if pe > sector_pe_avg * 1.2: reasons.append("overvalued P/E vs sector")
        if price > 0 and price < sma20 < sma50: reasons.append("bearish MA trend")
    else:
        if 40 <= rsi <= 60: reasons.append("neutral RSI momentum")
        if de > 2: reasons.append("elevated debt risk")

    summary = (
        "Signals indicate " + " and ".join(reasons[:2]) + "."
        if reasons else
        "Mixed signals — neutral consensus across fundamentals and technicals."
    )
    confidence = (
        'High' if score >= 75 or score <= 25 else
        'Medium' if score >= 60 or score <= 38 else
        'Low'
    )

    return {
        'recommendation':       rec,
        'recommendation_color': rec.lower(),
        'score':                score,
        'expected_growth_label': 'Smart Analysis',
        'confidence':           confidence,
        'confidence_reason':    f"Score {score}/100 — weighted fundamentals + technicals",
        'signals': [
            {'name': 'RSI (14-day)',        'value': f"{rsi:.1f}",    'score': rsi,   'impact': 'Positive' if rsi < 50 else 'Negative'},
            {'name': 'Composite Score',     'value': f"{score}/100",  'score': score, 'impact': 'Positive' if score >= 50 else 'Negative'},
            {'name': 'P/E vs Sector',       'value': f"{pe:.1f} vs {sector_pe_avg}", 'score': max(0, 100 - pe), 'impact': 'Positive' if pe < sector_pe_avg else 'Negative'},
        ],
        'summary':    summary,
        'ai_powered': False,
    }

# ── Gemini AI Engine (single-stock deep analysis) ────────────────────────────

def _gemini_predict(stock_data: dict, sector_pe_avg: float, gemini_client) -> dict | None:
    """
    Sends real stock metrics to Gemini AI and gets a structured analysis.
    Returns None on any failure so the caller can fall back to rule-based.
    """
    pe    = stock_data.get('pe_ratio', 0) or 0
    pb    = stock_data.get('pb_ratio', 0) or 0
    roe   = stock_data.get('roe', 0) or 0
    de    = stock_data.get('debt_to_equity', 0) or 0
    rev_g = stock_data.get('revenue_growth', 0) or 0
    pro_g = stock_data.get('profit_growth', 0) or 0
    beta  = stock_data.get('beta', 1.0) or 1.0
    price = stock_data.get('price', 0) or 0
    high52 = stock_data.get('week_52_high', 0) or 0
    low52  = stock_data.get('week_52_low', 0) or 0

    ohlcv  = stock_data.get('ohlcv', [])
    closes = [d['close'] for d in ohlcv]
    rsi    = calculate_rsi(closes, 14)
    sma20  = calculate_sma(closes, 20)
    sma50  = calculate_sma(closes, 50)

    if price > 0 and sma20 > 0 and sma50 > 0:
        if price > sma20 > sma50:   sma_signal = "Bullish (price > SMA20 > SMA50)"
        elif price < sma20 < sma50: sma_signal = "Bearish (price < SMA20 < SMA50)"
        else:                       sma_signal = "Mixed / Neutral"
    else:
        sma_signal = "Insufficient data"

    pct_from_low = ""
    if high52 > low52 > 0:
        p = round((price - low52) / (high52 - low52) * 100, 1)
        pct_from_low = f"{p}% above 52-week low"

    prompt = f"""You are a SEBI-compliant Indian stock market analyst for the Zenvest platform.
Analyze this NSE-listed stock and return ONLY a JSON object — no markdown, no explanation outside the JSON.

Stock: {stock_data.get('symbol', 'Unknown')} ({stock_data.get('company_name', 'Unknown')})
Sector: {stock_data.get('sector', 'Unknown')}
Current Price: ₹{price}
52-Week Range: ₹{low52} – ₹{high52} {f"({pct_from_low})" if pct_from_low else ""}

Fundamental Metrics:
  P/E Ratio:      {pe:.1f}  (Sector Average: {sector_pe_avg})
  P/B Ratio:      {pb:.2f}
  ROE:            {roe:.1f}%
  Debt/Equity:    {de:.2f}
  Revenue Growth: {rev_g:.1f}%
  Profit Growth:  {pro_g:.1f}%
  Beta:           {beta:.2f}

Technical Indicators:
  RSI (14-day):   {rsi:.1f}
  SMA 20/50:      {sma_signal}

Required JSON format:
{{
  "recommendation": "BUY" | "HOLD" | "SELL",
  "score": <integer 0-100, where 100=Strong Buy, 50=Neutral, 0=Strong Sell>,
  "confidence": "High" | "Medium" | "Low",
  "summary": "<2-sentence analysis covering the key reason for this recommendation>",
  "key_strengths": ["<strength 1>", "<strength 2>"],
  "key_risks": ["<risk 1>", "<risk 2>"],
  "expected_growth_label": "<concise label like 'Moderate Growth' or 'Value Pick' or 'High Risk'>"
}}"""

    try:
        # New google-genai SDK
        try:
            result = gemini_client.models.generate_content(
                model='gemini-2.0-flash',
                contents=prompt
            )
            raw = result.text.strip()
        except AttributeError:
            # Legacy SDK fallback
            result = gemini_client.generate_content(prompt)
            raw = result.text.strip()

        # Strip markdown code fences if present
        raw = re.sub(r'^```(?:json)?\s*', '', raw, flags=re.MULTILINE)
        raw = re.sub(r'\s*```\s*$', '', raw, flags=re.MULTILINE)
        raw = raw.strip()

        data = json.loads(raw)

        rec   = str(data.get('recommendation', 'HOLD')).upper()
        if rec not in ('BUY', 'HOLD', 'SELL'): rec = 'HOLD'
        score = max(0, min(100, int(data.get('score', 50))))
        conf  = data.get('confidence', 'Medium')
        summ  = data.get('summary', '')
        growth_label = data.get('expected_growth_label', 'Gemini AI Analysis')
        strengths = data.get('key_strengths', [])
        risks     = data.get('key_risks', [])

        signals = []
        for s in strengths[:2]:
            signals.append({'name': 'Strength', 'value': s, 'score': 75, 'impact': 'Positive'})
        for r in risks[:2]:
            signals.append({'name': 'Risk', 'value': r, 'score': 25, 'impact': 'Negative'})

        return {
            'recommendation':       rec,
            'recommendation_color': rec.lower(),
            'score':                score,
            'expected_growth_label': growth_label,
            'confidence':           conf,
            'confidence_reason':    f"Gemini AI deep analysis of {stock_data.get('symbol', '')} fundamentals + technicals",
            'signals':              signals,
            'summary':              summ,
            'ai_powered':           True,
        }

    except json.JSONDecodeError as e:
        print(f"[Gemini Predict] JSON parse error for {stock_data.get('symbol', '?')}: {e} | raw={raw[:200]}")
        return None
    except Exception as e:
        print(f"[Gemini Predict] Error for {stock_data.get('symbol', '?')}: {e}")
        return None


# ── Main Entry Point ─────────────────────────────────────────────────────────

def predict_stock(stock_data: dict, sector_pe_avg: float = 25,
                  goal: str = 'Long-term',
                  gemini_client=None, use_ai: bool = False) -> dict:
    """
    Unified prediction entry point.

    - use_ai=True + gemini_client provided  → Gemini AI analysis (single-stock pages)
    - Otherwise                             → Rule-based weighted scoring (bulk operations)

    Gemini failures always fall back to rule-based — never crashes the UI.
    """
    if use_ai and gemini_client is not None:
        result = _gemini_predict(stock_data, sector_pe_avg, gemini_client)
        if result is not None:
            return result
        print(f"[Predict] Gemini unavailable, falling back to rule-based for {stock_data.get('symbol', '?')}")

    return _rule_based_score(stock_data, sector_pe_avg)


if __name__ == '__main__':
    sample = {
        'symbol': 'RELIANCE.NS', 'company_name': 'Reliance Industries',
        'sector': 'Energy', 'pe_ratio': 18.5, 'pb_ratio': 2.1,
        'roe': 22.4, 'debt_to_equity': 0.4, 'revenue_growth': 18.0,
        'profit_growth': 24.0, 'beta': 1.1,
        'price': 2400, 'week_52_high': 2600, 'week_52_low': 1800,
        'ohlcv': [{'close': 2300 + i * 5} for i in range(60)]
    }
    import json as _json
    print(_json.dumps(predict_stock(sample, sector_pe_avg=20), indent=2))
