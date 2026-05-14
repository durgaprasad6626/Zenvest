import yfinance as yf
from datetime import datetime

POSITIVE_WORDS = {
    'profit', 'growth', 'record', 'surge', 'beat', 'strong', 'rally', 'upgrade',
    'outperform', 'dividend', 'partnership', 'expansion', 'gain', 'rise', 'soar',
    'boost', 'milestone', 'bullish', 'positive', 'success', 'advantage', 'win',
    'increase', 'higher', 'exceeded', 'acquisition', 'breakout', 'recovery'
}

NEGATIVE_WORDS = {
    'loss', 'decline', 'fall', 'crash', 'downgrade', 'miss', 'weak', 'debt',
    'lawsuit', 'recall', 'layoff', 'bankruptcy', 'fraud', 'probe', 'penalty',
    'drop', 'bearish', 'selloff', 'concern', 'risk', 'warning', 'miss',
    'lower', 'decreased', 'disappoints', 'below', 'slump', 'plunge', 'worries'
}

def analyze_sentiment(text: str) -> dict:
    """Classify text sentiment using keyword lexicon."""
    if not text:
        return {'sentiment': 'Neutral', 'score': 0.0, 'color': '#a0aec0'}

    words = text.lower().split()
    pos_count = sum(1 for w in words if any(p in w for p in POSITIVE_WORDS))
    neg_count = sum(1 for w in words if any(n in w for n in NEGATIVE_WORDS))
    total = pos_count + neg_count

    if total == 0:
        score = 0.0
    else:
        score = round((pos_count - neg_count) / total, 2)

    if score > 0.1:
        sentiment = 'Positive'
        color = '#00d4aa'
    elif score < -0.1:
        sentiment = 'Negative'
        color = '#ff4757'
    else:
        sentiment = 'Neutral'
        color = '#f5a623'

    return {'sentiment': sentiment, 'score': score, 'color': color}


def fetch_news_sentiment(symbol: str, max_articles: int = 15) -> list:
    """Fetch news for a symbol and return with sentiment analysis."""
    try:
        ticker = yf.Ticker(symbol)
        news_items = ticker.news or []

        results = []
        for raw_item in news_items[:max_articles]:
            # yfinance updated its structure — data is now often inside a 'content' key
            # We handle both the old and new structures here
            item = raw_item.get('content', raw_item) if isinstance(raw_item, dict) else {}
            
            title = item.get('title', '')
            summary = item.get('summary', '') or item.get('description', '')
            
            # Combine title + summary for better analysis
            text = title + ' ' + summary
            sentiment_data = analyze_sentiment(text)

            # Handle different date formats (int timestamp vs string date)
            pub_ts = item.get('providerPublishTime') or item.get('pubDate')
            pub_date = 'Unknown'
            if pub_ts:
                try:
                    if isinstance(pub_ts, int):
                        pub_date = datetime.fromtimestamp(pub_ts).strftime('%b %d, %Y')
                    else:
                        # Attempt to parse ISO string "2026-04-10T16:08:02Z"
                        dt = datetime.fromisoformat(str(pub_ts).replace('Z', '+00:00'))
                        pub_date = dt.strftime('%b %d, %Y')
                except:
                    pub_date = 'Unknown'

            # Handle publisher
            publisher = 'Unknown'
            provider = item.get('provider')
            if isinstance(provider, dict):
                publisher = provider.get('displayName', 'Unknown')
            else:
                publisher = item.get('publisher', 'Unknown')

            results.append({
                'title': title,
                'url': item.get('link') or item.get('clickThroughUrl', {}).get('url', '#'),
                'publisher': publisher,
                'sentiment': sentiment_data['sentiment'],
                'sentiment_score': sentiment_data['score'],
                'sentiment_color': sentiment_data['color'],
                'published_at': pub_date,
                'thumbnail': item.get('thumbnail', {}).get('resolutions', [{}])[0].get('url', '') if item.get('thumbnail') else ''
            })

        return results
    except Exception as e:
        return [{'error': str(e)}]


if __name__ == '__main__':
    import sys, json
    sym = sys.argv[1] if len(sys.argv) > 1 else 'RELIANCE.NS'
    articles = fetch_news_sentiment(sym)
    for a in articles[:5]:
        print(f"[{a.get('sentiment', '?')}] {a.get('title', '')}")
