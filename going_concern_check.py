"""
going_concern_check.py
Scans every 10-K and 10-Q from all_filings.csv for the language auditors and
managers use when they doubt a company can stay solvent: "substantial doubt"
close to "going concern". Statements that say there is NO substantial doubt
are ignored. Writes going_concern_flags.csv.

Only the first ~3 MB of each filing is read, to keep the run fast.
"""

import csv
import html
import re
import time
import requests

USER_AGENT = "Dillen Patel dillenpatel2008@gmail.com"

HEADERS = {
    "User-Agent": USER_AGENT,
    "Accept-Encoding": "gzip, deflate",
    "Host": "www.sec.gov",
}

INPUT_CSV = "all_filings.csv"
OUTPUT_CSV = "going_concern_flags.csv"

SLEEP_BETWEEN_REQUESTS = 0.35
MAX_ERRORS_IN_A_ROW = 5
MAX_BYTES_TO_READ = 3_000_000
MAX_FILINGS_TO_SCAN = 400
OVERLAP_CHARS = 20_000


def get_with_retry(url, headers, stream=False, timeout=30, tries=3):
    """Gets a page from the SEC. If the SEC says "slow down" (429 or 503), waits and tries again."""
    resp = None
    for attempt in range(1, tries + 1):
        resp = requests.get(url, headers=headers, stream=stream, timeout=timeout)
        if resp.status_code in (429, 503) and attempt < tries:
            wait = 20 * attempt
            print(f"  The SEC asked us to slow down ({resp.status_code}). Waiting {wait} seconds...")
            resp.close()
            time.sleep(wait)
            continue
        return resp
    return resp


PATTERN = re.compile(r"substantial doubt.{0,150}?going concern", re.IGNORECASE)

NEGATIONS = (
    "no substantial doubt",
    "not raise substantial doubt",
    "not have substantial doubt",
    "did not raise",
    "does not raise",
    "do not raise",
)


def normalize_cik(raw) -> str:
    digits = "".join(ch for ch in str(raw) if ch.isdigit())
    return digits.zfill(10) if digits else ""


def clean(raw_text: str) -> str:
    text = re.sub(r"<[^>]+>", " ", raw_text)
    text = html.unescape(text).replace("\xa0", " ")
    return re.sub(r"\s+", " ", text)


def find_going_concern(text: str):
    """Returns a short snippet around the first real match, or None."""
    for m in PATTERN.finditer(text):
        context = text[max(0, m.start() - 60): m.end()].lower()
        if any(neg in context for neg in NEGATIONS):
            continue
        return text[max(0, m.start() - 80): m.end() + 80].strip()
    return None


def scan_filing(url: str):
    bytes_read = 0
    tail = ""

    with get_with_retry(url, HEADERS, stream=True, timeout=30) as resp:
        resp.raise_for_status()
        for chunk in resp.iter_content(chunk_size=65536):
            if not chunk:
                continue
            bytes_read += len(chunk)
            combined = tail + chunk.decode("utf-8", errors="ignore")

            snippet = find_going_concern(clean(combined))
            if snippet:
                return snippet

            tail = combined[-OVERLAP_CHARS:]

            if bytes_read >= MAX_BYTES_TO_READ:
                break

    return None


def main():
    try:
        with open(INPUT_CSV, newline="") as f:
            rows = list(csv.DictReader(f))
    except FileNotFoundError:
        print(f"{INPUT_CSV} not found — run sec_daily_watcher.py first.")
        return

    target_rows = [r for r in rows if r["form_type"] in ("10-K", "10-Q")]
    target_rows = target_rows[:MAX_FILINGS_TO_SCAN]
    print(f"Scanning {len(target_rows)} 10-K/10-Q filing(s)...")

    flagged = []
    errors_in_a_row = 0

    for row in target_rows:
        try:
            snippet = scan_filing(row["url"])
            errors_in_a_row = 0
            if snippet:
                flagged.append({
                    "company": row["company"],
                    "cik": normalize_cik(row["cik"]),
                    "form_type": row["form_type"],
                    "date_filed": row["date_filed"],
                    "snippet": snippet,
                    "url": row["url"],
                })
        except requests.RequestException as e:
            print(f"  Error fetching {row['url']}: {e}")
            errors_in_a_row += 1
            if errors_in_a_row >= MAX_ERRORS_IN_A_ROW:
                print("  Too many errors in a row. Stopping this check early and keeping what we have.")
                break
        time.sleep(SLEEP_BETWEEN_REQUESTS)

    with open(OUTPUT_CSV, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=[
            "company", "cik", "form_type", "date_filed", "snippet", "url"
        ])
        writer.writeheader()
        for row in flagged:
            writer.writerow(row)

    print(f"Flagged {len(flagged)} filing(s) with going concern language.")
    print(f"Wrote {OUTPUT_CSV}")


if __name__ == "__main__":
    main()
