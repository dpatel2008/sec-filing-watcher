"""
scoring_engine.py
Combines filing types, insider-selling clusters, going-concern language and
(optionally) market data into one ranked list of the day's most significant
companies. Only companies with a score above zero are kept.
Writes daily_signals_ranked.csv.

Run it with --no-market for the first pass (before market data exists).
"""

import csv
import sys

ALL_FILINGS_CSV = "all_filings_with_tickers.csv"
FORM4_CLUSTERS_CSV = "form4_clusters.csv"
GOING_CONCERN_CSV = "going_concern_flags.csv"
MARKET_CONTEXT_CSV = "market_context.csv"
OUTPUT_CSV = "daily_signals_ranked.csv"

FORM_TYPE_POINTS = {
    "S-1": 15,
    "S-3": 15,
    "424B5": 25,
    "8-K": 10,
    "SC 13D": 20,
    "SCHEDULE 13D": 20,
    "NT 10-Q": 30,
    "NT 10-K": 30,
}


def normalize_cik(raw) -> str:
    digits = "".join(ch for ch in str(raw) if ch.isdigit())
    return digits.zfill(10) if digits else ""


def safe_float(value, default=0.0):
    try:
        if value is None or value == "" or value == "None":
            return default
        return float(value)
    except (ValueError, TypeError):
        return default


def load_csv(path):
    try:
        with open(path, newline="") as f:
            return list(csv.DictReader(f))
    except FileNotFoundError:
        print(f"  (note: {path} not found, skipping that signal)")
        return []


def main():
    use_market = "--no-market" not in sys.argv

    filings = load_csv(ALL_FILINGS_CSV)
    form4_clusters = load_csv(FORM4_CLUSTERS_CSV)
    going_concern = load_csv(GOING_CONCERN_CSV)
    market_context = load_csv(MARKET_CONTEXT_CSV) if use_market else []

    if not filings:
        print(f"{ALL_FILINGS_CSV} not found or empty — run the collection scripts first.")
        return

    cluster_by_cik = {
        normalize_cik(r["cik"]): r for r in form4_clusters if r.get("cluster_flag") == "YES"
    }
    going_concern_by_cik = {normalize_cik(r["cik"]): r for r in going_concern}
    market_by_ticker = {r["ticker"]: r for r in market_context if r.get("ticker")}

    companies = {}
    for row in filings:
        cik = normalize_cik(row.get("cik", ""))
        if not cik:
            continue
        info = companies.setdefault(cik, {
            "company": row["company"],
            "cik": cik,
            "ticker": "",
            "date_filed": row.get("date_filed", ""),
            "form_types": set(),
            "filing_urls": [],
        })
        if row.get("ticker") and not info["ticker"]:
            info["ticker"] = row["ticker"]
        info["form_types"].add(row["form_type"])
        info["filing_urls"].append(row["url"])

    scored = []
    for cik, info in companies.items():
        score = 0
        reasons = []

        for form_type in sorted(info["form_types"]):
            points = FORM_TYPE_POINTS.get(form_type, 0)
            if points:
                score += points
                reasons.append(f"{form_type} filed (+{points})")

        cluster = cluster_by_cik.get(cik)
        if cluster:
            num_sellers = int(cluster.get("num_sellers", 0) or 0)
            points = min(25 + max(0, num_sellers - 2) * 5, 40)
            score += points
            reasons.append(f"{num_sellers} insiders sold stock (+{points})")

        gc = going_concern_by_cik.get(cik)
        if gc:
            score += 35
            reasons.append("Going concern language in filing (+35)")

        market = market_by_ticker.get(info["ticker"]) if info["ticker"] else None
        if market:
            open_price = safe_float(market.get("prev_open"))
            close_price = safe_float(market.get("prev_close"))
            if open_price > 0 and close_price > 0:
                change = (close_price - open_price) / open_price * 100
                if change <= -8:
                    score += 10
                    reasons.append(f"Stock fell {abs(change):.1f}% on the day (+10)")

            short_pct = safe_float(market.get("short_percent_float"))
            if short_pct > 15:
                score += 10
                reasons.append(f"High short interest, {short_pct:.1f}% of float (+10)")

            market_cap = safe_float(market.get("market_cap"))
            if 0 < market_cap < 300_000_000:
                score += 5
                reasons.append("Small-cap, under $300M market value (+5)")

        if score > 0:
            scored.append({
                "company": info["company"],
                "ticker": info["ticker"],
                "cik": cik,
                "score": score,
                "form_types": "; ".join(sorted(info["form_types"])),
                "reasons": " | ".join(reasons),
                "date_filed": info["date_filed"],
                "num_filings": len(info["filing_urls"]),
                "sample_filing_url": info["filing_urls"][0],
            })

    scored.sort(key=lambda x: x["score"], reverse=True)

    with open(OUTPUT_CSV, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=[
            "company", "ticker", "cik", "score", "form_types", "reasons",
            "date_filed", "num_filings", "sample_filing_url"
        ])
        writer.writeheader()
        for row in scored:
            writer.writerow(row)

    print(f"{len(scored)} compan(ies) with a signal out of {len(companies)} that filed.")
    for row in scored[:5]:
        print(f"  [{row['score']}] {row['company']} ({row['ticker'] or 'no ticker'}): {row['reasons']}")
    print(f"Wrote {OUTPUT_CSV}")


if __name__ == "__main__":
    main()
