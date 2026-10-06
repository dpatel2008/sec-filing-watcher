"""
resume_summary.py
A one-page results summary of your paper trading.

Run it any time:   python tracker/resume_summary.py
It prints the numbers, writes tracker_data/results_summary.html, and suggests resume lines
that use your real results. It will not suggest performance claims until there are enough
closed trades for the numbers to mean something.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import analytics as A  # noqa: E402
import config as C  # noqa: E402
import dashboard as D  # noqa: E402
import engine  # noqa: E402

MIN_TRADES_FOR_CLAIMS = 20
MIN_DAYS_FOR_BETA_LINE = 60
DISCLAIMER = (
    "Paper trading only. Results use delayed quotes and ignore commissions and real-world slippage, "
    "so they are research results, not proof of live performance."
)


def load_curve():
    curve = []
    for r in engine.read_csv(engine.data_path("equity_curve.csv")):
        try:
            curve.append({
                "date": r["date"],
                "net_liq": float(r["net_liq"]),
                "spy_price": float(r["spy_price"]) if r["spy_price"] not in ("", None) else None,
            })
        except (ValueError, KeyError):
            continue
    curve.sort(key=lambda r: r["date"])
    return curve


def build():
    closed = engine.load_closed_trades()
    curve = load_curve()
    open_trades = engine.load_open_trades()
    open_options = engine.load_open_options()
    return {
        "closed": closed,
        "curve": curve,
        "open_trades": len(open_trades),
        "open_options": len(open_options),
        "stats": A.trade_stats(closed),
        "groups": engine.group_stats(closed),
        "equity": A.equity_stats(curve),
        "alpha_beta": A.alpha_beta(curve),
        "first_day": curve[0]["date"] if curve else None,
        "last_day": curve[-1]["date"] if curve else None,
    }


def resume_lines(summary):
    """Returns a list of suggested resume lines. Performance claims only appear with enough trades."""
    lines = [
        "Built an automated pipeline that scans every SEC EDGAR filing each morning (insider Form 4 trades, "
        "8-K news, share offerings, going-concern language), scores each company, and publishes a ranked "
        "signal list to a live dashboard.",
        "Built a paper-trading system on Interactive Brokers that turns the signals into long and short stock "
        "trades with volatility-based position sizing, market-beta hedging, and defined-risk option positions.",
    ]
    st, eq, ab = summary["stats"], summary["equity"], summary["alpha_beta"]
    if st["n"] < MIN_TRADES_FOR_CLAIMS:
        lines.append(
            f"(Performance line not ready: {st['n']} closed trade(s) so far. Wait for {MIN_TRADES_FOR_CLAIMS}+ "
            "before quoting a hit rate or profit factor.)"
        )
        return lines
    days = eq["days"] if eq else 0
    parts = [f"{st['n']} closed trades", f"{st['hit_rate'] * 100:.0f}% hit rate"]
    if st["profit_factor"] is not None:
        parts.append(f"profit factor {st['profit_factor']:.2f}")
    if eq and eq["sharpe"] is not None:
        parts.append(f"Sharpe {eq['sharpe']:.2f}")
    if eq:
        parts.append(f"max drawdown {eq['max_drawdown'] * 100:.1f}%")
    lines.append(f"Paper-traded the strategy for {days} trading days: " + ", ".join(parts) + ".")
    if ab["beta"] is not None and ab["n"] >= MIN_DAYS_FOR_BETA_LINE:
        lines.append(
            f"Measured a portfolio beta of {ab['beta']:.2f} versus SPY after hedging "
            f"(annualized alpha {ab['alpha_annual'] * 100:+.1f}%, R-squared {ab['r2']:.2f})."
        )
    elif ab["beta"] is not None:
        lines.append(
            f"(Beta and alpha line not ready: {ab['n']} days of data. Wait for {MIN_DAYS_FOR_BETA_LINE}+ days.)"
        )
    return lines


def text_report(summary):
    st, eq, ab = summary["stats"], summary["equity"], summary["alpha_beta"]
    out = ["RESULTS SUMMARY (paper account)", ""]
    if summary["first_day"]:
        out.append(f"Period: {summary['first_day']} to {summary['last_day']}")
    out.append(f"Closed trades: {st['n']}   Open stock trades: {summary['open_trades']}   Open options: {summary['open_options']}")
    if st["n"]:
        out.append(f"Hit rate: {st['hit_rate'] * 100:.0f}%   Total P&L: ${st['total_pnl']:,.0f}")
        if st["profit_factor"] is not None:
            out.append(f"Profit factor: {st['profit_factor']:.2f}")
        if st["avg_win"] is not None and st["avg_loss"] is not None:
            out.append(f"Average win {D.money(st['avg_win'])}   Average loss {D.money(st['avg_loss'])}")
    if eq:
        out.append(f"Account return: {eq['total_return'] * 100:+.2f}%   Max drawdown: {eq['max_drawdown'] * 100:.2f}%")
        if eq["sharpe"] is not None:
            out.append(f"Sharpe: {eq['sharpe']:.2f}")
    if ab["beta"] is not None:
        out.append(f"Beta vs SPY: {ab['beta']:.2f}   Alpha (annualized): {ab['alpha_annual'] * 100:+.1f}%")
    out += ["", "BY SIGNAL TYPE"]
    if summary["groups"]:
        for name, g in summary["groups"].items():
            hit = f"{g['hit_rate'] * 100:.0f}%" if g["hit_rate"] is not None else "-"
            out.append(f"  {name}: {g['n']} trade(s), hit rate {hit}, P&L ${g['total_pnl']:,.0f}")
    else:
        out.append("  no closed trades yet")
    out += ["", "SUGGESTED RESUME LINES"] + [f"  - {line}" for line in resume_lines(summary)]
    out += ["", DISCLAIMER]
    return "\n".join(out)


def html_report(summary):
    st, eq, ab = summary["stats"], summary["equity"], summary["alpha_beta"]
    tiles = [
        D.tile("Closed trades", str(st["n"])),
        D.tile("Hit rate", D.pct(st["hit_rate"], 0) if st["hit_rate"] is not None else "-"),
        D.tile("Profit factor", D.num(st["profit_factor"]) if st["profit_factor"] is not None else "-"),
        D.tile("Total P&L", D.money(st["total_pnl"], True), D.tone(st["total_pnl"])),
    ]
    if eq:
        tiles += [
            D.tile("Account return", D.pct(eq["total_return"], 2, True), D.tone(eq["total_return"])),
            D.tile("Max drawdown", D.pct(eq["max_drawdown"], 2)),
            D.tile("Sharpe", D.num(eq["sharpe"]) if eq["sharpe"] is not None else "needs 6+ days"),
        ]
    tiles += [
        D.tile("Beta vs SPY", D.num(ab["beta"]) if ab["beta"] is not None else "needs 20+ days"),
        D.tile("Alpha (annual)", D.pct(ab["alpha_annual"], 1, True) if ab["alpha_annual"] is not None else "needs 20+ days"),
    ]

    def row(name, g):
        return [
            D.td(name), D.td(g["n"]), D.td(D.pct(g["hit_rate"], 0) if g["hit_rate"] is not None else "-"),
            D.td(D.num(g["profit_factor"]) if g["profit_factor"] is not None else "-"),
            D.td(D.money(g["total_pnl"], True), D.tone(g["total_pnl"])),
            D.td(D.num(g["avg_hold_days"], 1) if g["avg_hold_days"] is not None else "-"),
        ]

    rows = [row("All closed trades", st)] + [row(k, v) for k, v in summary["groups"].items()] if st["n"] else []
    table = D.table(["Signal", "Trades", "Hit rate", "Profit factor", "Total P&L", "Avg days"], rows, "No closed trades yet.")
    curve = D.curve_svg(summary["curve"])
    bullets = "<ul>" + "".join(f"<li>{D.esc(line)}</li>" for line in resume_lines(summary)) + "</ul>"
    css = """
    *{box-sizing:border-box;margin:0;padding:0}
    body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;background:#0f1115;color:#e5e7eb;padding:24px;max-width:980px;margin:0 auto}
    h1{font-size:24px;color:#f9fafb} h2{font-size:15px;margin:28px 0 10px;color:#f3f4f6;text-transform:uppercase;letter-spacing:.06em}
    .sub{color:#9ca3af;font-size:13px;margin-top:4px}
    .tiles{display:flex;flex-wrap:wrap;gap:10px;margin:10px 0}
    .tile{background:#16181d;border:1px solid #2a2d35;border-radius:8px;padding:10px 14px;min-width:140px}
    .tl{font-size:11px;color:#9ca3af;text-transform:uppercase;letter-spacing:.05em}.tv{font-size:18px;font-weight:700;margin-top:4px}
    .wrap{overflow-x:auto}table{width:100%;border-collapse:collapse;background:#16181d;font-size:13px}
    th{text-align:left;padding:9px 10px;background:#1c1f26;color:#9ca3af;font-size:11px;text-transform:uppercase;letter-spacing:.05em;white-space:nowrap}
    td{padding:9px 10px;border-bottom:1px solid #22252c;vertical-align:top}
    .pos{color:#34d399}.neg{color:#f87171}.empty,.note{color:#6b7280;font-size:13px;margin:8px 0}
    p{font-size:14px;line-height:1.5;margin:6px 0}
    ul{margin:6px 0 6px 20px;font-size:14px;line-height:1.6}
    """
    period = (
        f"{D.esc(summary['first_day'])} to {D.esc(summary['last_day'])}" if summary["first_day"] else "no data yet"
    )
    return f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Paper Trading Results</title><style>{css}</style></head><body>
<h1>Paper Trading Results</h1>
<div class="sub">SEC filing signal strategy - {period}</div>
<div class="tiles">{"".join(tiles)}</div>
<h2>Account value</h2>{curve}
<h2>Results by signal type</h2>{table}
<h2>Suggested resume lines</h2>{bullets}
<p class="note">{D.esc(DISCLAIMER)}</p>
</body></html>
"""


def main():
    summary = build()
    print(text_report(summary))
    os.makedirs(C.DATA_DIR, exist_ok=True)
    path = engine.data_path("results_summary.html")
    with open(path, "w") as f:
        f.write(html_report(summary))
    print(f"\nSaved the one-page version to {path}")
    return path


if __name__ == "__main__":
    main()
