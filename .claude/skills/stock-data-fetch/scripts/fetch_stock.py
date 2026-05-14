import yfinance as yf
import pandas as pd
import time
import json
import os
from dotenv import load_dotenv
from supabase import create_client, Client

# ── Setup Supabase ────────────────────────────────────────────────────────────
# Load from .env if running standalone, but usually app.py handles this
load_dotenv()
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")

# Lazy Supabase singleton — created only once on first use
_supabase_client = None

def _get_supabase():
    global _supabase_client
    if _supabase_client is not None:
        return _supabase_client
    if SUPABASE_URL and SUPABASE_KEY:
        try:
            _supabase_client = create_client(SUPABASE_URL, SUPABASE_KEY)
        except Exception as e:
            print(f"FetchStock Error: Could not init Supabase client: {e}")
    return _supabase_client

# ── Cache TTL: 10 minutes (was 2 min — too aggressive) ───────────────────────
CACHE_TTL = 600

# ── In-memory L1 cache (process-level, sub-millisecond hits) ─────────────────
_mem_cache: dict = {}

def get_cached_stock(symbol: str):
    # L1: in-memory
    entry = _mem_cache.get(symbol)
    if entry and (time.time() - entry["ts"]) < CACHE_TTL:
        data = entry["data"].copy()
        data["from_cache"] = True
        return data

    # L2: Supabase DB cache
    sb = _get_supabase()
    if not sb:
        return None
    try:
        response = sb.table("stock_cache").select("*").eq("symbol", symbol).execute()
        if response.data:
            record = response.data[0]
            if (time.time() - record["fetched_at"]) < CACHE_TTL:
                data = record["data"]
                # Populate L1 so next hit is instant
                _mem_cache[symbol] = {"data": data, "ts": record["fetched_at"]}
                data = data.copy()
                data["from_cache"] = True
                return data
    except OSError as e:
        if 'getaddrinfo' in str(e) or 'WinError' in str(e):
            pass  # Transient DNS/network failure — suppress noisy per-symbol log
        else:
            print(f"Error fetching stock cache for {symbol}: {e}")
    except Exception as e:
        print(f"Error fetching stock cache for {symbol}: {e}")
    return None


def save_stock_cache(symbol: str, data: dict):
    # L1 write
    _mem_cache[symbol] = {"data": data, "ts": time.time()}

    # L2 write (async-safe — failures are non-fatal)
    sb = _get_supabase()
    if not sb:
        return
    try:
        record = {
            "symbol": symbol,
            "data": data,
            "fetched_at": time.time()
        }
        sb.table("stock_cache").upsert(record).execute()
    except OSError as e:
        if 'getaddrinfo' in str(e) or 'WinError' in str(e):
            pass  # Transient DNS/network failure — suppress noisy per-symbol log
        else:
            print(f"Error saving stock cache for {symbol}: {e}")
    except Exception as e:
        print(f"Error saving stock cache for {symbol}: {e}")


# ── Core Fetch Function ───────────────────────────────────────────────────────

def fetch_stock_data(symbol: str) -> dict:
    symbol = symbol.strip().upper()
    if not symbol:
        return {"error": "No symbol provided"}

    # 1. Try cache (L1 then L2)
    cached = get_cached_stock(symbol)
    if cached:
        return cached

    # 2. Fetch from yfinance — SINGLE combined call strategy
    try:
        ticker = yf.Ticker(symbol)

        # ── Fast path: use fast_info first (no network for price sometimes) ──
        try:
            fi = ticker.fast_info
            current_price = fi.last_price or 0
            prev_close    = fi.previous_close or 0
        except Exception:
            current_price = 0
            prev_close    = 0

        # ── info call (fundamental data) ─────────────────────────────────────
        info = ticker.info or {}

        # Fallback price from info if fast_info was empty
        if not current_price:
            current_price = float(info.get("currentPrice") or info.get("regularMarketPrice") or 0)
        if not prev_close:
            prev_close = float(info.get("previousClose") or 0)

        if not current_price:
            return {"error": f"Symbol {symbol} not found or no data available"}

        # Dividend yield — yfinance returns it as a fraction (0.02 = 2 %)
        div_yield_raw = info.get("trailingAnnualDividendYield") or info.get("dividendYield") or 0
        div_yield = div_yield_raw * 100 if div_yield_raw and div_yield_raw < 1.0 else div_yield_raw

        data = {
            "symbol":        symbol,
            "company_name":  info.get("longName") or info.get("shortName") or symbol,
            "price":         current_price,
            "prev_close":    prev_close,
            "currency":      info.get("currency", "INR"),
            "sector":        info.get("sector", "Unknown"),
            "industry":      info.get("industry", "Unknown"),
            "description":   (info.get("longBusinessSummary") or "")[:500],

            # Key Ratios
            "market_cap":      info.get("marketCap") or 0,
            "pe_ratio":        info.get("trailingPE") or info.get("forwardPE"),
            "pb_ratio":        info.get("priceToBook"),
            "roe":             (info.get("returnOnEquity") or 0) * 100,
            "eps":             info.get("trailingEps"),
            "dividend_yield":  div_yield,
            "debt_to_equity":  info.get("debtToEquity"),
            "revenue_growth":  (info.get("revenueGrowth") or 0) * 100,
            "profit_growth":   (info.get("earningsGrowth") or 0) * 100,
            "beta":            info.get("beta"),
            "week_52_high":    info.get("fiftyTwoWeekHigh"),
            "week_52_low":     info.get("fiftyTwoWeekLow"),
            "volume":          info.get("regularMarketVolume") or 0,
            "avg_volume":      info.get("averageVolume") or 0,
        }

        # Change calc
        if prev_close > 0:
            data["change"]     = data["price"] - prev_close
            data["change_pct"] = (data["change"] / prev_close) * 100
        else:
            data["change"]     = 0
            data["change_pct"] = 0

        # ── Single history call for OHLCV (1y) ───────────────────────────────
        # Previously there were TWO calls: history("1d") + history("1y").
        # Now we make ONE call and derive the current price from it too.
        hist = ticker.history(period="1y")
        ohlcv = []
        if not hist.empty:
            # Override current_price with the most accurate last-close
            last_close = float(hist["Close"].iloc[-1])
            if last_close:
                data["price"] = last_close
                if prev_close > 0:
                    data["change"]     = last_close - prev_close
                    data["change_pct"] = (data["change"] / prev_close) * 100

            for d, row in hist.iterrows():
                ohlcv.append({
                    "date":  d.strftime("%Y-%m-%d"),
                    "close": round(float(row["Close"]), 2),
                    "vol":   int(row["Volume"]),
                })
        data["ohlcv"] = ohlcv

        # Save to cache
        save_stock_cache(symbol, data)
        data["from_cache"] = False
        return data

    except Exception as e:
        return {"error": f"Failed to fetch {symbol}: {str(e)}"}


def fetch_backtest_data(symbol: str, years: int = 3) -> dict:
    symbol = symbol.strip().upper()
    period_map = {1: "1y", 3: "5y", 5: "5y"}
    period = period_map.get(years, "5y")
    try:
        ticker = yf.Ticker(symbol)
        hist = ticker.history(period=period)
        if hist.empty:
            return {"error": "No historical data"}

        target_date = hist.index[-1] - pd.DateOffset(years=years)
        past_slice  = hist[hist.index >= target_date]
        if past_slice.empty:
            return {"error": "Not enough history"}

        past_price    = float(past_slice.iloc[0]["Close"])
        current_price = float(hist["Close"].iloc[-1])
        past_date     = past_slice.index[0].strftime("%Y-%m-%d")

        return {
            "symbol":        symbol,
            "years":         years,
            "past_date":     past_date,
            "past_price":    past_price,
            "current_price": current_price,
        }
    except Exception as e:
        return {"error": str(e)}


# ── Bulk prefetch helper (for warming the cache) ──────────────────────────────
def prefetch_symbols(symbols: list, max_workers: int = 10):
    """Fire-and-forget bulk fetch to warm L1+L2 cache at startup."""
    import socket
    from concurrent.futures import ThreadPoolExecutor
    # Skip entirely if there's no network — avoids 80+ error lines in the log
    try:
        socket.getaddrinfo('query2.finance.yahoo.com', 443)
    except OSError:
        print('[prefetch] Network unavailable — skipping cache warm-up.')
        return
    uncached = [s for s in symbols if not _mem_cache.get(s)]
    if not uncached:
        return
    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        ex.map(fetch_stock_data, uncached)


if __name__ == "__main__":
    import sys
    test_sym = sys.argv[1] if len(sys.argv) > 1 else "RELIANCE.NS"
    print(f"Testing fetch for {test_sym}...")
    res = fetch_stock_data(test_sym)
    print(json.dumps({k: v for k, v in res.items() if k != "ohlcv"}, indent=2))
