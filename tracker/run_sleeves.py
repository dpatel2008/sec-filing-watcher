"""
run_sleeves.py
Usage (from the project folder, with the virtual environment on):

  python tracker/run_sleeves.py plan       look only: shows what each sleeve would do, places no orders
  python tracker/run_sleeves.py trade      places paper orders for the sleeves
  python tracker/run_sleeves.py report     refreshes the sleeves page from saved data
  python tracker/run_sleeves.py prefetch   downloads price history from IBKR (do this once, takes 20-30 minutes)
  python tracker/run_sleeves.py backtest   tests every sleeve on the saved history (no IBKR needed)
  python tracker/run_sleeves.py publish    builds results.json and publishes it to GitHub
  python tracker/run_sleeves.py capital    shows how much money each strategy is using (places no orders)

Extra options:  --force       allow trading when the market looks closed
                --no-open     do not open the page in the browser
                --years N     prefetch: how many years of history (default 3; use 8 for a better backtest)
                --cost-bps N  backtest: trading cost in basis points per dollar traded (default 5)
"""

import argparse
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import config as C  # noqa: E402
import sleeve_config as SC  # noqa: E402
import sleeve_strategies as S  # noqa: E402


def open_file(path):
    try:
        subprocess.run(["open", path], check=False)
    except OSError:
        pass


def prefetch(adapter, years):
    import sleeve_engine as E
    if years:
        SC.PRICE_YEARS = years
    symbols = S.all_symbols(extra=[SC.CASH_SYMBOL, SC.CASH_FALLBACK_SYMBOL, "SPY"])
    store = E.PriceStore(adapter, E.today_et().isoformat())
    if years:
        for s in symbols:
            try:
                os.remove(store._file(s))
            except OSError:
                pass
    print(f"Loading price history for {len(symbols)} symbols ({SC.PRICE_YEARS} years each).")
    print("IBKR only allows about 60 history requests every 10 minutes, so this can take 20-30 minutes.")
    print("You can leave it running. It saves as it goes, so it is safe to stop and run it again.\n")
    ok, missing = 0, []
    for i, s in enumerate(symbols, 1):
        data = store.get(s)
        if data:
            ok += 1
            print(f"  {i}/{len(symbols)} {s}: {len(data['close'])} days")
        else:
            missing.append(s)
            print(f"  {i}/{len(symbols)} {s}: no data")
    print(f"\nDone. {ok} symbols ready.")
    if missing:
        print("No data for:", ", ".join(missing), "(the sleeves just skip these)")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=["plan", "trade", "report", "prefetch", "backtest", "publish", "capital"])
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--no-open", action="store_true")
    parser.add_argument("--years", type=int, default=None)
    parser.add_argument("--cost-bps", type=float, default=5.0)
    args = parser.parse_args()

    if args.mode == "backtest":
        import backtest
        out = backtest.run_all(os.path.join(C.DATA_DIR, "prices"), args.cost_bps)
        if not out["table"] or not out["results"]:
            raise SystemExit("No price history found. Run: python tracker/run_sleeves.py prefetch")
        print(backtest.format_table(out))
        path = backtest.write_report(out, os.path.join(C.DATA_DIR, "sleeves_backtest.html"))
        print(f"\nSaved: {path}")
        if not args.no_open:
            open_file(path)
        return

    if args.mode == "publish":
        import publish_results
        ok, message = publish_results.publish()
        print(message)
        return

    from broker_extra import IBAdapter
    from broker_ib import IBBroker
    C.IB_CLIENT_ID = SC.MANUAL_CLIENT_ID     # a different connection number, so a manual run never blocks the scheduled one
    broker = IBBroker()
    broker.connect()
    try:
        adapter = IBAdapter(broker)
        if args.mode == "prefetch":
            prefetch(adapter, args.years)
            return
        if args.mode == "capital":
            import allocator
            import sleeve_engine as E
            rows, totals, state = allocator.report(adapter, E.today_et().isoformat())
            print(allocator.capital_text(rows, totals, state))
            path = os.path.join(C.DATA_DIR, "capital.html")
            with open(path, "w") as f:
                f.write(allocator.capital_page(rows, totals, state))
            print(f"\nSaved: {path}")
            if not args.no_open:
                open_file(path)
            return
        import sleeve_engine as E
        _, path = E.run(args.mode, adapter, force=args.force)
    finally:
        broker.disconnect()
    if not args.no_open:
        open_file(path)


if __name__ == "__main__":
    main()
