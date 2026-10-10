"""
setup_phone.py
Run once:  python tracker/setup_phone.py
Makes a secret topic name, saves it on this computer, and sends a test alert to your phone.
"""

import json
import os
import secrets
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import config as C  # noqa: E402
import phone_alert  # noqa: E402


def main():
    os.makedirs(C.DATA_DIR, exist_ok=True)
    topic = phone_alert.load_topic()
    if not topic:
        topic = "dillen-tracker-" + secrets.token_hex(8)
        with open(phone_alert.SETTINGS_FILE, "w") as f:
            json.dump({"topic": topic}, f)
        os.chmod(phone_alert.SETTINGS_FILE, 0o600)

    print()
    print("Your secret topic name is:")
    print()
    print("    " + topic)
    print()
    print("On your phone: install the free app called ntfy, tap the + button,")
    print("and type that topic name exactly. Keep it private. Anyone who knows it can read your alerts.")
    print()
    input("Press Enter here when your phone is subscribed, and I will send a test alert: ")
    if phone_alert.send("Tracker test", "If you can read this, phone alerts work.", "high"):
        print("Test alert sent. Check your phone.")
    else:
        print("The alert did not go through. Check the Mac's internet and the topic name, then run this again.")


if __name__ == "__main__":
    main()
