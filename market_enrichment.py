"""
market_enrichment.py
Looks up price and fundamentals for the highest-ranked companies only, using
the free tiers of Polygon.io and Alpha Vantage.

Free-tier limits this script respects:
  Polygon.io     5 calls per minute
  Alpha Vantage  5 calls per minute and 25 calls per day

So it enriches at most MAX_TICKERS companies (one call to each service per
company) and waits 13 seconds between companies.

Reads: daily_signals_ranked.csv   Writes: market_context.csv
"""

import csv
import os
import time
import requests

ALPHA_VANTAGE_KEY = os.environ.get("ALPHA_VANTAGE_KEY", "")
POLYGON_API_KEY = os.environ.get("POLYGON_API_KEY", "")

INPUT_CSV = "daily_signals_ranked.csv"
OUTPUT_CSV = "market_context.csv"

MAX_TICKERS = 10
DELAY_BETWEEN_TICKERS = 13

FIELDNAMES = [
    "ticker", "company", "prev_open", "prev_close", "prev_high", "prev_low",
    "prev_volume", "market_cap", "pe_ratio", "short_ratio",
    "short_percent_float", "analyst_target_price", "profit_margin",
]


def get_polygon_snapshot(ticker: str):
    if not POLYGON_API_KEY:
        return None
    url = f"https://api.polygon.io/v2/aggs/ticker/{ticker}/prev"
    try:
        resp = requests.get(url, params={"apiKey": POLYGON_API_KEY}, timeout=15)
        if resp.status_code != 200:
            print(f"    Polygon returned {resp.status_code} for {ticker}")
            return None
        results = resp.json().get("results")
        if not results:
            return None
        bar = results[0]
        return {
            "prev_open": bar.get("o"),
            "prev_close": bar.get("c"),
            "prev_high": bar.get("h"),
            "prev_low": bar.get("l"),
            "prev_volume": bar.get("v"),
        }
    except requests.RequestException as e:
        print(f"    Polygon error for {ticker}: {e}")
        return None


def get_alpha_vantage_overview(ticker: str):
    if not ALPHA_VANTAGE_KEY:
        return None
    try:
        resp = requests.get(
            "https://www.alphavantage.co/query",
            params={"function": "OVERVIEW", "symbol": ticker, "apikey": ALPHA_VANTAGE_KEY},
            timeout=15,
        )
        if resp.status_code != 200:
            return None
        data = resp.json()
        if "Symbol" not in data:
            note = data.get("Note") or data.get("Information") or "no data"
            print(f"    Alpha Vantage gave nothing for {ticker}: {str(note)[:80]}")
            return None
        return {
            "market_cap": data.get("MarketCapitalization"),
            "pe_ratio": data.get("PERatio"),
            "short_ratio": data.get("ShortRatio"),
            "short_percent_float": data.get("ShortPercentFloat"),
            "analyst_target_price": data.get("AnalystTargetPrice"),
            "profit_margin": data.get("ProfitMargin"),
        }
    except requests.RequestException as e:
        print(f"    Alpha Vantage error for {ticker}: {e}")
        return None


def write_rows(rows):
    with open(OUTPUT_CSV, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDNAMES, restval="", extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def main():
    try:
        with open(INPUT_CSV, newline="") as f:
            ranked = list(csv.DictReader(f))
    except FileNotFoundError:
        print(f"{INPUT_CSV} not found — run scoring_engine.py --no-market first.")
        write_rows([])
        return

    if not ALPHA_VANTAGE_KEY and not POLYGON_API_KEY:
        print("No API keys set, skipping market data.")
        write_rows([])
        return

    targets = [r for r in ranked if r.get("ticker")][:MAX_TICKERS]
    print(f"Adding market data for the top {len(targets)} ranked compan(ies)...")

    results = []
    for i, row in enumerate(targets):
        ticker = row["ticker"]
        print(f"  [{i + 1}/{len(targets)}] {ticker} ({row['company']})")

        merged = {"ticker": ticker, "company": row["company"]}
        merged.update(get_polygon_snapshot(ticker) or {})
        merged.update(get_alpha_vantage_overview(ticker) or {})
        results.append(merged)

        if i < len(targets) - 1:
            time.sleep(DELAY_BETWEEN_TICKERS)

    write_rows(results)
    print(f"Wrote {OUTPUT_CSV}")


if __name__ == "__main__":
    main()
