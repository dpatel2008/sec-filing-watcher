"""
risk.py
Safety rules shared by the SEC-filing tracker and the strategy sleeves (Phase 2 "Protect").
Pure math and small helpers. Nothing here places an order.
"""

import csv
import json
import math
import os

import numpy as np

import config as C
import sleeve_config as SC


def _float(x):
    try:
        v = float(x)
        return v if math.isfinite(v) else None
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------- market regime

def market_regime(closes, sma_days):
    """closes: SPY closing prices, oldest first. Returns a dict describing the market's mood.

    state: "unknown", "normal", "weak" (SPY under its average) or "stressed" (under its average AND very jumpy).
    """
    out = {"state": "unknown", "spy": None, "sma": None, "above": None, "vol20": None, "sma_days": sma_days}
    if closes is None or len(closes) < max(sma_days, 22):
        return out
    c = np.asarray(closes, dtype=float)
    avg = float(np.mean(c[-sma_days:]))
    r = np.diff(np.log(c[-21:]))
    vol = float(np.std(r, ddof=1) * math.sqrt(252))
    above = bool(c[-1] > avg)
    out.update({"spy": float(c[-1]), "sma": avg, "above": above, "vol20": vol})
    if above:
        out["state"] = "normal"
    elif vol >= SC.REGIME_STRESSED_VOL:
        out["state"] = "stressed"
    else:
        out["state"] = "weak"
    return out


def regime_scale(regime):
    """How much of its normal size a long-leaning stock sleeve may use."""
    if regime["state"] == "stressed":
        return SC.REGIME_STRESSED_SCALE
    if regime["state"] == "weak":
        return SC.REGIME_BELOW_SMA_SCALE
    return 1.0


# ---------------------------------------------------------------- daily loss limit

def previous_equity(today_iso):
    """Account value from the most recent saved day before today (from the tracker's equity_curve.csv)."""
    path = os.path.join(C.DATA_DIR, "equity_curve.csv")
    best = None
    try:
        with open(path, newline="") as f:
            for row in csv.DictReader(f):
                if row.get("date") and row["date"] < today_iso:
                    value = _float(row.get("net_liq"))
                    if value and (best is None or row["date"] > best[0]):
                        best = (row["date"], value)
    except FileNotFoundError:
        return None
    return best[1] if best else None


def daily_loss_status(equity, prev_equity, limit=None):
    """Returns {"halt": bool, "change": fraction or None}."""
    limit = SC.DAILY_LOSS_LIMIT_PCT if limit is None else limit
    if not equity or not prev_equity:
        return {"halt": False, "change": None}
    change = equity / prev_equity - 1.0
    return {"halt": change <= -limit, "change": change}


# ---------------------------------------------------------------- guards for the SEC-filing tracker

def sleeve_ledger_positions():
    """{sleeve: {symbol: {...}}} from the sleeves' saved ledger, {} if there is no ledger yet,
    or None if the file exists but cannot be read (never raises)."""
    path = os.path.join(C.DATA_DIR, "sleeves_state.json")
    if not os.path.exists(path):
        return {}
    try:
        with open(path) as f:
            return json.load(f).get("positions", {}) or {}
    except (OSError, ValueError, AttributeError):
        return None


def tracker_gross(broker, equity):
    """Dollars the SEC-filing tracker is using now (long + short stock value + option value, without the
    IWM hedge and without anything the sleeves own). None if the account could not be read."""
    ledger = sleeve_ledger_positions()
    if ledger is None:
        return None
    try:
        import allocator
        positions = broker.positions()
        rows, _ = allocator.capital_rows(equity, positions, ledger, {})
    except Exception:
        return None
    for r in rows:
        if r["key"] == "tracker":
            return r["gross"]
    return 0.0


def apply_entry_guards(plan, session, open_trades, equity, today):
    """Turns planned tracker trades into skips when a safety rule says so. Returns a list of messages."""
    notes = []
    if not SC.TRACKER_GUARDS_ENABLED:
        return notes
    planned = [r for r in plan if r.get("decision") == "TRADE"]

    def block(row, reason):
        row["decision"] = "SKIP"
        row["decision_reason"] = reason
        if row.get("spread_status") == "planned":
            row["spread_status"] = "not placed"

    if planned and SC.TRACKER_DAILY_LOSS_LIMIT:
        status = daily_loss_status(equity, previous_equity(today.isoformat()))
        if status["halt"]:
            for row in planned:
                block(row, f"daily loss limit: account is {status['change'] * 100:.1f}% since the last saved day")
            notes.append(f"Daily loss limit hit ({status['change'] * 100:.1f}%): no new tracker trades today.")
            planned = []

    if planned and SC.TRACKER_BLOCK_LONGS_WHEN_STRESSED:
        bars = session.bars(C.BENCHMARK_SYMBOL)
        regime = market_regime([b["close"] for b in bars], SC.TRACKER_REGIME_SMA_DAYS) if bars else None
        if regime and regime["state"] == "stressed":
            for row in planned:
                if row.get("direction") == "LONG":
                    block(row, "market regime: SPY is weak and very volatile, no new buys")
                    notes.append(f"{row['symbol']}: buy skipped because the market is stressed.")
            planned = [r for r in planned if r.get("decision") == "TRADE"]

    if planned and SC.MAX_PER_INDUSTRY:
        from broker_extra import sector as sector_of
        counts = {}
        for trade in open_trades:
            industry = sector_of(session.broker, trade["symbol"])
            if industry:
                counts[industry] = counts.get(industry, 0) + 1
        for row in planned:
            industry = sector_of(session.broker, row["symbol"])
            if not industry:
                continue
            if counts.get(industry, 0) >= SC.MAX_PER_INDUSTRY:
                block(row, f"industry limit: already {counts[industry]} trades in {industry}")
                notes.append(f"{row['symbol']}: skipped, too many trades in {industry}.")
            else:
                counts[industry] = counts.get(industry, 0) + 1
        planned = [r for r in planned if r.get("decision") == "TRADE"]

    if planned and SC.TRACKER_MAX_GROSS_PCT:
        used = tracker_gross(session.broker, equity)
        budget = SC.TRACKER_MAX_GROSS_PCT * equity
        if used is not None:
            for row in planned:
                size = _float(row.get("notional")) or 0.0
                if used + size > budget:
                    block(row, f"tracker capital budget full: using ${used:,.0f} of ${budget:,.0f} "
                               f"({SC.TRACKER_MAX_GROSS_PCT * 100:.0f}% of the account)")
                    notes.append(f"{row['symbol']}: skipped, the tracker's capital budget is full.")
                else:
                    used += size
    return notes
