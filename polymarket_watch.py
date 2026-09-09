#!/usr/bin/env python3
"""
Polymarket account watcher.

Checks a list of Polymarket wallet addresses for new trades since the last
run, and pushes a phone notification (via ntfy.sh) for each new bet found.

State (the last-seen trade timestamp per wallet) is persisted to state.json
so this can be re-run on a schedule (e.g. every 5-10 min via GitHub Actions
or cron) without re-notifying on the same trade twice.
"""

import json
import os
import socket
import time
import urllib.request
import urllib.parse
from pathlib import Path

# Force IPv4 for all outbound requests. Some CI runners (including GitHub
# Actions) advertise broken/unreachable IPv6 routes, which makes urllib pick
# an IPv6 address for a host like ntfy.sh and fail with
# "[Errno 101] Network is unreachable". Forcing IPv4 avoids that.
_orig_getaddrinfo = socket.getaddrinfo


def _ipv4_only_getaddrinfo(host, port, family=0, type=0, proto=0, flags=0):
    return _orig_getaddrinfo(host, port, socket.AF_INET, type, proto, flags)


socket.getaddrinfo = _ipv4_only_getaddrinfo

# ---------------------------------------------------------------------------
# CONFIG - fill these in
# ---------------------------------------------------------------------------

ACCOUNTS = {
    # display_name: wallet_address
    "lilybaeum": "0x01c78f8873c0c86d6b6b92ff627e3802237ee995",
    "monkeymashingkeyboard": "0x684baa57c338c2549aec0aa3f034f695d72a8409",
    "ferrarichampions2026": "0xfe787d2da716d60e8acff57fb87eb13cd4d10319",
}

# Accounts in this set get flagged/prioritized in the notification
PRIORITY_ACCOUNTS = {"ferrarichampions2026"}

# ntfy.sh topic - pick a random, hard-to-guess string. Install the ntfy app
# (iOS/Android) and subscribe to this same topic name to get push alerts.
NTFY_TOPIC = os.environ.get("NTFY_TOPIC", "REPLACE_ME_WITH_A_UNIQUE_TOPIC")

STATE_FILE = Path(__file__).parent / "state.json"

DATA_API = "https://data-api.polymarket.com"

# ---------------------------------------------------------------------------


def http_get_json(url: str):
    req = urllib.request.Request(url, headers={"User-Agent": "polymarket-watch/1.0"})
    with urllib.request.urlopen(req, timeout=20) as resp:
        return json.loads(resp.read().decode("utf-8"))


def load_state() -> dict:
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text())
    return {}


def save_state(state: dict):
    STATE_FILE.write_text(json.dumps(state, indent=2))


def fetch_recent_activity(address: str, limit: int = 50):
    """Recent TRADE activity for a wallet, newest first."""
    url = f"{DATA_API}/activity?user={address}&limit={limit}&type=TRADE"
    try:
        return http_get_json(url)
    except Exception as e:
        print(f"  ! failed to fetch activity for {address}: {e}")
        return []


def todays_total_usdc(activity: list, now_ts: int) -> float:
    """Sum of usdcSize for BUY trades in the last 24h, from an activity page."""
    cutoff = now_ts - 24 * 3600
    total = 0.0
    for item in activity:
        if item.get("timestamp", 0) >= cutoff and item.get("side") == "BUY":
            total += float(item.get("usdcSize", 0) or 0)
    return total


def send_ntfy(topic: str, title: str, message: str, priority: str = "default", url: str = None):
    headers = {
        "Title": title.encode("utf-8"),
        "Priority": priority,
    }
    if url:
        headers["Click"] = url
    req = urllib.request.Request(
        f"https://ntfy.sh/{topic}",
        data=message.encode("utf-8"),
        headers=headers,
        method="POST",
    )
    try:
        urllib.request.urlopen(req, timeout=10)
    except Exception as e:
        print(f"  ! failed to send notification: {e}")


def format_bet_message(item: dict, pct_of_day: float, day_total: float) -> str:
    side = item.get("side", "?")
    outcome = item.get("outcome", "?")
    title = item.get("title", "Unknown market")
    size = float(item.get("size", 0) or 0)
    usdc = float(item.get("usdcSize", 0) or 0)
    price = float(item.get("price", 0) or 0)

    lines = [
        f"{side} {outcome} on: {title}",
        f"${usdc:,.2f} ({size:,.1f} shares @ {price*100:.1f}c)",
        f"= {pct_of_day:.1f}% of
