"""
engine.py
The tracker's brain. Builds the trade plan, places paper trades (stock and option
spreads, long and short), manages exits, rebalances the hedge, and writes the reports. Works with any broker object that
has the same methods as IBBroker.
"""

import csv
import io
import json
import os
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import requests

import analytics as A
import config as C
import dashboard
import risk

ET = ZoneInfo("America/New_York")

PLAN_FIELDS = [
    "date", "symbol", "company", "score", "direction", "tags", "forms", "reasons", "filed",
    "decision", "decision_reason", "flags", "price", "avg_dollar_volume", "atr_pct", "beta",
    "shortable_level", "shortable_shares", "put_call_volume", "qty", "notional", "pct_of_equity",
    "stop", "target", "stop_dist", "target_dist", "exit_by", "risk_dollars",
    "opt_expiry", "opt_dte", "opt_atm_strike", "opt_atm_iv", "implied_move_pct",
    "opt_style", "spread_type", "spread_legs", "spread_debit", "spread_max_profit", "spread_breakeven",
    "spread_delta", "spread_gamma", "spread_vega", "spread_theta",
    "spread_qty", "spread_cost", "spread_status", "filing_url",
]

CLOSED_FIELDS = [
    "trade_id", "symbol", "company", "direction", "tags", "score", "qty", "entry_date",
    "entry_price", "exit_date", "exit_price", "exit_reason", "pnl", "return_pct", "hold_days",
]


# ---------------------------------------------------------------- small helpers

def today_et():
    return datetime.now(ET).date()


def market_is_open(now=None):
    now = now or datetime.now(ET)
    if now.weekday() >= 5:
        return False
    minutes = now.hour * 60 + now.minute
    return 9 * 60 + 30 <= minutes < 16 * 60


def data_path(name):
    return os.path.join(C.DATA_DIR, name)


def read_csv(path):
    try:
        with open(path, newline="") as f:
            return list(csv.DictReader(f))
    except FileNotFoundError:
        return []


def write_csv(path, rows, fieldnames):
    with open(path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def load_open_trades():
    try:
        with open(data_path("open_trades.json")) as f:
            return json.load(f)
    except FileNotFoundError:
        return []


def save_open_trades(trades):
    with open(data_path("open_trades.json"), "w") as f:
        json.dump(trades, f, indent=2)


def load_open_options():
    try:
        with open(data_path("open_options.json")) as f:
            return json.load(f)
    except FileNotFoundError:
        return []


def save_open_options(options):
    with open(data_path("open_options.json"), "w") as f:
        json.dump(options, f, indent=2)


def load_closed_trades():
    trades = []
    for row in read_csv(data_path("closed_trades.csv")):
        trades.append({
            "trade_id": row["trade_id"], "symbol": row["symbol"], "company": row["company"],
            "direction": row["direction"],
            "tags": [t for t in row["tags"].split("; ") if t],
            "score": row["score"], "qty": int(float(row["qty"])),
            "entry_date": row["entry_date"], "entry_price": float(row["entry_price"]),
            "exit_date": row["exit_date"], "exit_price": float(row["exit_price"]),
            "exit_reason": row["exit_reason"], "pnl": float(row["pnl"]),
            "return_pct": float(row["return_pct"]), "hold_days": int(float(row["hold_days"])),
        })
    return trades


def save_closed_trades(trades):
    rows = [dict(t, tags="; ".join(t["tags"])) for t in trades]
    write_csv(data_path("closed_trades.csv"), rows, CLOSED_FIELDS)


def load_signals(local_path=None):
    if local_path:
        with open(local_path, newline="") as f:
            return list(csv.DictReader(f))
    resp = requests.get(C.SIGNALS_URL, timeout=30)
    resp.raise_for_status()
    return list(csv.DictReader(io.StringIO(resp.text)))


class Session:
    """Caches quotes and price history so each symbol is only requested once per run."""

    def __init__(self, broker):
        self.broker = broker
        self._quotes = {}
        self._bars = {}

    def quote(self, symbol):
        if symbol not in self._quotes:
            self._quotes[symbol] = self.broker.snapshot(symbol)
        return self._quotes[symbol]

    def price(self, symbol):
        quote = self.quote(symbol)
        return quote.get("price") if quote else None

    def bars(self, symbol):
        if symbol not in self._bars:
            self._bars[symbol] = self.broker.history(symbol, C.HISTORY_DAYS) or []
        return self._bars[symbol]


# ---------------------------------------------------------------- the trade plan

def build_plan(session, signals, equity, open_symbols, today, open_spread_count=0):
    broker = session.broker
    plan = []
    analyzed = 0
    new_trades = 0
    new_by_side = {"LONG": 0, "SHORT": 0}
    side_caps = {"LONG": C.MAX_NEW_LONGS_PER_RUN, "SHORT": C.MAX_NEW_SHORTS_PER_RUN}
    option_count = 0
    spreads_planned = 0
    min_score = min(C.MIN_SCORE_TO_TRADE, C.MIN_SCORE_TO_TRADE_LONG)
    slots = max(0, C.MAX_OPEN_POSITIONS - len(open_symbols))
    max_new = min(slots, C.MAX_NEW_TRADES_PER_RUN)
    bench_closes = None

    for sig in signals:
        score = int(float(sig.get("score") or 0))
        if score < min_score:
            continue
        symbol = (sig.get("ticker") or "").strip().upper()
        row = {
            "date": today.isoformat(), "symbol": symbol, "company": sig.get("company", ""),
            "score": score, "forms": sig.get("form_types", ""), "reasons": sig.get("reasons", ""),
            "filed": sig.get("date_filed", ""), "filing_url": sig.get("sample_filing_url", ""),
            "direction": "", "tags": "", "decision": "SKIP", "decision_reason": "", "flags": "",
        }

        def skip(reason):
            row["decision"] = "SKIP"
            row["decision_reason"] = reason
            plan.append(row)

        if not symbol:
            skip("no ticker symbol")
            continue
        if analyzed >= C.MAX_CANDIDATES_TO_ANALYZE:
            break

        filed = A.parse_date(row["filed"])
        if filed is None or (today - filed).days > C.MAX_SIGNAL_AGE_DAYS:
            skip("signal is too old")
            continue

        direction, tags, why = A.classify_signal(row["forms"], row["reasons"])
        row["direction"] = direction
        row["tags"] = "; ".join(tags)
        if direction == "SHORT" and score < C.MIN_SCORE_TO_TRADE:
            continue
        if direction == "LONG" and score < C.MIN_SCORE_TO_TRADE_LONG:
            continue
        if direction == "SKIP":
            skip(why)
            continue
        if symbol in open_symbols:
            skip("already holding this name")
            continue

        analyzed += 1
        quote = session.quote(symbol)
        if not quote or not quote.get("price"):
            skip("no quote from IBKR (symbol not found or no data)")
            continue
        price = quote["price"]
        row["price"] = round(price, 4)
        if price < C.MIN_PRICE:
            skip(f"price under ${C.MIN_PRICE:g}")
            continue

        flags = []
        bars = session.bars(symbol)
        avg_shares = None
        adv_dollars = None
        if bars:
            raw = A.avg_volume(bars)
            if raw:
                avg_shares = raw * C.HISTORY_VOLUME_MULTIPLIER
                adv_dollars = avg_shares * price
                row["avg_dollar_volume"] = round(adv_dollars)
        else:
            flags.append("NO_HISTORY")
        atr_value = A.atr(bars) if bars else None
        if atr_value:
            row["atr_pct"] = round(atr_value / price, 4)
        if adv_dollars is not None and adv_dollars < C.MIN_AVG_DOLLAR_VOLUME:
            skip(f"too thinly traded (about ${adv_dollars:,.0f} a day)")
            continue

        level = quote.get("shortable_level")
        available = quote.get("shortable_shares")
        row["shortable_level"] = level if level is not None else ""
        row["shortable_shares"] = available if available is not None else ""
        if direction == "SHORT":
            if A.is_num(level) and level < C.MIN_SHORTABLE_LEVEL:
                skip(f"hard to borrow (IBKR shortable level {level:g})")
                continue
            if not A.is_num(level) and not A.is_num(available):
                if not C.ALLOW_UNKNOWN_SHORTABLE:
                    skip("shortability unknown")
                    continue
                flags.append("SHORTABLE_UNKNOWN")

        if bench_closes is None:
            bench_closes = A.closes_by_date(session.bars(C.HEDGE_SYMBOL))
        beta, _ = A.beta_vs(A.closes_by_date(bars), bench_closes) if bars else (None, 0)
        if beta is None:
            beta = C.DEFAULT_BETA
            flags.append("BETA_DEFAULT")
        beta = A.clamp(beta, 0.0, 3.0)
        row["beta"] = round(beta, 2)

        calls, puts = quote.get("call_volume"), quote.get("put_volume")
        if A.is_num(calls) and calls > 0 and A.is_num(puts):
            row["put_call_volume"] = round(puts / calls, 2)

        sized = A.size_position(direction, price, atr_value, equity, avg_shares)
        qty = sized["qty"]
        if direction == "SHORT" and A.is_num(available):
            qty = min(qty, int(available))
        if qty < 1:
            skip("position size rounds to zero")
            continue
        row.update({
            "qty": qty,
            "notional": round(qty * price, 2),
            "pct_of_equity": round(qty * price / equity, 4),
            "stop": round(sized["stop"], 4),
            "target": round(sized["target"], 4),
            "stop_dist": round(sized["stop_dist"], 4),
            "target_dist": round(sized["target_dist"], 4),
            "exit_by": (today + timedelta(days=C.HOLD_CALENDAR_DAYS)).isoformat(),
            "risk_dollars": round(qty * sized["stop_dist"], 2),
        })

        if new_trades >= max_new:
            decision, why_not = "SKIP", "position or new-trade limit reached"
        elif new_by_side[direction] >= side_caps[direction]:
            decision, why_not = "SKIP", f"all {direction.lower()} slots for this run are used"
        else:
            decision, why_not = "TRADE", "passed every check"

        style = A.option_style(score)
        row["opt_style"] = style
        chain = None
        if decision == "TRADE" or option_count < C.OPTION_MAX_CANDIDATES:
            option_count += 1
            chain = broker.option_chain(symbol, price)
            ideas = A.option_ideas(direction, price, chain, style)
            for key, value in ideas.items():
                row[key] = round(value, 4) if isinstance(value, float) else value
            row["_chain"] = chain

        row["flags"] = "; ".join(flags)
        row["decision"] = decision
        row["decision_reason"] = why_not
        if decision == "TRADE":
            new_trades += 1
            new_by_side[direction] += 1
            if plan_spread(row, direction, equity, chain, open_spread_count + spreads_planned):
                spreads_planned += 1
        plan.append(row)
    return plan


def plan_spread(row, direction, equity, chain, spreads_counted):
    """Decides whether a trade also gets an option (a spread or a plain call/put), and how many.
    Returns True if planned."""
    if not C.OPTIONS_ENABLED:
        row["spread_status"] = "options turned off"
        return False
    if spreads_counted >= C.OPTION_MAX_OPEN:
        row["spread_status"] = "too many options open"
        return False
    style = row.get("opt_style", "spread")
    legs = A.option_legs(direction, chain, style)
    if not legs or not row.get("spread_type"):
        row["spread_status"] = "no usable option prices"
        return False
    long_leg, short_leg, _ = legs
    problem = A.spread_problem(row, long_leg, short_leg)
    if problem:
        row["spread_status"] = problem
        return False
    count = A.size_spread(equity, row["spread_debit"])
    if count < 1:
        row["spread_status"] = "too expensive for the option budget"
        return False
    row["spread_qty"] = count
    row["spread_cost"] = round(count * row["spread_debit"] * 100, 2)
    row["spread_status"] = "planned"
    return True


# ---------------------------------------------------------------- orders

def execute_plan(broker, plan, open_trades, open_options, today, events):
    for row in plan:
        if row["decision"] != "TRADE":
            continue
        action = "SELL" if row["direction"] == "SHORT" else "BUY"
        ref = f"SECW-{row['symbol']}-{today.isoformat()}"
        result = broker.place_market(row["symbol"], action, int(row["qty"]), ref)
        filled = int(result.get("filled") or 0)
        if filled < 1:
            row["decision"] = "NOT FILLED"
            row["decision_reason"] = f"order ended as {result.get('status')}"
            events.append(f"{row['symbol']}: {action} order was not filled ({result.get('status')})")
            continue
        fill = result.get("avg_fill_price") or row["price"]
        sign = -1 if row["direction"] == "SHORT" else 1
        open_trades.append({
            "trade_id": f"{today.isoformat()}-{row['symbol']}",
            "symbol": row["symbol"], "company": row["company"],
            "direction": row["direction"],
            "tags": [t for t in row["tags"].split("; ") if t],
            "score": row["score"], "forms": row["forms"],
            "qty": filled, "entry_date": today.isoformat(), "entry_price": fill,
            "stop": fill - sign * row["stop_dist"],
            "target": fill + sign * row["target_dist"],
            "exit_by": row["exit_by"], "beta": row["beta"],
        })
        save_open_trades(open_trades)
        row["decision"] = "FILLED"
        row["decision_reason"] = f"{action} {filled} at {fill:.2f}"
        events.append(f"{action} {filled} {row['symbol']} at {fill:.2f} ({row['direction']}, score {row['score']})")
        if row.get("spread_status") == "planned":
            execute_spread(broker, row, open_options, today, events)


def execute_spread(broker, row, open_options, today, events):
    symbol = row["symbol"]
    chain = row.get("_chain")
    style = row.get("opt_style", "spread")
    legs = A.option_legs(row["direction"], chain, style)
    if not legs:
        row["spread_status"] = "option prices disappeared"
        return
    long_leg, short_leg, right = legs
    if not long_leg.get("conId") or (short_leg is not None and not short_leg.get("conId")):
        row["spread_status"] = "option contracts not found"
        return
    opt = {
        "trade_id": f"{today.isoformat()}-{symbol}-OPTION",
        "stock_trade_id": f"{today.isoformat()}-{symbol}",
        "style": style,
        "symbol": symbol, "underlying": chain.get("underlying", symbol),
        "company": row["company"], "direction": row["direction"],
        "tags": [t for t in row["tags"].split("; ") if t], "score": row["score"],
        "right": right, "expiry": chain["expiry"],
        "long_strike": long_leg["strike"],
        "short_strike": short_leg["strike"] if short_leg else None,
        "long_conid": long_leg["conId"],
        "short_conid": short_leg["conId"] if short_leg else None,
        "multiplier": chain.get("multiplier", "100"), "trading_class": chain.get("trading_class", ""),
        "entry_date": today.isoformat(),
    }
    debit = long_leg["mid"] - (short_leg["mid"] if short_leg else 0.0)
    limit = debit * (1 + C.OPTION_LIMIT_SLIPPAGE)
    result = broker.place_spread(opt, "BUY", int(row["spread_qty"]), limit, f"SECW-OPT-{symbol}-{today.isoformat()}")
    filled = int(result.get("filled") or 0)
    if filled < 1:
        row["spread_status"] = f"not filled ({result.get('status')})"
        events.append(f"{symbol}: option order was not filled ({result.get('status')})")
        return
    fill = result.get("avg_fill_price") or debit
    if short_leg is not None:
        width = abs(long_leg["strike"] - short_leg["strike"])
        max_profit = max(width - fill, 0.0)
    else:
        max_profit = None
    opt.update({"qty": filled, "entry_debit": fill, "max_profit": max_profit})
    open_options.append(opt)
    save_open_options(open_options)
    row["spread_status"] = f"BOUGHT {filled} at {fill:.2f}"
    word = "put" if right == "P" else "call"
    if short_leg is None:
        what = f"{long_leg['strike']:g} long {word}"
    else:
        what = f"{long_leg['strike']:g}/{short_leg['strike']:g} {word} spread"
    events.append(
        f"BOUGHT {filled} {symbol} {what} exp {chain['expiry']} at {fill:.2f} "
        f"(most it can lose: ${fill * 100 * filled:,.0f})"
    )


def manage_exits(session, open_trades, today):
    broker = session.broker
    held = {p["symbol"]: p for p in broker.positions() if p["sec_type"] == "STK"}
    still_open, closed_now = [], []
    for trade in open_trades:
        symbol = trade["symbol"]
        price = session.price(symbol)
        position = held.get(symbol)
        if position is None or abs(position["qty"]) < 1:
            exit_price = price or trade["entry_price"]
            closed_now.append(A.close_record(trade, exit_price, today, "closed outside tracker", trade["qty"]))
            continue
        if price is None:
            still_open.append(trade)
            continue
        reason = A.exit_reason(trade, price, today)
        if reason is None:
            still_open.append(trade)
            continue
        action = "BUY" if trade["direction"] == "SHORT" else "SELL"
        result = broker.place_market(symbol, action, int(trade["qty"]), f"SECW-EXIT-{symbol}-{today.isoformat()}")
        filled = int(result.get("filled") or 0)
        if filled < 1:
            still_open.append(trade)
            continue
        exit_price = result.get("avg_fill_price") or price
        closed_now.append(A.close_record(trade, exit_price, today, reason, filled))
        if filled < int(trade["qty"]):
            trade["qty"] = int(trade["qty"]) - filled
            still_open.append(trade)
    return still_open, closed_now


def manage_option_exits(session, open_options, open_trades, today, events):
    broker = session.broker
    open_ids = {t["trade_id"] for t in open_trades}
    held_ids = {p.get("con_id") for p in broker.positions() if p["sec_type"] == "OPT"}
    still_open, closed_now = [], []
    for opt in open_options:
        quote = broker.spread_quote(opt)
        mid = quote["mid"] if quote else None
        if opt.get("short_conid"):
            label = f"{opt['symbol']} {opt['long_strike']:g}/{opt['short_strike']:g}{opt['right']} spread"
        else:
            label = f"{opt['symbol']} {opt['long_strike']:g}{opt['right']} option"
        if opt["long_conid"] not in held_ids:
            exit_price = mid if mid is not None else opt["entry_debit"]
            closed_now.append(A.close_option_record(opt, exit_price, today, "closed outside tracker", opt["qty"]))
            events.append(f"{label}: no longer in the account, recorded as closed")
            continue
        reason = A.option_exit_reason(opt, mid, today, opt["stock_trade_id"] in open_ids)
        if reason is None:
            still_open.append(opt)
            continue
        limit = mid * (1 - C.OPTION_LIMIT_SLIPPAGE) if mid is not None else 0.01
        result = broker.place_spread(opt, "SELL", int(opt["qty"]), limit, f"SECW-OPTEXIT-{opt['symbol']}-{today.isoformat()}")
        filled = int(result.get("filled") or 0)
        if filled < 1:
            still_open.append(opt)
            events.append(f"{label}: wanted to close ({reason}) but the order was not filled")
            continue
        exit_price = result.get("avg_fill_price") or mid or 0.0
        record = A.close_option_record(opt, exit_price, today, reason, filled)
        closed_now.append(record)
        events.append(f"Closed {label} ({reason}): P&L ${record['pnl']:,.2f}")
        if filled < int(opt["qty"]):
            opt["qty"] = int(opt["qty"]) - filled
            still_open.append(opt)
    return still_open, closed_now


def rebalance_hedge(session, open_trades, today):
    if not C.HEDGE_ENABLED:
        return None
    broker = session.broker
    hedge_price = session.price(C.HEDGE_SYMBOL)
    if not hedge_price:
        return {"status": "no quote for the hedge symbol, skipped"}
    exposures = []
    for trade in open_trades:
        price = session.price(trade["symbol"]) or trade["entry_price"]
        sign = -1 if trade["direction"] == "SHORT" else 1
        exposures.append({"symbol": trade["symbol"], "signed_notional": sign * price * trade["qty"], "beta": trade["beta"]})
    target = A.hedge_target(exposures, hedge_price)
    held = {p["symbol"]: p for p in broker.positions() if p["sec_type"] == "STK"}
    current = int(held[C.HEDGE_SYMBOL]["qty"]) if C.HEDGE_SYMBOL in held else 0
    delta = target["hedge_shares"] - current
    info = dict(target)
    info.update({
        "symbol": C.HEDGE_SYMBOL, "price": hedge_price, "current_shares": current,
        "m2k_contracts": A.m2k_contracts(target["hedge_dollars"], hedge_price),
        "status": "already balanced", "traded_shares": 0,
    })
    if abs(delta * hedge_price) >= C.MIN_HEDGE_DOLLARS:
        action = "BUY" if delta > 0 else "SELL"
        result = broker.place_market(C.HEDGE_SYMBOL, action, abs(int(delta)), f"SECW-HEDGE-{today.isoformat()}")
        filled = int(result.get("filled") or 0)
        if filled > 0:
            info["traded_shares"] = filled if action == "BUY" else -filled
            info["current_shares"] = current + info["traded_shares"]
            info["status"] = f"{action} {filled} {C.HEDGE_SYMBOL}"
        else:
            info["status"] = f"hedge order ended as {result.get('status')}"
    return info


# ---------------------------------------------------------------- the report

def build_report(session, account_id, today, open_trades, closed_trades, plan, hedge_info, open_options=None, events=None):
    broker = session.broker
    acct = broker.account_values()
    equity = acct.get("NetLiquidation")
    held = {p["symbol"]: p for p in broker.positions() if p["sec_type"] == "STK"}

    option_rows = []
    for opt in open_options or []:
        quote = broker.spread_quote(opt)
        mid = quote["mid"] if quote else None
        mult = float(opt.get("multiplier") or 100)
        qty = int(opt["qty"])
        single = not opt.get("short_conid")
        word = "put" if opt["right"] == "P" else "call"
        if single:
            kind = "Long put" if opt["right"] == "P" else "Long call"
            legs_text = f"Buy {opt['long_strike']:g} {word}"
        else:
            kind = "Bear put spread" if opt["right"] == "P" else "Bull call spread"
            legs_text = f"Buy {opt['long_strike']:g} / Sell {opt['short_strike']:g} {word}"
        max_profit = opt.get("max_profit")
        option_rows.append({
            "symbol": opt["symbol"], "direction": opt["direction"],
            "kind": kind, "legs": legs_text,
            "expiry": opt["expiry"], "qty": qty, "entry_debit": opt["entry_debit"], "mid": mid,
            "pnl": (mid - opt["entry_debit"]) * mult * qty if mid is not None else None,
            "max_loss": opt["entry_debit"] * mult * qty,
            "max_profit": max_profit * mult * qty if max_profit is not None else None,
            "entry_date": opt["entry_date"],
        })

    open_rows, exposures = [], []
    for trade in open_trades:
        price = session.price(trade["symbol"]) or trade["entry_price"]
        sign = -1 if trade["direction"] == "SHORT" else 1
        qty = trade["qty"]
        pnl = sign * (price - trade["entry_price"]) * qty
        notional = sign * price * qty
        entry = A.parse_date(trade["entry_date"])
        open_rows.append({
            "symbol": trade["symbol"], "company": trade["company"], "direction": trade["direction"],
            "tags": "; ".join(trade["tags"]), "score": trade["score"], "qty": qty,
            "entry_price": trade["entry_price"], "price": price, "pnl": pnl,
            "pnl_pct": pnl / (trade["entry_price"] * qty) if qty else 0.0,
            "notional": notional, "stop": trade["stop"], "target": trade["target"],
            "exit_by": trade["exit_by"], "beta": trade["beta"],
            "days_held": (today - entry).days if entry else 0,
        })
        exposures.append({"symbol": trade["symbol"], "signed_notional": notional, "beta": trade["beta"]})

    hedge_exposure = None
    hedge_position = held.get(C.HEDGE_SYMBOL)
    if hedge_position and abs(hedge_position["qty"]) > 0:
        hp = session.price(C.HEDGE_SYMBOL) or hedge_position["market_price"] or 0
        hedge_exposure = {"symbol": C.HEDGE_SYMBOL, "signed_notional": hedge_position["qty"] * hp, "beta": 1.0}

    everything = exposures + ([hedge_exposure] if hedge_exposure else [])
    exposure = {
        "gross": sum(abs(e["signed_notional"]) for e in exposures),
        "net": sum(e["signed_notional"] for e in exposures),
        "net_beta": sum(e["signed_notional"] * e["beta"] for e in exposures),
        "net_beta_after_hedge": sum(e["signed_notional"] * e["beta"] for e in everything),
        "hedge_notional": hedge_exposure["signed_notional"] if hedge_exposure else 0.0,
    }

    histories = {e["symbol"]: A.closes_by_date(session.bars(e["symbol"])) for e in everything}
    risk = A.portfolio_risk([(e["symbol"], e["signed_notional"]) for e in everything], histories) if everything else None
    stress = A.stress_tests(exposures, hedge_exposure)

    curve = []
    for r in read_csv(data_path("equity_curve.csv")):
        if r["date"] != today.isoformat():
            curve.append(r)
    spy = session.price(C.BENCHMARK_SYMBOL)
    if equity:
        curve.append({"date": today.isoformat(), "net_liq": equity, "spy_price": spy if spy else ""})
    curve.sort(key=lambda r: r["date"])
    write_csv(data_path("equity_curve.csv"), curve, ["date", "net_liq", "spy_price"])
    numeric_curve = []
    for r in curve:
        try:
            numeric_curve.append({
                "date": r["date"], "net_liq": float(r["net_liq"]),
                "spy_price": float(r["spy_price"]) if r["spy_price"] not in ("", None) else None,
            })
        except ValueError:
            continue

    spy_adv = None
    spy_bars = session.bars(C.BENCHMARK_SYMBOL)
    if spy_bars and spy:
        raw = A.avg_volume(spy_bars)
        if raw:
            spy_adv = raw * C.HISTORY_VOLUME_MULTIPLIER * spy

    write_csv(data_path("positions_snapshot.csv"), open_rows, [
        "symbol", "company", "direction", "tags", "score", "qty", "entry_price", "price",
        "pnl", "pnl_pct", "notional", "stop", "target", "exit_by", "beta", "days_held",
    ])

    return {
        "account_id": account_id, "generated": datetime.now(ET).strftime("%Y-%m-%d %H:%M ET"),
        "acct": acct, "equity": equity, "plan": plan, "open_rows": open_rows,
        "closed": closed_trades, "hedge_info": hedge_info, "exposure": exposure,
        "risk": risk, "stress": stress, "curve": numeric_curve,
        "equity_stats": A.equity_stats(numeric_curve),
        "alpha_beta": A.alpha_beta(numeric_curve),
        "trade_stats": A.trade_stats(closed_trades),
        "tag_stats": group_stats(closed_trades),
        "option_rows": option_rows,
        "events": events or [],
        "spy_adv": spy_adv,
    }


def group_stats(closed_trades):
    """Results by signal type for stock trades, then by long/short, then the option trades."""
    def is_option(t):
        return "OPTION_SPREAD" in t["tags"] or "OPTION_SINGLE" in t["tags"]

    stock = [t for t in closed_trades if not is_option(t)]
    spreads = [t for t in closed_trades if "OPTION_SPREAD" in t["tags"]]
    singles = [t for t in closed_trades if "OPTION_SINGLE" in t["tags"]]
    stats = A.stats_by_tag(stock)
    for side in ("LONG", "SHORT"):
        subset = [t for t in stock if t["direction"] == side]
        if subset:
            stats[f"{side} stock trades"] = A.trade_stats(subset)
    if spreads:
        stats["Option spreads (all)"] = A.trade_stats(spreads)
    if singles:
        stats["Single calls and puts (all)"] = A.trade_stats(singles)
    return stats


# ---------------------------------------------------------------- the main entry point

def print_plan(plan):
    trades = [r for r in plan if r["decision"] in ("TRADE", "FILLED", "NOT FILLED")]
    print(f"\nTrade plan: {len(trades)} trade(s), {len(plan) - len(trades)} skipped.")
    for r in plan:
        extra = f" x{r['qty']} @ {r['price']}" if r.get("qty") else ""
        spread = f" | option: {r['spread_status']}" if r.get("spread_status") else ""
        print(f"  [{r['decision']}] {r['symbol'] or '-'} {r['direction']}{extra} score {r['score']}: {r['decision_reason']}{spread}")


def run(mode, broker, signals_path=None, force=False, today=None):
    today = today or today_et()
    os.makedirs(C.DATA_DIR, exist_ok=True)
    account = broker.account_id

    if mode == "trade" and not str(account).startswith("DU"):
        raise SystemExit(f"Safety stop: account {account} is not a paper account (paper accounts start with DU).")
    if mode == "trade" and not market_is_open() and not force:
        raise SystemExit("The US stock market is closed right now. Run this between 9:35 AM and 3:45 PM Eastern, or add --force.")

    session = Session(broker)
    equity = broker.account_values().get("NetLiquidation")
    if not equity:
        raise SystemExit("Could not read the account value from IBKR.")
    print(f"Account {account}, net liquidation ${equity:,.0f}")

    open_trades = load_open_trades()
    open_options = load_open_options()
    closed_trades = load_closed_trades()
    hedge_info = None
    events = []

    if mode == "trade":
        open_trades, closed_now = manage_exits(session, open_trades, today)
        for c in closed_now:
            events.append(f"Closed {c['symbol']} ({c['exit_reason']}): P&L ${c['pnl']:,.2f}")
        open_options, closed_options = manage_option_exits(session, open_options, open_trades, today, events)
        closed_trades += closed_now + closed_options
        for line in events:
            print(f"  {line}")
        save_open_trades(open_trades)
        save_open_options(open_options)
        save_closed_trades(closed_trades)

    plan_file = data_path(f"trade_plan_{today.isoformat()}.csv")
    if mode == "report":
        plan = read_csv(plan_file)
    else:
        signals = load_signals(signals_path)
        print(f"Loaded {len(signals)} ranked signal(s).")
        plan = build_plan(session, signals, equity, {t["symbol"] for t in open_trades}, today, len(open_options))
        try:
            guard_notes = risk.apply_entry_guards(plan, session, open_trades, equity, today)
        except Exception as exc:                       # a guard problem must never stop the tracker
            guard_notes = [f"safety checks skipped because of an error: {exc}"]
        for note in guard_notes:
            print(f"  [GUARD] {note}")
            if mode == "trade":
                events.append(f"Guard: {note}")

    if mode == "trade":
        execute_plan(broker, plan, open_trades, open_options, today, events)
        save_open_trades(open_trades)
        save_open_options(open_options)
        hedge_info = rebalance_hedge(session, open_trades, today)
        if hedge_info:
            print(f"Hedge: {hedge_info['status']}")
            if hedge_info["status"] != "already balanced":
                events.append(f"Hedge: {hedge_info['status']}")

    if mode != "report":
        write_csv(plan_file, plan, PLAN_FIELDS)
        print_plan(plan)

    report = build_report(session, account, today, open_trades, closed_trades, plan, hedge_info, open_options, events)
    if report["spy_adv"] is not None:
        print(f"Volume check: {C.BENCHMARK_SYMBOL} average daily dollar volume is ${report['spy_adv']:,.0f} "
              f"(should be in the tens of billions; if it looks 100 times too small or big, change HISTORY_VOLUME_MULTIPLIER).")
    path = data_path("tracker_dashboard.html")
    with open(path, "w") as f:
        f.write(dashboard.render(report))
    print(f"\nDashboard saved to {path}")
    return report, path
