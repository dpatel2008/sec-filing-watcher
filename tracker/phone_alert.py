"""
phone_alert.py
Sends a short alert to your phone with the free ntfy app. The secret topic name is saved by
setup_phone.py in tracker_data/phone_alert.json. That file stays on this computer.

Test it any time:   python tracker/phone_alert.py "Hello" "This is a test"
"""

import json
import os
import ssl
import sys
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import config as C  # noqa: E402

SETTINGS_FILE = os.path.join(C.DATA_DIR, "phone_alert.json")
SERVER = "https://ntfy.sh"


def load_topic():
    try:
        with open(SETTINGS_FILE) as f:
            return (json.load(f).get("topic") or "").strip()
    except (OSError, ValueError):
        return ""


def _ascii(text):
    return str(text).encode("ascii", "ignore").decode("ascii")


def _ssl_context():
    """Uses certifi's certificates when it is installed (the Mac's Python often has none of its own)."""
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        return ssl.create_default_context()


def send(title, message, priority="default"):
    """Returns True if the alert was sent. Never raises, so an alert problem cannot stop the tracker."""
    topic = load_topic()
    if not topic:
        return False
    request = urllib.request.Request(
        f"{SERVER}/{topic}", data=str(message).encode("utf-8"), method="POST"
    )
    request.add_header("Title", _ascii(title))
    request.add_header("Priority", priority)
    try:
        with urllib.request.urlopen(request, timeout=15, context=_ssl_context()) as response:
            return 200 <= response.status < 300
    except Exception:
        return False


if __name__ == "__main__":
    title = sys.argv[1] if len(sys.argv) > 1 else "Tracker"
    message = sys.argv[2] if len(sys.argv) > 2 else "Test alert from your tracker."
    print("Alert sent." if send(title, message) else "Alert NOT sent. Run: python tracker/setup_phone.py")
