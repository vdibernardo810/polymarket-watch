#!/usr/bin/env python3
"""
Win rate calculator for tracked Polymarket accounts.

Pulls every resolved position (CLOSED = exited via sale/redemption,
REDEEMABLE = resolved but payout not yet claimed) for each tracked account
and classifies each as a win (positive total P&L) or a loss (zero or
negative total P&L), then reports a win rate.

This is a straightforward win/loss count by profitability, not a
probability-calibration or CLV-style metric - a position that was sold
early for a small profit counts as a "win" the same as one that resolved
in their favor and was redeemed.
"""

import json
import socket
import time
import urllib.request
import urllib.parse

_orig_getaddrinfo = socket.getaddrinfo


def _ipv4_only_getaddrinfo(host, port, family=0, type=0, proto=0, flags=0):
    return _orig_getaddrinfo(host, port, socket.AF_INET, type, proto, flags)


socket.getaddrinfo = _ipv4_only_getaddrinfo

ACCOUNTS = {
    "monkeymashingkeyboard": "0x684baa57c338c2549aec0aa3f034f695d72a8409",
    "ferrarichampions2026": "0xfe787d2da716d60e8acff57fb87eb13cd4d10319",
}

DATA_API = "https://data-api.polymarket.com/v2"


def http_get_json(url):
    req = urllib.request.Request(url, headers={"User-Agent": "polymarket-winrate/1.0"})
    with urllib.request.urlopen(req, timeout=20) as resp:
        return json.loads(resp.read().decode("utf-8"))


def fetch_all_positions(address, status, max_pages=300):
    """Paginate through every position at a given status for this address.
    max_pages is a generous safety ceiling (300 pages x 500 = up to 150,000
    positions) - not meant to actually cap normal accounts, just prevent a
    runaway loop if the API ever misbehaves."""
    results = []
    cursor = None
    for _ in range(max_pages):
        url = DATA_API + "/positions?user=" + address + "&status=" + status + "&limit=500"
        if cursor:
            url += "&cursor=" + urllib.parse.quote(cursor)
        try:
            payload = http_get_json(url)
        except Exception as e:
            print("  ! failed to fetch " + status + " positions: " + str(e))
            break
        rows = payload.get("data", [])
        results.extend(rows)
        pagination = payload.get("pagination", {})
        if not pagination.get("has_more"):
            break
        cursor = pagination.get("next_cursor")
        if not cursor:
            break
        if len(results) % 5000 == 0:
            print("    ...fetched " + str(len(results)) + " " + status.lower() + " positions so far")
        time.sleep(0.2)  # be polite to the API
    return results


def classify(position):
    total_pnl = position.get("total_pnl")
    if total_pnl is None:
        realized = position.get("realized_pnl", 0) or 0
        unrealized = position.get("unrealized_pnl", 0) or 0
        total_pnl = realized + unrealized

    if total_pnl > 0:
        return "win"
    elif total_pnl < 0:
        return "loss"
    else:
        return "push"


def main():
    overall = {"win": 0, "loss": 0, "push": 0}

    for name, address in ACCOUNTS.items():
        print("=" * 60)
        print(name + " (" + address[:10] + "...)")
        print("=" * 60)

        closed = fetch_all_positions(address, "CLOSED")
        redeemable = fetch_all_positions(address, "REDEEMABLE")
        all_resolved = closed + redeemable

        counts = {"win": 0, "loss": 0, "push": 0}
        total_pnl_sum = 0.0

        for pos in all_resolved:
            result = classify(pos)
            counts[result] += 1
            overall[result] += 1
            total_pnl_sum += pos.get("total_pnl", 0) or 0

        decided = counts["win"] + counts["loss"]
        win_rate = (counts["win"] / decided * 100) if decided > 0 else 0.0

        print("Resolved positions: " + str(len(all_resolved)) + " (" + str(len(closed)) + " closed, " + str(len(redeemable)) + " redeemable-unclaimed)")
        print("Wins: " + str(counts["win"]) + "  Losses: " + str(counts["loss"]) + "  Pushes: " + str(counts["push"]))
        print("Win rate: " + str(round(win_rate, 1)) + "%  (excludes pushes)")
        print("Net P&L across resolved positions: $" + format(total_pnl_sum, ",.2f"))
        print("")

    decided_overall = overall["win"] + overall["loss"]
    overall_rate = (overall["win"] / decided_overall * 100) if decided_overall > 0 else 0.0

    print("=" * 60)
    print("COMBINED (both tracked accounts)")
    print("=" * 60)
    print("Wins: " + str(overall["win"]) + "  Losses: " + str(overall["loss"]) + "  Pushes: " + str(overall["push"]))
    print("Overall win rate: " + str(round(overall_rate, 1)) + "%")


if __name__ == "__main__":
    main()
