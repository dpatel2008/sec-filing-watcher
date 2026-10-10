"""
ibc_setup.py
Sets up automatic IB Gateway login (paper account) using the free IBC tool.

  python tracker/ibc_setup.py            one-time setup (asks for your PAPER username and password)
  python tracker/ibc_setup.py schedule   open and log in IB Gateway at 9:31 AM, Monday to Friday
  python tracker/ibc_setup.py remove     turn that schedule off

You still approve each login with a tap on your phone (IBKR requires it). Your password is saved
only in a file on this Mac, readable only by you. Use it ONLY with a paper-trading login.
"""

import getpass
import glob
import os
import plistlib
import re
import stat
import subprocess
import sys

PROJECT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PYTHON = os.path.abspath(sys.executable)
HOME = os.path.expanduser("~")
IBC_ROOT = os.path.join(HOME, "ibc")
LABEL = "com.dillen.startgateway"
PLIST = os.path.join(HOME, "Library", "LaunchAgents", f"{LABEL}.plist")
DOMAIN = f"gui/{os.getuid()}"


def find_ibc_dir():
    for folder in [IBC_ROOT] + sorted(glob.glob(os.path.join(IBC_ROOT, "*"))):
        if os.path.exists(os.path.join(folder, "gatewaystartmacos.sh")):
            return folder
    return None


def find_gateway():
    """Returns (version like '10.51', parent folder) for the newest IB Gateway.
    On a Mac, IBC wants the version WITH the dot, because the install folder is named 'IB Gateway 10.51'."""
    best = None
    for apps in (os.path.join(HOME, "Applications"), "/Applications"):
        for folder in glob.glob(os.path.join(apps, "IB Gateway *")):
            match = re.fullmatch(r"IB Gateway (\d+)\.(\d+)", os.path.basename(folder))
            if match and os.path.isdir(folder):
                version = (int(match.group(1)), int(match.group(2)))
                if best is None or version > best[0]:
                    best = (version, apps)
    if best:
        return f"{best[0][0]}.{best[0][1]}", best[1]
    return None, None


def set_value(text, key, value):
    """Sets KEY=value in an ini or shell file, replacing the line or adding it at the end."""
    pattern = re.compile(rf"^[ \t]*{re.escape(key)}[ \t]*=.*$", re.MULTILINE)
    line = f"{key}={value}"
    if pattern.search(text):
        return pattern.sub(lambda _m: line, text, count=1), True
    return text.rstrip("\n") + "\n" + line + "\n", False


def run(*args):
    try:
        return subprocess.run(args, capture_output=True, text=True)
    except OSError:
        return subprocess.CompletedProcess(args, 1, "", "command not found")


def setup():
    ibc = find_ibc_dir()
    if not ibc:
        print("I cannot find the IBC folder. Download IBC for Mac, unzip it into a folder named")
        print("ibc in your home folder, then run this again. (See the steps I gave you.)")
        return
    version, tws_path = find_gateway()
    if not version:
        version = input("I could not find IB Gateway. Type its version number WITH the dot (for example 10.51): ").strip()
        tws_path = os.path.join(HOME, "Applications")
    print(f"IBC folder: {ibc}")
    print(f"IB Gateway version: {version} in {tws_path}")
    if not os.path.isdir(os.path.join(tws_path, f"IB Gateway {version}", "jars")):
        print()
        print(f"STOP: there is no 'jars' folder in {os.path.join(tws_path, 'IB Gateway ' + version)}.")
        print("Either the version number is wrong, or this IB Gateway is the self-updating kind (IBC only works")
        print("with the OFFLINE kind). Download the OFFLINE 'IB Gateway Stable' for Mac from IBKR's website,")
        print("install it, then run this again. Nothing was changed.")
        return
    print()
    print("Use your PAPER trading login only.")
    username = input("IBKR paper username: ").strip()
    password = getpass.getpass("IBKR paper password (nothing shows as you type): ")
    if not username or not password:
        print("Username and password are both needed. Run this again.")
        return

    run("xattr", "-dr", "com.apple.quarantine", ibc)

    # IBC settings file
    ini_path = os.path.join(ibc, "config.ini")
    with open(ini_path) as f:
        ini = f.read()
    settings = {
        "IbLoginId": username,
        "IbPassword": password,
        "TradingMode": "paper",
        "ExistingSessionDetectedAction": "primary",
        "AcceptNonBrokerageAccountWarning": "yes",
        "AcceptIncomingConnectionAction": "accept",
        "ReadOnlyApi": "no",
        "OverrideTwsApiPort": "4002",
        "ReloginAfterSecondFactorAuthenticationTimeout": "yes",
        "MinimizeMainWindow": "yes",
    }
    for key, value in settings.items():
        ini, _ = set_value(ini, key, value)
    paper_ini = os.path.join(ibc, "config_paper.ini")
    with open(paper_ini, "w") as f:
        f.write(ini)
    os.chmod(paper_ini, stat.S_IRUSR | stat.S_IWUSR)

    # IBC start script for the Gateway
    with open(os.path.join(ibc, "gatewaystartmacos.sh")) as f:
        start = f.read()
    log_dir = os.path.join(ibc, "logs")
    os.makedirs(log_dir, exist_ok=True)
    values = {
        "TWS_MAJOR_VRSN": version,
        "IBC_INI": paper_ini,
        "TRADING_MODE": "paper",
        "TWOFA_TIMEOUT_ACTION": "restart",      # if the phone approval times out, try the login again
        "IBC_PATH": ibc,
        "TWS_PATH": tws_path,
        "LOG_PATH": log_dir,
    }
    jts = os.path.join(HOME, "Jts")             # where IB Gateway keeps its settings on a Mac
    os.makedirs(jts, exist_ok=True)
    values["TWS_SETTINGS_PATH"] = jts
    missing = []
    for key, value in values.items():
        start, found = set_value(start, key, value)
        if not found:
            missing.append(key)
    start_path = os.path.join(IBC_ROOT, "start_gateway_paper.sh")
    with open(start_path, "w") as f:
        f.write(start)
    for path in [start_path] + glob.glob(os.path.join(ibc, "*.sh")) + glob.glob(os.path.join(ibc, "scripts", "*.sh")):
        os.chmod(path, os.stat(path).st_mode | stat.S_IXUSR)

    # Desktop icon
    icon = os.path.join(HOME, "Desktop", "Start Gateway.command")
    os.makedirs(os.path.dirname(icon), exist_ok=True)
    with open(icon, "w") as f:
        f.write(f'#!/bin/bash\ncd "{PROJECT}" || exit 1\n"{PYTHON}" tracker/start_gateway.py\n')
    os.chmod(icon, os.stat(icon).st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)

    print()
    print("Done. Settings saved. Made the Desktop icon: Start Gateway.command")
    if missing:
        print("Note: these lines were not in IBC's start file, so I added them at the bottom:", ", ".join(missing))
        print("If the start does not work, send me a screenshot of this message.")
    print("Test it: quit IB Gateway, then double-click Start Gateway.command and approve on your phone.")
    print("Note: after IBC starts IB Gateway once, it renames the normal IB Gateway app so IBKR cannot restart it")
    print("without IBC. From then on, open IB Gateway with the Start Gateway icon only.")


def schedule(action):
    run("launchctl", "bootout", f"{DOMAIN}/{LABEL}")
    if action == "remove":
        if os.path.exists(PLIST):
            os.remove(PLIST)
        print("The automatic IB Gateway start is turned off.")
        return
    plist = {
        "Label": LABEL,
        "ProgramArguments": [PYTHON, os.path.join(PROJECT, "tracker", "start_gateway.py")],
        "WorkingDirectory": PROJECT,
        "StartCalendarInterval": [{"Weekday": day, "Hour": 9, "Minute": 31} for day in range(1, 6)],
        "AbandonProcessGroup": True,
        "StandardOutPath": os.path.join(PROJECT, "tracker_data", "start_gateway_output.log"),
        "StandardErrorPath": os.path.join(PROJECT, "tracker_data", "start_gateway_output.log"),
    }
    os.makedirs(os.path.dirname(PLIST), exist_ok=True)
    os.makedirs(os.path.join(PROJECT, "tracker_data"), exist_ok=True)
    with open(PLIST, "wb") as f:
        plistlib.dump(plist, f)
    result = run("launchctl", "bootstrap", DOMAIN, PLIST)
    if result.returncode != 0:
        print("Could not turn it on:", (result.stderr or result.stdout).strip())
        return
    print("IB Gateway will now open and log in at 9:31 AM Eastern, Monday to Friday.")
    print("Your phone gets an alert. Approve the login in IBKR Mobile.")


def main():
    action = sys.argv[1] if len(sys.argv) > 1 else "setup"
    if action in ("schedule", "remove"):
        schedule(action)
    else:
        setup()


if __name__ == "__main__":
    main()
