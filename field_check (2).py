#!/usr/bin/env python3
"""
Diagnostic only - prints the full raw JSON of a few real trades so we can
see exactly what field names Polymarket's activity API actually returns
(in particular, whether "slug" is present and populated).
"""

import json
import socket
import urllib.request

_orig_getaddrinfo = socket.getaddrinfo


def _ipv4_only_getaddrinfo(host, port, family=0, type=0, proto=0, flags=0):
    return _orig_getaddrinfo(host, port, socket.AF_INET, type, proto, flags)


socket.getaddrinfo = _ipv4_only_getaddrinfo

WALLET_ADDRESS = "0x684baa57c338c2549aec0aa3f034f695d72a8409"

url = "https://data-api.polymarket.com/activity?user=" + WALLET_ADDRESS + "&limit=3&type=TRADE"
req = urllib.request.Request(url, headers={"User-Agent": "field-check/1.0"})

with urllib.request.urlopen(req, timeout=20) as resp:
    data = json.loads(resp.read().decode("utf-8"))

print("Got " + str(len(data)) + " raw trade records. Full contents of each:")
print("")
for i, item in enumerate(data):
    print("--- Trade " + str(i + 1) + " ---")
    print(json.dumps(item, indent=2))
    print("")

# --- also check the positions endpoint's real field names ---
print("")
print("=" * 60)
print("Now checking /v2/positions field names (CLOSED and REDEEMABLE)")
print("=" * 60)

for status in ("CLOSED", "REDEEMABLE"):
    pos_url = "https://data-api.polymarket.com/v2/positions?user=" + WALLET_ADDRESS + "&status=" + status + "&limit=2"
    pos_req = urllib.request.Request(pos_url, headers={"User-Agent": "field-check/1.0"})
    with urllib.request.urlopen(pos_req, timeout=20) as resp:
        pos_payload = json.loads(resp.read().decode("utf-8"))
    rows = pos_payload.get("data", [])
    print("")
    print("--- " + status + " positions (" + str(len(rows)) + " shown) ---")
    for row in rows:
        print(json.dumps(row, indent=2))
        print("")

# --- also check the v2/activity endpoint (cursor-paginated, no offset cap -
# this is what mock_backtest.py needs to switch to for windows beyond ~5500
# records, since v1 /activity's offset pagination hard-fails past that) ---
print("")
print("=" * 60)
print("Now checking /v2/activity field names")
print("=" * 60)

v2_activity_url = "https://data-api.polymarket.com/v2/activity?user=" + WALLET_ADDRESS + "&limit=2&type=TRADE"
v2_req = urllib.request.Request(v2_activity_url, headers={"User-Agent": "field-check/1.0"})
with urllib.request.urlopen(v2_req, timeout=20) as resp:
    v2_payload = json.loads(resp.read().decode("utf-8"))

print("")
print(json.dumps(v2_payload, indent=2))
