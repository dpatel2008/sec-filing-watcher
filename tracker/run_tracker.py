"""
run_tracker.py
Usage (from the project folder, with the virtual environment on):

  python tracker/run_tracker.py plan      look only: builds the plan and dashboard, places no orders
  python tracker/run_tracker.py trade     places paper orders, manages exits, rebalances the hedge
  python tracker/run_tracker.py report    refreshes the dashboard from the paper account

Extra options:  --signals FILE   use a local CSV instead of the GitHub link
                --force          allow trading when the market looks closed
                --no-open        do not open the dashboard in the browser
"""

import argparse
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import engine  # noqa: E402
from broker_ib import IBBroker  # noqa: E402


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=["plan", "trade", "report"])
    parser.add_argument("--signals", default=None)
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--no-open", action="store_true")
    args = parser.parse_args()

    broker = IBBroker()
    broker.connect()
    try:
        _, path = engine.run(args.mode, broker, signals_path=args.signals, force=args.force)
    finally:
        broker.disconnect()

    if not args.no_open:
        try:
            subprocess.run(["open", path], check=False)
        except OSError:
            pass


if __name__ == "__main__":
    main()
