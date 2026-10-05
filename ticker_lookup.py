"""
ticker_lookup.py
Maps each filing's CIK number to its stock ticker using SEC's official list.
Writes all_filings_with_tickers.csv (same rows as all_filings.csv plus a
"ticker" column).
"""

import csv
import requests

USER_AGENT = "Dillen Patel dillenpatel2008@gmail.com"

HEADERS = {
    "User-Agent": USER_AGENT,
    "Accept-Encoding": "gzip, deflate",
    "Host": "www.sec.gov",
}

INPUT_CSV = "all_filings.csv"
OUTPUT_CSV = "all_filings_with_tickers.csv"

SEC_TICKER_MAP_URL = "https://www.sec.gov/files/company_tickers.json"


def normalize_cik(raw) -> str:
    digits = "".join(ch for ch in str(raw) if ch.isdigit())
    return digits.zfill(10) if digits else ""


def load_cik_to_ticker_map():
    resp = requests.get(SEC_TICKER_MAP_URL, headers=HEADERS, timeout=30)
    resp.raise_for_status()
    data = resp.json()

    cik_to_ticker = {}
    for entry in data.values():
        cik_to_ticker[normalize_cik(entry["cik_str"])] = entry["ticker"]
    return cik_to_ticker


def main():
    try:
        with open(INPUT_CSV, newline="") as f:
            rows = list(csv.DictReader(f))
    except FileNotFoundError:
        print(f"{INPUT_CSV} not found — run sec_daily_watcher.py first.")
        return

    print("Downloading SEC's CIK-to-ticker list...")
    cik_map = load_cik_to_ticker_map()
    print(f"Loaded {len(cik_map)} tickers.")

    matched = 0
    for row in rows:
        ticker = cik_map.get(normalize_cik(row.get("cik", "")), "")
        row["ticker"] = ticker
        if ticker:
            matched += 1

    print(f"Matched {matched} of {len(rows)} filings to a ticker.")

    fieldnames = ["form_type", "company", "cik", "date_filed", "url", "ticker"]
    with open(OUTPUT_CSV, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)

    print(f"Wrote {OUTPUT_CSV}")


if __name__ == "__main__":
    main()
