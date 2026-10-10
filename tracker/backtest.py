"""
backtest.py
Tests each strategy sleeve on saved price history. It uses the same rules as the live sleeves.

  python tracker/run_sleeves.py backtest

Honest limits (also printed with the results):
  * Prices come from the files saved by the prefetch step. The more years you prefetch, the more this means.
  * The stock lists are today's large companies, so the results look better than a real run would
    (survivorship bias).
  * Costs are a flat number of basis points per dollar traded. Real slippage can be larger.
  * Prices are not adjusted for dividends, so ETFs that pay income look a little worse than they are.
"""

import csv
import html
import math
import os

import numpy as np

import config as C
import sleeve_config as SC
import sleeve_strategies as S

WARMUP_DAYS = 260
CASH_RETURN_YEARLY = 0.04


def load_prices(price_dir, symbols=None):
    """{symbol: {"dates": [...], "close": [...]}} from the saved CSV files."""
    out = {}
    if not os.path.isdir(price_dir):
        return out
    for name in sorted(os.listdir(price_dir)):
        if not name.endswith(".csv"):
            continue
        symbol = name[:-4]
        if symbols is not None and symbol not in symbols:
            continue
        dates, closes = [], []
        with open(os.path.join(price_dir, name), newline="") as f:
            for row in csv.DictReader(f):
                try:
                    c = float(row["close"])
                except (KeyError, ValueError):
                    continue
                if c > 0:
                    dates.append(row["date"])
                    closes.append(c)
        if dates:
            out[symbol] = {"dates": dates, "close": closes}
    return out


def align(data, calendar):
    """Puts every symbol on one calendar, carrying the last price forward. Returns {symbol: (array, first_index)}."""
    index = {d: i for i, d in enumerate(calendar)}
    out = {}
    for s, d in data.items():
        arr = np.full(len(calendar), np.nan)
        for day, c in zip(d["dates"], d["close"]):
            i = index.get(day)
            if i is not None:
                arr[i] = c
        valid = np.where(~np.isnan(arr))[0]
        if len(valid) == 0:
            continue
        first = int(valid[0])
        last = arr[first]
        for i in range(first, len(arr)):
            if np.isnan(arr[i]):
                arr[i] = last
            else:
                last = arr[i]
        out[s] = (arr, first)
    return out


def simulate(name, data, cost_bps=5.0, params=None, start=None, end=None):
    """Runs one sleeve over history. Returns {"dates", "nav", "turnover", "trades", "gross"}."""
    universe = [s for s in S.UNIVERSE[name] if s in data]
    if "SPY" not in data:
        raise ValueError("SPY prices are needed as the calendar. Run the prefetch first.")
    calendar = data["SPY"]["dates"]
    aligned = align({s: data[s] for s in set(universe) | {"SPY"} if s in data}, calendar)
    freq = S.FREQ[name]
    stop_pct = SC.SLEEVE_STOP_PCT.get(name)
    nav0 = 100_000.0
    cash = nav0
    shares, cost_basis, entry_day = {}, {}, {}
    nav_series, dates_out, turnover_total, trades, gross_series = [], [], 0.0, 0, []
    first_i = max(WARMUP_DAYS, 0)
    for i in range(first_i, len(calendar) - 1):
        day = calendar[i]
        if start and day < start:
            continue
        if end and day > end:
            break
        # decide with today's close, trade at the NEXT bar's close (live runs decide on yesterday's close and trade today)
        prices = {s: aligned[s][0][i + 1] for s in aligned if aligned[s][1] <= i}
        nav = cash + sum(q * prices[s] for s, q in shares.items() if s in prices)
        due = freq == "daily" or (i > 0 and calendar[i][:7] != calendar[i - 1][:7]) or not dates_out
        weights = None
        if due:
            view = {}
            for s in universe:
                if s in aligned and aligned[s][1] <= i - 30:
                    arr, first = aligned[s]
                    view[s] = {"dates": calendar[first:i + 1], "close": arr[first:i + 1]}
            held = {s: {"qty": q, "avg_cost": cost_basis.get(s, prices.get(s)), "entry_date": entry_day.get(s)}
                    for s, q in shares.items() if q != 0}
            weights = S.RUNNERS[name](view, held, day, params)["weights"]
        elif stop_pct:
            weights = {}
            for s, q in shares.items():
                price = prices.get(s)
                if price is None or q == 0:
                    continue
                cost = cost_basis.get(s, price)
                hit = (q > 0 and price < cost * (1 - stop_pct)) or (q < 0 and price > cost * (1 + stop_pct))
                if not hit:
                    weights[s] = q * price / nav
            if len(weights) == len([1 for q in shares.values() if q != 0]):
                weights = None
        if weights is not None:
            targets = {s: weights.get(s, 0.0) * nav for s in set(weights) | set(shares)}
            for s, tv in targets.items():
                price = prices.get(s)
                if price is None:
                    continue
                cur = shares.get(s, 0.0)
                want = math.floor(abs(tv) / price) * (1 if tv >= 0 else -1)
                if cur != 0 and want != 0 and cur * want > 0 and abs(want - cur) * price < SC.REBALANCE_BAND * abs(want) * price:
                    continue
                if want != 0 and abs(want - cur) * price < SC.MIN_ORDER_DOLLARS:
                    continue
                delta = want - cur
                if delta == 0:
                    continue
                traded = abs(delta) * price
                cash -= delta * price + traded * cost_bps / 1e4
                turnover_total += traded / nav
                trades += 1
                if cur == 0 or cur * want < 0:
                    cost_basis[s] = price
                    entry_day[s] = day
                elif cur * want > 0 and abs(want) > abs(cur):
                    cost_basis[s] = (abs(cur) * cost_basis.get(s, price) + abs(delta) * price) / abs(want)
                if want == 0:
                    shares.pop(s, None)
                    cost_basis.pop(s, None)
                    entry_day.pop(s, None)
                else:
                    shares[s] = want
        nav_now = cash + sum(q * prices[s] for s, q in shares.items() if s in prices)
        gross = sum(abs(q * prices[s]) for s, q in shares.items() if s in prices) / nav_now if nav_now else 0.0
        # one day passes
        nxt = {s: aligned[s][0][i + 1] for s in aligned if aligned[s][1] <= i}
        nav_next = cash + sum(q * nxt.get(s, prices.get(s, 0.0)) for s, q in shares.items())
        dates_out.append(calendar[i + 1])
        nav_series.append(nav_next / nav0)
        gross_series.append(gross)
    return {"dates": dates_out, "nav": nav_series, "turnover": turnover_total, "trades": trades, "gross": gross_series}


def stats(dates, nav):
    if len(nav) < 3:
        return None
    a = np.asarray(nav, dtype=float)
    r = a[1:] / a[:-1] - 1
    years = max(len(r) / 252.0, 1e-9)
    total = float(a[-1] / a[0] - 1) if a[0] else 0.0
    cagr = float(a[-1] ** (1 / years) - 1) if a[-1] > 0 else -1.0
    vol = float(np.std(r, ddof=1) * math.sqrt(252)) if len(r) > 2 else 0.0
    sharpe = float(np.mean(r) / np.std(r, ddof=1) * math.sqrt(252)) if vol > 0 else None
    peak = np.maximum.accumulate(a)
    dd = float(np.min(a / peak - 1))
    return {"days": len(a), "total": total, "cagr": cagr, "vol": vol, "sharpe": sharpe, "max_dd": dd}


def combine(results, allocations):
    """Daily returns of the whole sleeve mix, with unused money earning a cash rate."""
    names = [n for n in results if results[n]["nav"]]
    if not names:
        return None
    length = min(len(results[n]["nav"]) for n in names)
    dates = results[names[0]]["dates"][-length:]
    rets = np.zeros(length - 1)
    used = 0.0
    for n in names:
        a = np.asarray(results[n]["nav"][-length:], dtype=float)
        rets += allocations.get(n, 0.0) * (a[1:] / a[:-1] - 1)
        used += allocations.get(n, 0.0)
    rets += (1.0 - used) * CASH_RETURN_YEARLY / 252.0
    nav = [1.0]
    for x in rets:
        nav.append(nav[-1] * (1 + x))
    return {"dates": dates, "nav": nav}


def benchmark(data, dates):
    spy = data.get("SPY")
    if not spy:
        return None
    index = dict(zip(spy["dates"], spy["close"]))
    vals = [index.get(d) for d in dates]
    if any(v is None for v in vals):
        return None
    return [v / vals[0] for v in vals]


def run_all(price_dir, cost_bps=5.0, names=None):
    data = load_prices(price_dir)
    names = names or [n for n in S.SLEEVE_ORDER if SC.SLEEVE_ENABLED.get(n)]
    results, table = {}, []
    for n in names:
        res = simulate(n, data, cost_bps)
        results[n] = res
        st = stats(res["dates"], res["nav"])
        years = max(len(res["nav"]) / 252.0, 1e-9)
        invested = float(np.mean([g > 0.05 for g in res["gross"]])) if res["gross"] else 0.0
        table.append((n, st, res["turnover"] / years, res["trades"], invested))
    mix = combine(results, SC.SLEEVE_ALLOCATION)
    bench = None
    if mix:
        b = benchmark(data, mix["dates"])
        if b:
            bench = {"dates": mix["dates"], "nav": b}
    return {"results": results, "table": table, "mix": mix, "bench": bench}


def pct(x, d=1):
    return "-" if x is None else f"{x * 100:.{d}f}%"


def format_table(out):
    lines = [f"{'sleeve':<10}{'days':>6}{'total':>9}{'CAGR':>8}{'vol':>8}{'Sharpe':>8}{'maxDD':>9}{'turn/yr':>9}{'trades':>8}{'invested':>10}"]
    for n, st, turn, trades, inv in out["table"]:
        if not st:
            lines.append(f"{n:<10} not enough history")
            continue
        sh = "-" if st["sharpe"] is None else f"{st['sharpe']:.2f}"
        lines.append(f"{n:<10}{st['days']:>6}{pct(st['total']):>9}{pct(st['cagr']):>8}{pct(st['vol']):>8}{sh:>8}"
                     f"{pct(st['max_dd']):>9}{turn:>8.1f}x{trades:>8}{pct(inv, 0):>10}")
    for label, series in (("MIX", out["mix"]), ("SPY", out["bench"])):
        if series:
            st = stats(series["dates"], series["nav"])
            if st:
                sh = "-" if st["sharpe"] is None else f"{st['sharpe']:.2f}"
                lines.append(f"{label:<10}{st['days']:>6}{pct(st['total']):>9}{pct(st['cagr']):>8}{pct(st['vol']):>8}{sh:>8}{pct(st['max_dd']):>9}")
    lines.append("")
    lines.append("MIX = all sleeves at your chosen shares, idle money earning 4%. SPY = buy and hold.")
    lines.append("Limits: survivorship bias (today's big companies), flat trading costs, short history. Treat as a sanity check, not a promise.")
    return "\n".join(lines)


def write_report(out, path):
    text = html.escape(format_table(out))
    with open(path, "w") as f:
        f.write("<!doctype html><html><head><meta charset='utf-8'><title>Backtest</title></head>"
                "<body style='font:14px monospace;background:#0f1115;color:#e5e7eb;padding:16px'>"
                f"<h2>Backtest</h2><pre>{text}</pre></body></html>")
    return path
