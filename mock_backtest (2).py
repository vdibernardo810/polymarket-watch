#!/usr/bin/env python3
"""
Mock-account backtest: simulates copying a tracked Polymarket wallet's
qualifying bets (>= MIN_PCT_OF_DAY of their own daily volume, same threshold
used by the live Kalshi auto-trader) starting from a $100 bankroll, over the
last LOOKBACK_DAYS days.

METHODOLOGY / CAVEATS - read before trusting the output:
- Position sizing: each simulated bet is sized as (their bet's % of their
  own day's total volume) applied to OUR current balance, same logic as
  kalshi_autotrade.py. This compounds - wins grow the size of future bets,
  losses shrink it.
- For a bet whose market has since resolved (status REDEEMABLE in Polymarket
  position data), the payoff is computed exactly: 1/share if their side won,
  0/share if it lost, against the price they actually paid for that trade.
- For a bet whose position was exited early (status CLOSED - they sold
  before resolution), there's no clean per-trade exit price available at
  this granularity, so we approximate using that position's own overall
  percent_pnl as the return. This is a real simplification and can be
  wrong for a position built from multiple trades at different times.
- Bets with no resolved or closed position match (still fully open, unknown
  outcome) are excluded from the simulation and reported separately.
- No fees, slippage, or liquidity limits are modeled. This assumes every
  simulated fill happened at exactly the price they got.
"""

import json
import socket
import time
import urllib.request
import urllib.parse
from datetime import datetime, timezone

_orig_getaddrinfo = socket.getaddrinfo


def _ipv4_only_getaddrinfo(host, port, family=0, type=0, proto=0, flags=0):
    return _orig_getaddrinfo(host, port, socket.AF_INET, type, proto, flags)


socket.getaddrinfo = _ipv4_only_getaddrinfo

# ---------------------------------------------------------------------------
# CONFIG
# ---------------------------------------------------------------------------

WALLET_NAME = "monkeymashingkeyboard"
WALLET_ADDRESS = "0x684baa57c338c2549aec0aa3f034f695d72a8409"

STARTING_BALANCE = 100.0
LOOKBACK_DAYS = 30
MIN_PCT_OF_DAY = 5.0  # same threshold as the live Kalshi auto-trader

DATA_API_V1 = "https://data-api.polymarket.com"
DATA_API_V2 = "https://data-api.polymarket.com/v2"

# ---------------------------------------------------------------------------


def http_get_json(url):
    req = urllib.request.Request(url, headers={"User-Agent": "polymarket-backtest/1.0"})
    with urllib.request.urlopen(req, timeout=20) as resp:
        return json.loads(resp.read().decode("utf-8"))


def fetch_activity_window(address, window_start_ts, max_pages=50):
    """Fetch BUY trade activity within the lookback window, oldest-safe."""
    all_items = []
    offset = 0
    page_size = 500

    for _ in range(max_pages):
        url = (
            DATA_API_V1 + "/activity?user=" + address
            + "&limit=" + str(page_size)
            + "&offset=" + str(offset)
            + "&type=TRADE"
        )
        try:
            page = http_get_json(url)
        except Exception as e:
            print("  ! failed to fetch activity page: " + str(e))
            break

        if not page:
            break

        all_items.extend(page)

        oldest_ts_in_page = min(item.get("timestamp", 0) for item in page)
        if oldest_ts_in_page < window_start_ts:
            # we've gone far enough back, no need for more pages
            break

        if len(page) < page_size:
            break

        offset += page_size
        time.sleep(0.15)

    # keep only BUY trades inside the window
    return [
        item for item in all_items
        if item.get("side") == "BUY" and item.get("timestamp", 0) >= window_start_ts
    ]


def fetch_all_positions(address, status, max_pages=300):
    results = []
    cursor = None
    for _ in range(max_pages):
        url = DATA_API_V2 + "/positions?user=" + address + "&status=" + status + "&limit=500"
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
        time.sleep(0.15)
    return results


def build_resolution_lookup(closed_positions, redeemable_positions):
    """
    Three-tier lookup, most precise first:
    1. token_id - the literal specific outcome token. This is the
       bulletproof match: unambiguous, no possibility of collision.
    2. slug + outcome - unique per specific market instance (includes the
       date), used only if a token_id match isn't found.
    3. title + outcome - last resort. Polymarket reuses generic titles like
       "Spread: Los Angeles Angels (-3.5)" across different dates, so this
       tier can mismatch and is only kept as a final fallback.
    """
    token_lookup = {}
    slug_lookup = {}
    title_lookup = {}

    for pos in redeemable_positions:
        info = {"kind": "redeemable", "current_price": pos.get("current_price", 0)}
        token_id = pos.get("token_id", "")
        slug = pos.get("slug", "")
        title = pos.get("title", "")
        outcome = pos.get("outcome", "")
        if token_id:
            token_lookup[token_id] = info
        if slug:
            slug_lookup[(slug, outcome)] = info
        title_lookup.setdefault((title, outcome), info)

    for pos in closed_positions:
        info = {"kind": "closed", "percent_pnl": pos.get("percent_pnl", 0)}
        token_id = pos.get("token_id", "")
        slug = pos.get("slug", "")
        title = pos.get("title", "")
        outcome = pos.get("outcome", "")
        if token_id and token_id not in token_lookup:
            token_lookup[token_id] = info
        if slug and (slug, outcome) not in slug_lookup:
            slug_lookup[(slug, outcome)] = info
        title_lookup.setdefault((title, outcome), info)

    return token_lookup, slug_lookup, title_lookup


def find_resolution(token_id, slug, title, outcome, token_lookup, slug_lookup, title_lookup):
    if token_id:
        match = token_lookup.get(token_id)
        if match:
            return match, "token_id"
    if slug:
        match = slug_lookup.get((slug, outcome))
        if match:
            return match, "slug"
    match = title_lookup.get((title, outcome))
    if match:
        return match, "title"
    return None, None


def main():
    now_ts = int(time.time())
    window_start_ts = now_ts - LOOKBACK_DAYS * 24 * 3600

    print("Backtesting " + WALLET_NAME + " over the last " + str(LOOKBACK_DAYS) + " days")
    print("Starting mock balance: $" + format(STARTING_BALANCE, ",.2f"))
    print("Auto-trade threshold: >= " + str(MIN_PCT_OF_DAY) + "% of their day's volume")
    print("")

    print("Fetching trade activity...")
    trades = fetch_activity_window(WALLET_ADDRESS, window_start_ts)
    print("  " + str(len(trades)) + " BUY trades in window")

    print("Fetching resolved positions for outcome matching...")
    closed = fetch_all_positions(WALLET_ADDRESS, "CLOSED")
    redeemable = fetch_all_positions(WALLET_ADDRESS, "REDEEMABLE")
    token_lookup, slug_lookup, title_lookup = build_resolution_lookup(closed, redeemable)
    print("  " + str(len(token_lookup)) + " resolved token_id entries, " + str(len(slug_lookup)) + " slug fallback entries, " + str(len(title_lookup)) + " title fallback entries")
    print("")

    # --- compute each calendar day's total BUY volume, for pct_of_day ---
    day_totals = {}
    for item in trades:
        ts = item.get("timestamp", 0)
        day_key = datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d")
        day_totals[day_key] = day_totals.get(day_key, 0) + float(item.get("usdcSize", 0) or 0)

    # --- filter to qualifying trades ---
    qualifying = []
    for item in trades:
        ts = item.get("timestamp", 0)
        day_key = datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d")
        day_total = day_totals.get(day_key, 0)
        usdc = float(item.get("usdcSize", 0) or 0)
        pct_of_day = (usdc / day_total * 100) if day_total > 0 else 0.0
        if pct_of_day >= MIN_PCT_OF_DAY:
            item["_pct_of_day"] = pct_of_day
            qualifying.append(item)

    qualifying.sort(key=lambda x: x.get("timestamp", 0))
    print(str(len(qualifying)) + " trades met the " + str(MIN_PCT_OF_DAY) + "% threshold and qualify for simulation")
    print("")

    # --- simulate ---
    balance = STARTING_BALANCE
    simulated_count = 0
    skipped_no_match = 0
    wins = 0
    losses = 0
    token_matches = 0
    slug_matches = 0
    title_fallback_matches = 0
    first_trade_date = None
    last_trade_date = None

    print("-" * 70)
    for item in qualifying:
        title = item.get("title", "")
        outcome = item.get("outcome", "")
        slug = item.get("slug", "")
        token_id = item.get("asset", "")
        price = float(item.get("price", 0) or 0)
        pct_of_day = item["_pct_of_day"]
        ts = item.get("timestamp", 0)
        date_str = datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d")

        if first_trade_date is None:
            first_trade_date = date_str
        last_trade_date = date_str

        match, match_kind = find_resolution(token_id, slug, title, outcome, token_lookup, slug_lookup, title_lookup)
        if match:
            if match_kind == "token_id":
                token_matches += 1
            elif match_kind == "slug":
                slug_matches += 1
            else:
                title_fallback_matches += 1
        if not match or price <= 0:
            skipped_no_match += 1
            continue

        if match["kind"] == "redeemable":
            payout_per_share = round(match["current_price"])  # 0 or 1
            return_multiple = payout_per_share / price
            debug_info = "current_price=" + str(match["current_price"]) + " payout=" + str(payout_per_share)
        else:  # closed
            return_multiple = 1 + (match["percent_pnl"] / 100.0)
            debug_info = "percent_pnl=" + str(match["percent_pnl"])

        bet_size = balance * (pct_of_day / 100.0)
        bet_size = min(bet_size, balance)  # never bet more than we have

        pnl = bet_size * (return_multiple - 1)
        balance += pnl
        simulated_count += 1

        if pnl > 0:
            wins += 1
            result = "WIN "
        else:
            losses += 1
            result = "LOSS"

        pct_str = str(round(pct_of_day, 1))
        print(date_str + "  " + result + "  [" + match_kind + "] " + debug_info + "  return_mult=" + str(round(return_multiple, 3)) + "  " + pct_str + "% sizing  bet $" + format(bet_size, ",.2f") + "  pnl $" + format(pnl, ",.2f") + "  -> balance $" + format(balance, ",.2f") + "  [" + title[:40] + "]")

        if balance <= 0.01:
            print("Balance wiped out - stopping simulation early.")
            break

    print("-" * 70)
    print("")
    print("=" * 60)
    print("RESULTS")
    print("=" * 60)
    print("Timeframe: " + str(first_trade_date) + " to " + str(last_trade_date))
    print("Trades simulated: " + str(simulated_count) + "  (Wins: " + str(wins) + "  Losses: " + str(losses) + ")")
    print("  matched via token_id (bulletproof): " + str(token_matches) + "  |  via slug: " + str(slug_matches) + "  |  via title fallback (least reliable): " + str(title_fallback_matches))
    print("Trades skipped (no resolution match found): " + str(skipped_no_match))
    print("")
    print("Starting balance: $" + format(STARTING_BALANCE, ",.2f"))
    print("Ending balance:   $" + format(balance, ",.2f"))
    total_pnl = balance - STARTING_BALANCE
    total_pct = (total_pnl / STARTING_BALANCE * 100) if STARTING_BALANCE > 0 else 0
    print("Net P&L:          $" + format(total_pnl, ",.2f") + "  (" + str(round(total_pct, 1)) + "%)")


if __name__ == "__main__":
    main()
