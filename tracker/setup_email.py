"""
setup_email.py
Run once:  python tracker/setup_email.py
Saves your Gmail address and a Gmail "app password" on this computer, then sends a test email.
"""

import getpass
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import config as C  # noqa: E402
import notify  # noqa: E402

DEFAULT_ADDRESS = "dillenpatel2008@gmail.com"


def main():
    print("This saves your email settings on this computer only.")
    username = input(f"Your Gmail address [{DEFAULT_ADDRESS}]: ").strip() or DEFAULT_ADDRESS
    print("Paste your 16-letter Gmail app password. You will not see it as you type. Then press Enter.")
    password = getpass.getpass("App password: ").replace(" ", "").strip()
    if not password:
        raise SystemExit("No password entered. Run this again.")
    to = input(f"Send the summaries to [{username}]: ").strip() or username

    os.makedirs(C.DATA_DIR, exist_ok=True)
    with open(notify.SETTINGS_FILE, "w") as f:
        json.dump({"username": username, "password": password, "to": to}, f)
    os.chmod(notify.SETTINGS_FILE, 0o600)

    print("Sending a test email...")
    ok = notify.send_email(
        "Tracker test email",
        "It works. Your tracker will email you a summary after each automatic run.",
    )
    if ok:
        print(f"Done. Check the inbox of {to}.")
    else:
        print("The test email failed. Check the app password (16 letters, no spaces) and run this again.")


if __name__ == "__main__":
    main()
