"""
dashboard.py
Turns the report into one HTML page (tracker_dashboard.html). It stays on your
computer, so broker data is never published.
"""

import html


def _f(x):
    try:
        if x is None or x == "":
            return None
        return float(x)
    except (TypeError, ValueError):
        return None


def money(x, signed=False):
    v = _f(x)
    if v is None:
        return "-"
    sign = "+" if signed and v > 0 else ""
    return f"{sign}{'-' if v < 0 else ''}${abs(v):,.0f}"


def pct(x, digits=1, signed=False):
    v = _f(x)
    if v is None:
        return "-"
    sign = "+" if signed and v > 0 else ""
    return f"{sign}{v * 100:.{digits}f}%"


def num(x, digits=2):
    v = _f(x)
    return "-" if v is None else f"{v:,.{digits}f}"


def esc(x):
    return html.escape("" if x is None else str(x))


def tone(x):
    v = _f(x)
    if v is None or v == 0:
        return ""
    return "pos" if v > 0 else "neg"


def table(headers, rows, empty="Nothing yet."):
    if not rows:
        return f'<p class="empty">{esc(empty)}</p>'
    head = "".join(f"<th>{esc(h)}</th>" for h in headers)
    body = "".join("<tr>" + "".join(cells) + "</tr>" for cells in rows)
    return f'<div class="wrap"><table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table></div>'


def td(text, cls=""):
    return f'<td class="{cls}">{esc(text)}</td>'


def tile(label, value, cls=""):
    return f'<div class="tile"><div class="tl">{esc(label)}</div><div class="tv {cls}">{esc(value)}</div></div>'


def curve_svg(curve):
    if len(curve) < 2:
        return '<p class="empty">The equity curve appears after the tracker has run on two or more days.</p>'
    w, h, pad = 760, 220, 40
    values = [p["net_liq"] for p in curve]
    lo, hi = min(values), max(values)
    if hi == lo:
        hi, lo = hi + 1, lo - 1
    n = len(curve)
    pts = []
    for i, v in enumerate(values):
        x = pad + (w - 2 * pad) * i / (n - 1)
        y = h - pad - (h - 2 * pad) * (v - lo) / (hi - lo)
        pts.append(f"{x:.1f},{y:.1f}")
    return (
        f'<svg viewBox="0 0 {w} {h}" width="100%" role="img" aria-label="Account value over time">'
        f'<rect x="0" y="0" width="{w}" height="{h}" fill="#16181d"/>'
        f'<polyline points="{" ".join(pts)}" fill="none" stroke="#60a5fa" stroke-width="2"/>'
        f'<text x="{pad}" y="16" fill="#9ca3af" font-size="11">{esc(money(hi))}</text>'
        f'<text x="{pad}" y="{h - 8}" fill="#9ca3af" font-size="11">{esc(money(lo))}</text>'
        f'<text x="{w - pad}" y="{h - 8}" fill="#9ca3af" font-size="11" text-anchor="end">'
        f'{esc(curve[0]["date"])} to {esc(curve[-1]["date"])}</text></svg>'
    )


def spread_text(p):
    status = p.get("spread_status") or "-"
    if _f(p.get("spread_qty")):
        return f'{status}: {int(_f(p["spread_qty"]))} for {money(p.get("spread_cost"))}'
    return status


def render(r):
    expo = r["exposure"]
    open_pnl = sum(row["pnl"] for row in r["open_rows"])
    option_pnl = sum(o["pnl"] for o in r.get("option_rows", []) if o["pnl"] is not None)
    closed_pnl = r["trade_stats"]["total_pnl"]

    tiles = "".join([
        tile("Account value", money(r["equity"])),
        tile("Open P&L", money(open_pnl, True), tone(open_pnl)),
        tile("Open option P&L", money(option_pnl, True), tone(option_pnl)),
        tile("Closed P&L", money(closed_pnl, True), tone(closed_pnl)),
        tile("Gross exposure", money(expo["gross"])),
        tile("Net exposure", money(expo["net"], True)),
        tile("Beta-adjusted net", money(expo["net_beta"], True)),
        tile("After hedge", money(expo["net_beta_after_hedge"], True)),
    ])

    plan_rows = []
    for p in r["plan"]:
        plan_rows.append([
            td(p.get("symbol")), td(p.get("direction")), td(p.get("score")),
            td(p.get("qty") or "-"), td(money(p.get("notional"))),
            td(num(p.get("stop"))), td(num(p.get("target"))),
            td(num(p.get("beta"))), td(p.get("shortable_level") if p.get("shortable_level") not in (None, "") else "-"),
            td(p.get("decision"), "dec-" + str(p.get("decision", "")).lower().replace(" ", "")),
            td(p.get("decision_reason")), td(spread_text(p)), td(p.get("flags")),
        ])
    plan_html = table(["Symbol", "Side", "Score", "Qty", "Size", "Stop", "Target", "Beta", "Borrow", "Decision", "Why", "Option", "Flags"],
                      plan_rows, "No plan yet. Run the tracker in plan or trade mode.")

    open_rows = [[
        td(o["symbol"]), td(o["direction"]), td(o["qty"]), td(num(o["entry_price"])), td(num(o["price"])),
        td(money(o["pnl"], True), tone(o["pnl"])), td(pct(o["pnl_pct"], 1, True), tone(o["pnl_pct"])),
        td(num(o["stop"])), td(num(o["target"])), td(o["exit_by"]), td(o["days_held"]), td(o["tags"]),
    ] for o in r["open_rows"]]
    open_html = table(["Symbol", "Side", "Qty", "Entry", "Now", "P&L", "P&L %", "Stop", "Target", "Exit by", "Days", "Signals"],
                      open_rows, "No open paper trades.")

    spread_open = [[
        td(o["symbol"]), td(o["kind"]), td(o["legs"]), td(o["expiry"]), td(o["qty"]),
        td(money(o["max_loss"])), td(money(o["max_profit"])), td(num(o["entry_debit"])),
        td(num(o["mid"]) if o["mid"] is not None else "-"),
        td(money(o["pnl"], True) if o["pnl"] is not None else "-", tone(o["pnl"])), td(o["entry_date"]),
    ] for o in r.get("option_rows", [])]
    spread_open_html = table(
        ["Symbol", "Type", "Legs", "Expires", "Contracts", "Most it can lose", "Best case", "Paid", "Now", "P&L", "Opened"],
        spread_open, "No open options.",
    )

    events = r.get("events") or []
    events_html = (
        "<ul>" + "".join(f"<li>{esc(line)}</li>" for line in events) + "</ul>"
        if events else '<p class="empty">No orders were placed in this run.</p>'
    )

    h = r["hedge_info"]
    if h:
        hedge_html = (
            f'<p>Net beta exposure of the stock book: <b>{esc(money(h["net_beta_dollars"], True))}</b>. '
            f'Target hedge: <b>{esc(money(h["hedge_dollars"], True))}</b> of {esc(h["symbol"])} '
            f'(about {esc(num(h["hedge_shares"], 0))} shares, roughly {esc(num(h["m2k_contracts"], 1))} Micro Russell 2000 futures). '
            f'Held now: {esc(num(h["current_shares"], 0))} shares. Action: {esc(h["status"])}.</p>'
        )
    else:
        hedge_html = f'<p>Hedge position now: <b>{esc(money(expo["hedge_notional"], True))}</b>. Run in trade mode to rebalance it.</p>'

    risk = r["risk"]
    if risk:
        risk_html = (
            '<div class="tiles">' + "".join([
                tile("1-day VaR 95%", money(risk["var95"])), tile("1-day VaR 99%", money(risk["var99"])),
                tile("Expected shortfall 95%", money(risk["cvar95"])), tile("Daily volatility", money(risk["daily_vol_dollars"])),
            ]) + f'</div><p class="note">Normal-distribution estimate using {esc(risk["method"])} across {risk["n_positions"]} position(s). Real losses can be much larger.</p>'
        )
    else:
        risk_html = '<p class="empty">Risk numbers appear once there are open positions with price history.</p>'
    stress_html = table(["Scenario", "Estimated P&L"], [[td(s["scenario"]), td(money(s["pnl"], True), tone(s["pnl"]))] for s in r["stress"]])

    es, ab = r["equity_stats"], r["alpha_beta"]
    perf_tiles = []
    if es:
        perf_tiles += [
            tile("Total return", pct(es["total_return"], 2, True), tone(es["total_return"])),
            tile("Max drawdown", pct(es["max_drawdown"], 2)),
            tile("Volatility (annual)", pct(es["ann_vol"]) if es["ann_vol"] is not None else "needs more days"),
            tile("Sharpe", num(es["sharpe"]) if es["sharpe"] is not None else "needs 6+ days"),
        ]
    perf_tiles += [
        tile("Beta vs SPY", num(ab["beta"]) if ab["beta"] is not None else f"needs 20+ days ({ab['n']} so far)"),
        tile("Alpha (annualized)", pct(ab["alpha_annual"], 1, True) if ab["alpha_annual"] is not None else "needs 20+ days"),
        tile("R-squared", num(ab["r2"]) if ab["r2"] is not None else "-"),
    ]
    perf_html = '<div class="tiles">' + "".join(perf_tiles) + "</div>" + curve_svg(r["curve"])

    ts = r["trade_stats"]

    def stat_row(name, s):
        return [
            td(name), td(s["n"]), td(pct(s["hit_rate"], 0) if s["hit_rate"] is not None else "-"),
            td(money(s["avg_win"], True) if s["avg_win"] is not None else "-"),
            td(money(s["avg_loss"], True) if s["avg_loss"] is not None else "-"),
            td(num(s["profit_factor"]) if s["profit_factor"] is not None else "-"),
            td(money(s["total_pnl"], True), tone(s["total_pnl"])),
            td(num(s["avg_hold_days"], 1) if s["avg_hold_days"] is not None else "-"),
        ]

    stat_rows = [stat_row("All closed trades", ts)] + [stat_row(tag, s) for tag, s in r["tag_stats"].items()]
    stat_html = table(["Signal", "Trades", "Hit rate", "Avg win", "Avg loss", "Profit factor", "Total P&L", "Avg days"],
                      stat_rows if ts["n"] else [], "No closed trades yet. This fills in as trades exit.")

    recent = sorted(r["closed"], key=lambda t: t["exit_date"], reverse=True)[:15]
    recent_html = table(["Symbol", "Side", "Entry", "Exit", "Reason", "P&L", "Return", "Days"], [[
        td(t["symbol"]), td(t["direction"]), td(f'{t["entry_date"]} @ {num(t["entry_price"])}'),
        td(f'{t["exit_date"]} @ {num(t["exit_price"])}'), td(t["exit_reason"]),
        td(money(t["pnl"], True), tone(t["pnl"])), td(pct(t["return_pct"], 1, True), tone(t["return_pct"])), td(t["hold_days"]),
    ] for t in recent], "No closed trades yet.")

    option_rows = [[
        td(p["symbol"]), td(p.get("direction")), td(p.get("spread_type")), td(p.get("spread_legs")),
        td(money(_f(p.get("spread_debit")) * 100) if _f(p.get("spread_debit")) is not None else "-"),
        td(money(_f(p.get("spread_max_profit")) * 100) if _f(p.get("spread_max_profit")) is not None else "-"),
        td(num(p.get("spread_breakeven"))), td(pct(p.get("opt_atm_iv"), 0)), td(pct(p.get("implied_move_pct"), 1)),
        td(num(p.get("spread_delta"), 2)), td(num(p.get("spread_gamma"), 3)), td(num(p.get("spread_vega"), 2)), td(num(p.get("spread_theta"), 2)),
    ] for p in r["plan"] if p.get("spread_type")]
    option_html = table(["Symbol", "Side", "Idea", "Legs", "Cost / contract", "Max profit", "Breakeven", "ATM IV", "Implied move", "Delta", "Gamma", "Vega", "Theta"],
                        option_rows, "No option ideas yet (needs options data from IBKR).")

    notes = []
    if r["spy_adv"] is not None:
        notes.append(f"Volume check: SPY average daily dollar volume reads {money(r['spy_adv'])}. It should be in the tens of billions.")
    notes.append("Paper results use delayed quotes and market orders. They ignore commissions and real-world slippage, so treat them as research, not proof.")

    css = """
    *{box-sizing:border-box;margin:0;padding:0}
    body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;background:#0f1115;color:#e5e7eb;padding:24px}
    h1{font-size:24px;color:#f9fafb} h2{font-size:15px;margin:28px 0 10px;color:#f3f4f6;text-transform:uppercase;letter-spacing:.06em}
    .sub{color:#9ca3af;font-size:13px;margin-top:4px}
    .tiles{display:flex;flex-wrap:wrap;gap:10px;margin:10px 0}
    .tile{background:#16181d;border:1px solid #2a2d35;border-radius:8px;padding:10px 14px;min-width:140px}
    .tl{font-size:11px;color:#9ca3af;text-transform:uppercase;letter-spacing:.05em}.tv{font-size:18px;font-weight:700;margin-top:4px}
    .wrap{overflow-x:auto}table{width:100%;border-collapse:collapse;background:#16181d;font-size:13px}
    th{text-align:left;padding:9px 10px;background:#1c1f26;color:#9ca3af;font-size:11px;text-transform:uppercase;letter-spacing:.05em;white-space:nowrap}
    td{padding:9px 10px;border-bottom:1px solid #22252c;vertical-align:top}
    .pos{color:#34d399}.neg{color:#f87171}.empty,.note{color:#6b7280;font-size:13px;margin:8px 0}
    .dec-trade,.dec-filled{color:#34d399;font-weight:700}.dec-notfilled{color:#f87171;font-weight:700}.dec-skip{color:#9ca3af}
    p{font-size:14px;line-height:1.5;margin:6px 0}
    ul{margin:6px 0 6px 20px;font-size:14px;line-height:1.6}
    """
    return f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>SEC Signal Tracker</title><style>{css}</style></head><body>
<h1>SEC Signal Tracker (paper account)</h1>
<div class="sub">Account {esc(r["account_id"])} - generated {esc(r["generated"])}</div>
<div class="tiles">{tiles}</div>
<h2>What happened in the last run</h2>{events_html}
<h2>Today's trade plan</h2>{plan_html}
<h2>Open paper trades</h2>{open_html}
<h2>Open options</h2>{spread_open_html}
<p class="note">Options (spreads and single calls or puts) are not counted in the exposure, hedge or risk numbers. Their worst case is the amount paid.</p>
<h2>Hedge</h2>{hedge_html}
<h2>Risk</h2>{risk_html}{stress_html}
<h2>Performance</h2>{perf_html}
<h2>Results by signal type</h2>{stat_html}
<h2>Recent closed trades</h2>{recent_html}
<h2>Option ideas for today's plan</h2>{option_html}
<p class="note">{esc(" ".join(notes))}</p>
</body></html>
"""
