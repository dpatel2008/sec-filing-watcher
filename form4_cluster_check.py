"""
form4_cluster_check.py
Opens every Form 4 from all_filings.csv and looks for insider selling.
Sales are grouped by the company the insider works for (the "issuer"), so
several different insiders selling at one company shows up as a cluster.
Writes form4_clusters.csv.
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

SLEEP_BETWEEN_REQUESTS = 0.2
MAX_FORM4_TO_CHECK = 2500


def normalize_cik(raw) -> str:
    digits = "".join(ch for ch in str(raw) if ch.isdigit())
    return digits.zfill(10) if digits else ""


def extract_xml_blocks(submission_text: str):
    return re.findall(r"<XML>(.*?)</XML>", submission_text, re.DOTALL)


def text_of(node, path: str) -> str:
    el = node.find(path)
    return el.text.strip() if el is not None and el.text else ""


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

    sold_shares = 0.0
    saw_sale = False
    for txn in root.findall(".//nonDerivativeTransaction"):
        if text_of(txn, "transactionCoding/transactionCode") == "S":
            saw_sale = True
            try:
                sold_shares += float(text_of(txn, "transactionAmounts/transactionShares/value"))
            except ValueError:
                pass

    return {
        "issuer_cik": text_of(root, "issuer/issuerCik"),
        "issuer_name": text_of(root, "issuer/issuerName"),
        "issuer_ticker": text_of(root, "issuer/issuerTradingSymbol"),
        "owner": " & ".join(owners) if owners else "Unknown",
        "sold_shares": sold_shares,
        "saw_sale": saw_sale,
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

    groups = {}

    for row in form4_rows:
        try:
            resp = requests.get(row["url"], headers=HEADERS, timeout=20)
            resp.raise_for_status()

            parsed = None
            for xml_block in extract_xml_blocks(resp.text):
                result = parse_form4(xml_block)
                if result is not None:
                    parsed = result
                    break

            if not parsed or not parsed["saw_sale"]:
                continue

            cik = normalize_cik(parsed["issuer_cik"])
            if not cik:
                continue

            group = groups.setdefault(cik, {
                "company": parsed["issuer_name"],
                "ticker": parsed["issuer_ticker"],
                "date_filed": row["date_filed"],
                "sellers": {},
            })
            group["sellers"][parsed["owner"]] = (
                group["sellers"].get(parsed["owner"], 0.0) + parsed["sold_shares"]
            )

        except requests.RequestException as e:
            print(f"  Error fetching {row['url']}: {e}")
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

    clusters = sum(1 for g in groups.values() if len(g["sellers"]) >= 2)
    print(f"{len(groups)} compan(ies) with insider sales, {clusters} with 2+ insiders selling.")
    print(f"Wrote {OUTPUT_CSV}")


if __name__ == "__main__":
    main()
