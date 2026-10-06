"""
positive_8k_check.py
Opens the 8-K filings from all_filings.csv once and looks for two kinds of news.

Good news: a new share buyback, raised guidance, an FDA approval, a special or
higher dividend, a large new contract, or record revenue or earnings. Sentences that
are negated ("has not received FDA approval", "terminated its buyback") are ignored.
Writes positive_8k_flags.csv.

Bad news: a restatement of past results (Item 4.02), a bankruptcy filing (Item 1.03),
a stock exchange delisting notice (Item 3.01), an auditor change (Item 4.01), or a
CEO/CFO leaving (Item 5.02). Writes negative_8k_flags.csv.

(The file keeps its old name so the daily workflow does not need to change.)

Only the first ~600 KB of each filing is read, to keep the run fast.
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
OUTPUT_CSV = "positive_8k_flags.csv"
NEGATIVE_CSV = "negative_8k_flags.csv"

SLEEP_BETWEEN_REQUESTS = 0.35
MAX_ERRORS_IN_A_ROW = 5
MAX_BYTES_TO_READ = 600_000
MAX_FILINGS_TO_SCAN = 700

I = re.IGNORECASE

CATEGORIES = {
    "share buyback": [
        re.compile(r"\b(?:authoriz\w*|approv\w*|announc\w*|launch\w*|new)\s[^.]{0,100}?\b(?:share|stock)\s+(?:repurchase|buy-?back)", I),
        re.compile(r"\brepurchase\s+(?:up\s+to\s+)?\$\s?[\d.,]+\s*(?:million|billion)\s+(?:of\s+)?(?:its\s+|the\s+company's\s+)?(?:outstanding\s+)?(?:common\s+)?(?:stock|shares)", I),
    ],
    "guidance raised": [
        re.compile(r"\b(?:rais(?:es|ed|ing)|increas(?:es|ed|ing)|boost(?:s|ed|ing)|lift(?:s|ed|ing))\s+(?:its\s+|our\s+|the\s+|full[- ]year\s+|fiscal[- ]year\s+|fiscal\s+\d{4}\s+|annual\s+|financial\s+|revenue\s+|earnings\s+|eps\s+)*(?:guidance|outlook)\b", I),
    ],
    "FDA approval": [
        re.compile(r"\b(?:receiv\w*|announc\w*|secur\w*|obtain\w*)\s+(?:u\.?s\.?\s+)?(?:fda|food and drug administration)\s+(?:marketing\s+)?(?:approval|clearance)", I),
        re.compile(r"\b(?:fda|food and drug administration)\s+(?:has\s+)?approv(?:ed|es)\b", I),
    ],
    "dividend": [
        re.compile(r"\bspecial\s+(?:cash\s+)?dividend\b", I),
        re.compile(r"\b(?:increas\w*|rais\w*)\s+(?:its\s+|the\s+|our\s+)?(?:quarterly\s+|annual\s+|regular\s+)?(?:cash\s+)?dividend", I),
    ],
    "record results": [
        re.compile(r"\brecord\s+(?:(?:first|second|third|fourth)[- ]quarter\s+|quarterly\s+|annual\s+|full[- ]year\s+|fiscal[- ]year\s+)*(?:total\s+)?(?:revenues?|net sales|earnings|net income|bookings)\b", I),
    ],
    "big contract": [
        re.compile(r"\b(?:awarded|secured|won)\s[^.]{0,80}?\$\s?[\d.,]+\s*(?:million|billion)\s[^.]{0,40}?contract", I),
        re.compile(r"\b(?:awarded|secured|won)\s[^.]{0,60}?contract[^.]{0,60}?\$\s?[\d.,]+\s*(?:million|billion)", I),
    ],
}

LEAVING = r"(?:resign\w*|step(?:s|ped|ping)?\s+down|terminat\w*|retire\w*|depart\w*|removed)"
OFFICER = r"(?:chief\s+executive\s+officer|chief\s+financial\s+officer|\bceo\b|\bcfo\b)"

# Bad news. These match the SEC's own item headings, which only appear when the company reports that event.
BAD_CATEGORIES = {
    "restatement": [
        re.compile(r"non-?reliance on previously issued financial statements", I),
    ],
    "bankruptcy": [
        re.compile(r"\bitem\s*1\.03\b", I),
        re.compile(r"voluntary petition for relief under chapter\s*(?:7|11)\b", I),
    ],
    "delisting notice": [
        re.compile(r"notice of delisting or failure to satisfy a continued listing rule", I),
    ],
    "auditor change": [
        re.compile(r"changes in registrant.s certifying accountant", I),
    ],
    "executive departure": [
        re.compile(OFFICER + r"[^.]{0,150}?" + LEAVING, I),
        re.compile(LEAVING + r"[^.]{0,150}?" + OFFICER, I),
    ],
}
# A bad-news category only counts when its heading is present, and not when the text after it
# shows the problem was fixed.
BAD_REQUIRES = {"executive departure": re.compile(r"\bitem\s*5\.02\b", I)}
BAD_FIXED = re.compile(r"regained compliance|regain compliance|back into compliance|has cured|were cured", I)
BAD_FIXED_CATEGORIES = ("delisting notice",)

# If one of these words shows up just before the match, the news is not good news.
NEGATIVE = re.compile(
    r"\b(?:not|no|unable|unless|until|terminat\w*|suspend\w*|cancel\w*|discontinu\w*|withdr\w*|"
    r"lower\w*|reduc\w*|cut|decreas\w*|failed|denied|whether|if|could|would|seek\w*)\b",
    re.IGNORECASE,
)
LOOKBACK_CHARS = 70


def normalize_cik(raw) -> str:
    digits = "".join(ch for ch in str(raw) if ch.isdigit())
    return digits.zfill(10) if digits else ""


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


def clean(raw_text: str) -> str:
    text = re.sub(r"<[^>]+>", " ", raw_text)
    text = html.unescape(text).replace("\xa0", " ").replace("’", "'")
    return re.sub(r"\s+", " ", text)


def find_good_news(text: str):
    """Returns {category: snippet} for each kind of good news found in the text."""
    found = {}
    for category, patterns in CATEGORIES.items():
        for pattern in patterns:
            for m in pattern.finditer(text):
                before = text[max(0, m.start() - LOOKBACK_CHARS): m.start()]
                if NEGATIVE.search(before) or NEGATIVE.search(m.group(0)):
                    continue
                found[category] = text[max(0, m.start() - 60): m.end() + 80].strip()
                break
            if category in found:
                break
    return found


def find_bad_news(text: str):
    """Returns {category: snippet} for each kind of bad news found in the text."""
    found = {}
    for category, patterns in BAD_CATEGORIES.items():
        required = BAD_REQUIRES.get(category)
        if required is not None and not required.search(text):
            continue
        for pattern in patterns:
            m = pattern.search(text)
            if not m:
                continue
            after = text[m.end(): m.end() + 800]
            if category in BAD_FIXED_CATEGORIES and BAD_FIXED.search(after):
                continue
            found[category] = text[max(0, m.start() - 60): m.end() + 100].strip()
            break
    return found


def scan_filing(url: str):
    chunks = []
    bytes_read = 0
    with get_with_retry(url, HEADERS, stream=True, timeout=30) as resp:
        resp.raise_for_status()
        for chunk in resp.iter_content(chunk_size=65536):
            if not chunk:
                continue
            chunks.append(chunk)
            bytes_read += len(chunk)
            if bytes_read >= MAX_BYTES_TO_READ:
                break
    text = clean(b"".join(chunks).decode("utf-8", errors="ignore"))
    return find_good_news(text), find_bad_news(text)


def main():
    try:
        with open(INPUT_CSV, newline="") as f:
            rows = list(csv.DictReader(f))
    except FileNotFoundError:
        print(f"{INPUT_CSV} not found — run sec_daily_watcher.py first.")
        return

    seen_urls = set()
    target_rows = []
    for r in rows:
        if r["form_type"] == "8-K" and r["url"] not in seen_urls:
            seen_urls.add(r["url"])
            target_rows.append(r)
    target_rows = target_rows[:MAX_FILINGS_TO_SCAN]
    print(f"Scanning {len(target_rows)} 8-K filing(s) for good and bad news...")

    flagged = []
    flagged_bad = []
    errors_in_a_row = 0

    for row in target_rows:
        try:
            found, found_bad = scan_filing(row["url"])
            errors_in_a_row = 0
            for results, bucket in ((found, flagged), (found_bad, flagged_bad)):
                if results:
                    bucket.append({
                        "company": row["company"],
                        "cik": normalize_cik(row["cik"]),
                        "form_type": row["form_type"],
                        "date_filed": row["date_filed"],
                        "categories": "; ".join(results.keys()),
                        "snippet": " || ".join(f"{k}: {v}" for k, v in results.items()),
                        "url": row["url"],
                    })
        except requests.RequestException as e:
            print(f"  Error fetching {row['url']}: {e}")
            errors_in_a_row += 1
            if errors_in_a_row >= MAX_ERRORS_IN_A_ROW:
                print("  Too many errors in a row. Stopping this check early and keeping what we have.")
                break
        time.sleep(SLEEP_BETWEEN_REQUESTS)

    fields = ["company", "cik", "form_type", "date_filed", "categories", "snippet", "url"]
    for path, rows_out in ((OUTPUT_CSV, flagged), (NEGATIVE_CSV, flagged_bad)):
        with open(path, "w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=fields)
            writer.writeheader()
            for row in rows_out:
                writer.writerow(row)

    print(f"Flagged {len(flagged)} 8-K filing(s) with good news.")
    print(f"Flagged {len(flagged_bad)} 8-K filing(s) with bad news.")
    print(f"Wrote {OUTPUT_CSV} and {NEGATIVE_CSV}")


if __name__ == "__main__":
    main()
