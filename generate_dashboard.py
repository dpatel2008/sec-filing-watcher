"""
generate_dashboard.py
Builds one self-contained HTML report (dashboard.html) from
daily_signals_ranked.csv. Open it in any browser.
"""

import csv
import html
from datetime import datetime
from zoneinfo import ZoneInfo

INPUT_CSV = "daily_signals_ranked.csv"
OUTPUT_HTML = "dashboard.html"
MAX_ROWS = 100


def score_color(score: int) -> str:
    if score >= 60:
        return "#dc2626"
    if score >= 35:
        return "#ea580c"
    if score >= 15:
        return "#ca8a04"
    return "#6b7280"


def score_label(score: int) -> str:
    if score >= 60:
        return "HIGH"
    if score >= 35:
        return "MODERATE"
    if score >= 15:
        return "MILD"
    return "LOW"


def main():
    try:
        with open(INPUT_CSV, newline="") as f:
            rows = list(csv.DictReader(f))
    except FileNotFoundError:
        rows = []

    rows = rows[:MAX_ROWS]
    generated = datetime.now(ZoneInfo("America/New_York")).strftime("%Y-%m-%d %H:%M ET")
    data_date = html.escape(rows[0].get("date_filed", "")) if rows else ""

    row_html = []
    for row in rows:
        score = int(float(row.get("score", 0) or 0))
        company = html.escape(row.get("company", ""))
        ticker = html.escape(row.get("ticker", "") or "no ticker")
        form_types = html.escape(row.get("form_types", ""))
        reasons = html.escape(row.get("reasons", ""))
        url = html.escape(row.get("sample_filing_url", ""))
        color = score_color(score)

        row_html.append(f"""
        <tr>
          <td><span class="score-pill" style="background:{color}">{score}</span></td>
          <td><span class="severity" style="color:{color}">{score_label(score)}</span></td>
          <td class="company-cell">
            <div class="company-name">{company}</div>
            <div class="ticker">{ticker}</div>
          </td>
          <td class="form-types">{form_types}</td>
          <td class="reasons">{reasons}</td>
          <td><a href="{url}" target="_blank" class="filing-link">View</a></td>
        </tr>
        """)

    rows_joined = "\n".join(row_html) if row_html else """
        <tr><td colspan="6" class="empty-state">No signals found for this day.</td></tr>
    """

    subtitle = f"Filings dated {data_date} · " if data_date else ""

    html_doc = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Daily SEC Signal Report</title>
<style>
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
    background: #0f1115;
    color: #e5e7eb;
    padding: 32px;
  }}
  .header h1 {{ font-size: 24px; font-weight: 700; color: #f9fafb; }}
  .header .date {{ color: #9ca3af; font-size: 14px; margin-top: 4px; }}
  .legend {{ display: flex; gap: 16px; margin: 16px 0 24px 0; font-size: 12px; color: #9ca3af; flex-wrap: wrap; }}
  .legend span {{ display: inline-flex; align-items: center; gap: 6px; }}
  .legend .dot {{ width: 8px; height: 8px; border-radius: 50%; display: inline-block; }}
  .table-wrap {{ overflow-x: auto; }}
  table {{ width: 100%; border-collapse: collapse; background: #16181d; border-radius: 8px; overflow: hidden; }}
  thead th {{
    text-align: left; padding: 12px 16px; background: #1c1f26; color: #9ca3af;
    font-size: 11px; text-transform: uppercase; letter-spacing: 0.05em;
    border-bottom: 1px solid #2a2d35;
  }}
  tbody td {{ padding: 14px 16px; border-bottom: 1px solid #22252c; font-size: 14px; vertical-align: top; }}
  tbody tr:hover {{ background: #1a1d24; }}
  .score-pill {{
    display: inline-block; min-width: 32px; text-align: center; padding: 4px 8px;
    border-radius: 6px; color: white; font-weight: 700; font-size: 13px;
  }}
  .severity {{ font-size: 11px; font-weight: 700; letter-spacing: 0.05em; }}
  .company-cell {{ min-width: 160px; }}
  .company-name {{ font-weight: 600; color: #f3f4f6; }}
  .ticker {{ color: #6b7280; font-size: 12px; margin-top: 2px; }}
  .form-types {{ color: #9ca3af; font-size: 12px; max-width: 140px; }}
  .reasons {{ color: #d1d5db; font-size: 13px; max-width: 380px; }}
  .filing-link {{ color: #60a5fa; text-decoration: none; font-size: 13px; font-weight: 500; }}
  .filing-link:hover {{ text-decoration: underline; }}
  .empty-state {{ text-align: center; padding: 40px; color: #6b7280; }}
  .footer {{ margin-top: 24px; font-size: 12px; color: #4b5563; }}
</style>
</head>
<body>
  <div class="header">
    <h1>Daily SEC Signal Report</h1>
    <div class="date">{subtitle}generated {generated}</div>
  </div>

  <div class="legend">
    <span><span class="dot" style="background:#dc2626"></span> High (60+)</span>
    <span><span class="dot" style="background:#ea580c"></span> Moderate (35-59)</span>
    <span><span class="dot" style="background:#ca8a04"></span> Mild (15-34)</span>
    <span><span class="dot" style="background:#6b7280"></span> Low (under 15)</span>
  </div>

  <div class="table-wrap">
  <table>
    <thead>
      <tr>
        <th>Score</th><th>Severity</th><th>Company</th><th>Forms</th><th>Why it scored</th><th>Filing</th>
      </tr>
    </thead>
    <tbody>
      {rows_joined}
    </tbody>
  </table>
  </div>

  <div class="footer">
    Built from SEC EDGAR filings, Form 4 insider sales, going-concern language checks
    and free-tier market data. A score is a prompt to look closer, not a trade signal.
  </div>
</body>
</html>
"""

    with open(OUTPUT_HTML, "w") as f:
        f.write(html_doc)

    print(f"Wrote {OUTPUT_HTML} with {len(rows)} ranked compan(ies).")


if __name__ == "__main__":
    main()
