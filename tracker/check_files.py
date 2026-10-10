"""
check_files.py
Checks that every file you pasted into GitHub matches the tested version exactly.

  python tracker/check_files.py
"""

import hashlib
import os

FOLDER = os.path.dirname(os.path.abspath(__file__))
EXPECTED = {
    "phone_alert.py": "728231557b4749f9",
    "setup_phone.py": "555a1e59c2f9bd1f",
    "make_icons.py": "004f3c585c28083d",
    "auto_run.py": "d95151b9ce490a8d",
    "start_gateway.py": "1e8ee5f294845e83",
    "ibc_setup.py": "ebf67a375dea2fb1",
    "sleeve_config.py": "d6989ae0dc02422a",
    "sleeve_strategies.py": "9c4448dcdc2b641f",
    "risk.py": "09d4d81ed0dd3154",
    "broker_extra.py": "8a988778ae707468",
    "allocator.py": "8db85614a251c913",
    "sleeve_engine.py": "977b9c5cfbefc0b5",
    "backtest.py": "177b9d6ed00aeab2",
    "run_sleeves.py": "1aa6124cf88dbc47",
    "publish_results.py": "695db6ee2f57a8c6",
    "engine.py": "db8886c4a8718ca5",
}


def fingerprint(text):
    lines = [line.rstrip() for line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n")]
    return hashlib.sha256("\n".join(lines).strip("\n").encode()).hexdigest()[:16]


def main():
    bad = 0
    for name, want in EXPECTED.items():
        path = os.path.join(FOLDER, name)
        if not os.path.exists(path):
            print(f"MISSING    {name}")
            bad += 1
            continue
        with open(path, encoding="utf-8", errors="replace") as f:
            got = fingerprint(f.read())
        if got == want:
            print(f"OK         {name}")
        else:
            print(f"DIFFERENT  {name}   <- paste this file again")
            bad += 1
    print()
    print("Everything matches." if bad == 0 else f"{bad} file(s) need attention. Send me a screenshot.")


if __name__ == "__main__":
    main()
