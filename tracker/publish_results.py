"""
publish_results.py
Builds results.json (account value, positions, closed trades, sleeves) and publishes it to your GitHub
repository so a Lovable page can show it. It contains your PAPER account results only, never the
account number, and the repository is public, so anyone with the link can read it.

  python tracker/publish_results.py            build results.json, then publish it to GitHub
  python tracker/publish_results.py --no-push  only build the file
"""

import csv
import json
import math
import os
import subprocess
import sys
from datetime import datetime
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import config as C  # noqa: E402

ET = ZoneInfo("America/New_York")
PROJECT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RESULTS_FILE = "results.json"


def _num(x):
    try:
        v = float(x)
        return v if math.isfinite(v) else None
    except (TypeError, ValueError):
        return None


def _rows(name):
    try:
        with open(os.path.join(C.DATA_DIR, name), newline="") as f:
            return list(csv.DictReader(f))
    except FileNotFoundError:
        return []


def build():
    curve = []
    for r in _rows("equity_curve.csv"):
        equity = _num(r.get("net_liq"))
        if equity:
            curve.append({"date": r["date"], "equity": round(equity, 2), "spy": _num(r.get("spy_price"))})
    account = {"equity": None, "start_equity": None, "return_pct": None, "spy_return_pct": None, "since": None}
    if curve:
        account["equity"] = curve[-1]["equity"]
        account["start_equity"] = curve[0]["equity"]
        account["since"] = curve[0]["date"]
        account["return_pct"] = round(curve[-1]["equity"] / curve[0]["equity"] - 1, 5)
        spy = [p["spy"] for p in curve if p["spy"]]
        if len(spy) >= 2:
            account["spy_return_pct"] = round(spy[-1] / spy[0] - 1, 5)

    positions = []
    for r in _rows("positions_snapshot.csv"):
        positions.append({
            "symbol": r["symbol"], "side": r["direction"], "qty": _num(r["qty"]),
            "entry_price": _num(r["entry_price"]), "price": _num(r["price"]), "pnl": _num(r["pnl"]),
            "pnl_pct": _num(r["pnl_pct"]), "exit_by": r["exit_by"], "signals": r["tags"],
        })
    closed = []
    for r in _rows("closed_trades.csv"):
        closed.append({
            "symbol": r["symbol"], "side": r["direction"], "entry_date": r["entry_date"], "exit_date": r["exit_date"],
            "pnl": _num(r["pnl"]), "return_pct": _num(r["return_pct"]), "reason": r["exit_reason"],
        })
    closed_pnl = sum(c["pnl"] or 0.0 for c in closed)
    wins = sum(1 for c in closed if (c["pnl"] or 0) > 0)

    sleeves, regime, capital, capital_as_of = [], None, [], None
    try:
        with open(os.path.join(C.DATA_DIR, "sleeves_report.json")) as f:
            rep = json.load(f)
        regime = rep.get("regime")
        capital_as_of = rep.get("generated")
        for r in ((rep.get("capital") or {}).get("rows") or []):
            capital.append({"strategy": r["name"], "budget_pct": r.get("budget_pct"), "using": round(r["gross"], 2),
                            "using_pct": round(r["gross_pct"], 4), "open_pnl": round(r.get("pnl") or 0.0, 2)})
        for s in rep.get("sleeves", []):
            sleeves.append({
                "name": s["name"], "value": round(s["market_value"], 2), "realized": round(s["realized"], 2),
                "unrealized": round(s["unrealized"], 2),
                "positions": [{"symbol": p["symbol"], "qty": p["qty"], "price": round(p["price"], 2),
                               "value": round(p["value"], 2), "pnl": round(p["pnl"], 2)} for p in s["positions"]],
            })
    except (FileNotFoundError, ValueError, KeyError):
        pass

    return {
        "updated": datetime.now(ET).strftime("%Y-%m-%d %H:%M ET"),
        "note": "Paper trading results. Not real money. Not investment advice.",
        "account": account,
        "curve": curve,
        "tracker": {
            "open_positions": positions,
            "closed_trades": closed[-100:],
            "closed_count": len(closed),
            "closed_pnl": round(closed_pnl, 2),
            "hit_rate": round(wins / len(closed), 4) if closed else None,
        },
        "sleeves": sleeves,
        "capital": capital,
        "capital_as_of": capital_as_of,
        "regime": regime,
    }


def write_file():
    path = os.path.join(PROJECT, RESULTS_FILE)
    with open(path, "w") as f:
        json.dump(build(), f, indent=1)
    return path


def _git(*args):
    try:
        return subprocess.run(["git", *args], cwd=PROJECT, capture_output=True, text=True, timeout=60,
                              env=dict(os.environ, GIT_TERMINAL_PROMPT="0"))
    except subprocess.TimeoutExpired:
        return subprocess.CompletedProcess(["git", *args], 1, "", "git took too long (is a sign-in needed?)")


def push(path):
    """Commits and pushes results.json. Returns (ok, message)."""
    if _git("rev-parse", "--is-inside-work-tree").returncode != 0:
        return False, "This folder is not a git repository."
    identity = []
    if not _git("config", "user.name").stdout.strip():
        identity = ["-c", "user.name=SEC Tracker", "-c", "user.email=tracker@users.noreply.github.com"]
    pulled = _git("pull", "--rebase", "--autostash")
    if pulled.returncode != 0:
        return False, "Could not update from GitHub first: " + (pulled.stderr or pulled.stdout).strip()[:300]
    _git("add", RESULTS_FILE)
    if _git("diff", "--cached", "--quiet").returncode == 0:
        return True, "results.json has not changed."
    commit = _git(*identity, "commit", "-m", "Update results", "--", RESULTS_FILE)
    if commit.returncode != 0:
        return False, "Could not save the commit: " + (commit.stderr or commit.stdout).strip()[:300]
    sent = _git("push")
    if sent.returncode != 0:
        return False, "Could not push to GitHub (sign-in needed?): " + (sent.stderr or sent.stdout).strip()[:300]
    return True, "Published results.json to GitHub."


def publish(do_push=True):
    path = write_file()
    if not do_push:
        return True, f"Wrote {path}"
    return push(path)


if __name__ == "__main__":
    ok, message = publish("--no-push" not in sys.argv)
    print(message)
    sys.exit(0 if ok else 1)
