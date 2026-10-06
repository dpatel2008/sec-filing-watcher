"""
auto_run.py
The hands-free daily run. The schedule (install_schedule.py) starts this a few times each
weekday morning. It checks that the market is open and IB Gateway is logged in, runs the
tracker in trade mode (paper account only), and emails you a summary.

Test it any time, with no orders placed:   python tracker/auto_run.py --plan-only
"""

import os
import sys
import traceback
from collections import Counter
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import config as C  # noqa: E402
import engine  # noqa: E402
import notify  # noqa: E402
from broker_ib import IBBroker  # noqa: E402

WINDOW_START = 9 * 60 + 35     # 9:35 AM Eastern
WINDOW_END = 15 * 60 + 45      # 3:45 PM Eastern
LAST_TRY = 10 * 60 + 55        # the last scheduled try; only then do we email "could not run"


def log(text):
    os.makedirs(C.DATA_DIR, exist_ok=True)
    line = f"{datetime.now(engine.ET).strftime('%Y-%m-%d %H:%M:%S')} {text}"
    print(line)
    with open(engine.data_path("auto_run.log"), "a") as f:
        f.write(line + "\n")


def summarize(report, mode):
    plan = report["plan"]
    new_trades = sum(1 for r in plan if r.get("decision") == "FILLED")
    closed_count = sum(1 for line in report["events"] if line.startswith("Closed"))
    stats = report["trade_stats"]
    open_pnl = sum(o["pnl"] for o in report["open_rows"])
    option_pnl = sum(o["pnl"] for o in report["option_rows"] if o["pnl"] is not None)

    lines = []
    if mode == "plan":
        lines.append("PLAN ONLY. No orders were placed.")
        lines.append("")
    lines.append("WHAT HAPPENED")
    if report["events"]:
        lines += [f"  - {e}" for e in report["events"]]
    else:
        lines.append("  - No orders were placed.")

    planned = [r for r in plan if r.get("decision") in ("TRADE", "FILLED", "NOT FILLED")]
    skipped = [r for r in plan if r.get("decision") == "SKIP"]
    lines += ["", f"PLAN: {len(planned)} trade(s), {len(skipped)} skipped"]
    reasons = Counter(r.get("decision_reason", "") for r in skipped)
    for reason, count in reasons.most_common(4):
        lines.append(f"  - {count} skipped: {reason}")

    lines += ["", "OPEN STOCK TRADES"]
    if report["open_rows"]:
        for o in report["open_rows"]:
            lines.append(
                f"  - {o['symbol']} {o['direction']} {o['qty']} sh, in at {o['entry_price']:.2f}, "
                f"now {o['price']:.2f}, P&L ${o['pnl']:,.0f}, exit by {o['exit_by']}"
            )
    else:
        lines.append("  - none")

    lines += ["", "OPEN OPTION SPREADS"]
    if report["option_rows"]:
        for o in report["option_rows"]:
            pnl = f"${o['pnl']:,.0f}" if o["pnl"] is not None else "no price"
            lines.append(f"  - {o['symbol']} {o['legs']} exp {o['expiry']} x{o['qty']}, P&L {pnl}")
    else:
        lines.append("  - none")

    expo = report["exposure"]
    lines += [
        "",
        "ACCOUNT",
        f"  Account value: ${report['equity']:,.0f}",
        f"  Open P&L: stocks ${open_pnl:,.0f}, options ${option_pnl:,.0f}",
        f"  Closed P&L so far: ${stats['total_pnl']:,.0f} over {stats['n']} trade(s)"
        + (f", hit rate {stats['hit_rate'] * 100:.0f}%" if stats["hit_rate"] is not None else ""),
        f"  Gross exposure ${expo['gross']:,.0f}, net ${expo['net']:,.0f}, "
        f"beta-adjusted net after hedge ${expo['net_beta_after_hedge']:,.0f}",
        "",
        "The full dashboard is attached. Open it in a browser.",
        "Paper account only. Results use delayed quotes and ignore commissions and slippage.",
    ]
    subject = (
        f"Tracker {report['generated'][:10]}: {new_trades} new trade(s), {closed_count} closed, "
        f"account ${report['equity']:,.0f}"
    )
    if mode == "plan":
        subject += " (plan only)"
    return subject, "\n".join(lines)


def main():
    plan_only = "--plan-only" in sys.argv
    mode = "plan" if plan_only else "trade"
    now = datetime.now(engine.ET)
    minutes = now.hour * 60 + now.minute
    today = now.date()
    marker = engine.data_path(f"auto_done_{today.isoformat()}.txt")
    os.makedirs(C.DATA_DIR, exist_ok=True)

    if not plan_only:
        if now.weekday() >= 5:
            log("Weekend, nothing to do.")
            return
        if not (WINDOW_START <= minutes <= WINDOW_END):
            log("Outside the 9:35 AM to 3:45 PM Eastern window, nothing to do.")
            return
        if os.path.exists(marker):
            log("Already ran today, nothing to do.")
            return

    broker = IBBroker()
    try:
        broker.connect()
    except SystemExit:
        log("IB Gateway is not running or not logged in to the paper account.")
        if not plan_only and minutes >= LAST_TRY:
            notify.send_email(
                f"Tracker {today.isoformat()}: did NOT run",
                "The tracker could not connect to IB Gateway, so no trades were placed today.\n\n"
                "Open IB Gateway, choose IB API and Paper Trading, log in, then double-click "
                "RunTracker.command on your Desktop to run it by hand.",
            )
        elif plan_only:
            print("Open IB Gateway, log in to the paper account, then try again.")
        return

    try:
        report, path = engine.run(mode, broker)
        subject, body = summarize(report, mode)
        sent = notify.send_email(subject, body, [path])
        log(f"Finished ({mode}). Email sent: {sent}. {subject}")
    except SystemExit as e:
        log(f"Stopped: {e}")
        if not plan_only:
            notify.send_email(f"Tracker {today.isoformat()}: stopped", f"The tracker stopped with this message:\n\n{e}")
    except Exception:
        details = traceback.format_exc()
        log("ERROR:\n" + details)
        if not plan_only:
            notify.send_email(
                f"Tracker {today.isoformat()}: ERROR",
                "The tracker hit an error. Some orders may have been placed before it stopped, so open "
                "the dashboard before running anything again.\n\n" + details[-1500:],
            )
    finally:
        broker.disconnect()
        if not plan_only:
            with open(marker, "w") as f:
                f.write("done\n")


if __name__ == "__main__":
    main()
