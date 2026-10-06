"""
form4_cluster_check.py
Opens every Form 4 from all_filings.csv and looks for insider SELLING and insider BUYING.
Trades are grouped by the company the insider works for (the "issuer"), so
several different insiders trading at one company shows up as a cluster.
Writes form4_clusters.csv (selling) and form4_buys.csv (buying).

A single CEO, CFO or president buying $50,000 or more also counts.
Only open-market purchases (transaction code P) count as buying. Stock awards,
option exercises and gifts are ignored, because they are not a bet on the stock.
"""

import csv
import re
import time
import requests
import xml.etree.ElementTree as ET

USER_AGENT = "Dillen Patel dillenpatel2008@gmail.com"

HEADERS = {
    "User-Agent": USER_AGENT,
    "Accept-Encoding": "gzip, deflate",
    "Host": "www.sec.gov",
}

INPUT_CSV = "all_filings.csv"
OUTPUT_CSV = "form4_clusters.csv"
BUYS_CSV = "form4_buys.csv"

SLEEP_BETWEEN_REQUESTS = 0.35
MAX_FORM4_TO_CHECK = 2500
MAX_ERRORS_IN_A_ROW = 5
MIN_BUYER_DOLLARS = 10_000   # a buyer must spend at least this much to count
MIN_EXEC_DOLLARS = 50_000    # a CEO, CFO or president buying this much on their own is a signal
EXEC_TITLE = re.compile(r"\b(?:chief executive|ceo|chief financial|cfo|president|chairman)\b", re.IGNORECASE)


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


def normalize_cik(raw) -> str:
    digits = "".join(ch for ch in str(raw) if ch.isdigit())
    return digits.zfill(10) if digits else ""


def extract_xml_blocks(submission_text: str):
    return re.findall(r"<XML>(.*?)</XML>", submission_text, re.DOTALL)


def text_of(node, path: str) -> str:
    el = node.find(path)
    return el.text.strip() if el is not None and el.text else ""


def to_float(text: str) -> float:
    try:
        return float(text)
    except (TypeError, ValueError):
        return 0.0


def parse_form4(xml_text: str):
    try:
        root = ET.fromstring(xml_text.strip())
    except ET.ParseError:
        return None

    if root.tag != "ownershipDocument":
        return None

    owners = [
        el.text.strip()
        for el in root.findall("reportingOwner/reportingOwnerId/rptOwnerName")
        if el.text
    ]
    is_exec = False
    for owner_el in root.findall("reportingOwner"):
        title = text_of(owner_el, "reportingOwnerRelationship/officerTitle")
        if title and EXEC_TITLE.search(title):
            is_exec = True

    sold_shares = 0.0
    saw_sale = False
    bought_shares = 0.0
    bought_dollars = 0.0
    saw_buy = False
    for txn in root.findall(".//nonDerivativeTransaction"):
        code = text_of(txn, "transactionCoding/transactionCode")
        shares = to_float(text_of(txn, "transactionAmounts/transactionShares/value"))
        if code == "S":
            saw_sale = True
            sold_shares += shares
        elif code == "P":
            saw_buy = True
            price = to_float(text_of(txn, "transactionAmounts/transactionPricePerShare/value"))
            bought_shares += shares
            bought_dollars += shares * price

    return {
        "issuer_cik": text_of(root, "issuer/issuerCik"),
        "issuer_name": text_of(root, "issuer/issuerName"),
        "issuer_ticker": text_of(root, "issuer/issuerTradingSymbol"),
        "owner": " & ".join(owners) if owners else "Unknown",
        "sold_shares": sold_shares,
        "saw_sale": saw_sale,
        "bought_shares": bought_shares,
        "bought_dollars": bought_dollars,
        "saw_buy": saw_buy,
        "is_exec": is_exec,
    }


def main():
    try:
        with open(INPUT_CSV, newline="") as f:
            rows = list(csv.DictReader(f))
    except FileNotFoundError:
        print(f"{INPUT_CSV} not found — run sec_daily_watcher.py first.")
        return

    # SEC lists every Form 4 twice (once under the insider, once under the
    # company) with the same URL, so keep only one copy of each.
    seen_urls = set()
    form4_rows = []
    for row in rows:
        if row["form_type"] == "4" and row["url"] not in seen_urls:
            seen_urls.add(row["url"])
            form4_rows.append(row)
    form4_rows = form4_rows[:MAX_FORM4_TO_CHECK]
    print(f"Checking {len(form4_rows)} unique Form 4 filing(s)...")

    groups = {}       # companies with insider sales
    buy_groups = {}   # companies with insider purchases
    errors_in_a_row = 0

    for row in form4_rows:
        try:
            resp = get_with_retry(row["url"], HEADERS, timeout=20)
            resp.raise_for_status()
            errors_in_a_row = 0

            parsed = None
            for xml_block in extract_xml_blocks(resp.text):
                result = parse_form4(xml_block)
                if result is not None:
                    parsed = result
                    break

            if parsed:
                cik = normalize_cik(parsed["issuer_cik"])
                if cik and parsed["saw_sale"]:
                    group = groups.setdefault(cik, {
                        "company": parsed["issuer_name"],
                        "ticker": parsed["issuer_ticker"],
                        "date_filed": row["date_filed"],
                        "sellers": {},
                    })
                    group["sellers"][parsed["owner"]] = (
                        group["sellers"].get(parsed["owner"], 0.0) + parsed["sold_shares"]
                    )
                if cik and parsed["saw_buy"]:
                    group = buy_groups.setdefault(cik, {
                        "company": parsed["issuer_name"],
                        "ticker": parsed["issuer_ticker"],
                        "date_filed": row["date_filed"],
                        "buyers": {},
                        "exec_buyers": {},
                        "shares": 0.0,
                    })
                    group["buyers"][parsed["owner"]] = (
                        group["buyers"].get(parsed["owner"], 0.0) + parsed["bought_dollars"]
                    )
                    if parsed["is_exec"]:
                        group["exec_buyers"][parsed["owner"]] = (
                            group["exec_buyers"].get(parsed["owner"], 0.0) + parsed["bought_dollars"]
                        )
                    group["shares"] += parsed["bought_shares"]

        except requests.RequestException as e:
            print(f"  Error fetching {row['url']}: {e}")
            errors_in_a_row += 1
            if errors_in_a_row >= MAX_ERRORS_IN_A_ROW:
                print("  Too many errors in a row. Stopping this check early and keeping what we have.")
                break
        time.sleep(SLEEP_BETWEEN_REQUESTS)

    with open(OUTPUT_CSV, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=[
            "company", "cik", "ticker", "date_filed", "num_sellers",
            "total_shares_sold", "sellers", "cluster_flag"
        ])
        writer.writeheader()

        for cik, group in groups.items():
            sellers = group["sellers"]
            writer.writerow({
                "company": group["company"],
                "cik": cik,
                "ticker": group["ticker"],
                "date_filed": group["date_filed"],
                "num_sellers": len(sellers),
                "total_shares_sold": int(sum(sellers.values())),
                "sellers": "; ".join(sellers.keys()),
                "cluster_flag": "YES" if len(sellers) >= 2 else "no",
            })

    buy_clusters = 0
    exec_buys = 0
    with open(BUYS_CSV, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=[
            "company", "cik", "ticker", "date_filed", "num_buyers",
            "total_shares_bought", "total_dollars_bought", "buyers", "buy_cluster_flag",
            "exec_buyer_flag", "exec_buyer_dollars"
        ])
        writer.writeheader()

        for cik, group in buy_groups.items():
            real_buyers = {name: d for name, d in group["buyers"].items() if d >= MIN_BUYER_DOLLARS}
            if not real_buyers:
                continue
            flag = "YES" if len(real_buyers) >= 2 else "no"
            if flag == "YES":
                buy_clusters += 1
            exec_best = max(group["exec_buyers"].values(), default=0.0)
            exec_flag = "YES" if exec_best >= MIN_EXEC_DOLLARS else "no"
            if exec_flag == "YES":
                exec_buys += 1
            writer.writerow({
                "company": group["company"],
                "cik": cik,
                "ticker": group["ticker"],
                "date_filed": group["date_filed"],
                "num_buyers": len(real_buyers),
                "total_shares_bought": int(group["shares"]),
                "total_dollars_bought": int(sum(real_buyers.values())),
                "buyers": "; ".join(real_buyers.keys()),
                "buy_cluster_flag": flag,
                "exec_buyer_flag": exec_flag,
                "exec_buyer_dollars": int(exec_best),
            })

    clusters = sum(1 for g in groups.values() if len(g["sellers"]) >= 2)
    print(f"{len(groups)} compan(ies) with insider sales, {clusters} with 2+ insiders selling.")
    print(f"{len(buy_groups)} compan(ies) with insider purchases, {buy_clusters} with 2+ insiders buying, {exec_buys} with a CEO/CFO/president buying.")
    print(f"Wrote {OUTPUT_CSV} and {BUYS_CSV}")


if __name__ == "__main__":
    main()
