#!/usr/bin/env python3
"""
ThaiCash crypto-rates scraper.

Fetches the live USDT/THB *bid* (what a market sell receives) from the three
SEC-licensed Thai venues' public ticker APIs and writes data/crypto-rates.json.
Runs on the same GitHub Actions cron as the booth scraper — but crypto trades
24/7, so the workflow fires around the clock, not just Bangkok shop hours.

Design rules (same as booth_rates.py):
- stdlib only (no pip installs in CI)
- every venue is independent: one API breaking never blocks the others
- sanity-gate every parsed rate (25 < rate < 45) so an API change can only
  mark a venue stale, never publish garbage
- if EVERY venue fails, exit non-zero WITHOUT writing — the last good feed
  survives and the app's freshness gate handles the rest
"""

from __future__ import annotations

import json
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
      "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Safari/605.1.15")

SANITY_LO, SANITY_HI = 25.0, 45.0   # plausible THB-per-USDT window (~USD/THB)


def fetch_json(url: str, timeout: int = 20):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8", errors="replace"))


def scrape_binance_th() -> float:
    """Gulf Binance public API. Prefer the order-book bid; fall back to last."""
    try:
        d = fetch_json("https://api.binance.th/api/v1/ticker/bookTicker?symbol=USDTTHB")
        return float(d["bidPrice"])
    except Exception:
        d = fetch_json("https://api.binance.th/api/v1/ticker/price?symbol=USDTTHB")
        return float(d["price"])


def scrape_bitkub() -> float:
    d = fetch_json("https://api.bitkub.com/api/v3/market/ticker?sym=usdt_thb")
    return float(d[0]["highest_bid"])


def scrape_bitazza() -> float:
    d = fetch_json("https://apexapi.bitazza.co.th/AP/summary")
    for row in d:
        if row.get("trading_pairs") == "USDT_THB":
            return float(row["highest_bid"])
    raise ValueError("USDT_THB pair missing from summary")


# id must match the catalog leg id — the app keys live rates by it.
VENUES = [
    ("binance_th_usdt", "Binance TH", scrape_binance_th),
    ("bitkub_usdt",     "Bitkub",     scrape_bitkub),
    ("bitazza_usdt",    "Bitazza",    scrape_bitazza),
]


def main() -> None:
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    rates = []
    for vid, name, scraper in VENUES:
        entry: dict = {"id": vid, "name": name, "ok": False}
        try:
            v = scraper()
            if not (SANITY_LO < v < SANITY_HI):
                raise ValueError(f"rate {v} outside sanity window")
            entry.update(ok=True, thbPerUsdt=round(v, 4), fetchedAt=now)
        except Exception as e:  # noqa: BLE001 — per-venue isolation by design
            entry["reason"] = f"{type(e).__name__}: {e}"[:200]
        rates.append(entry)

    live = sum(1 for r in rates if r["ok"])
    for r in rates:
        print(f"  {r['name']}: {r.get('thbPerUsdt', r.get('reason'))}")
    if live == 0:
        print("all venues failed — keeping the last good feed", file=sys.stderr)
        sys.exit(1)

    out = {"version": 1, "updated": now, "rates": rates}
    path = Path(__file__).resolve().parent.parent / "data" / "crypto-rates.json"
    path.write_text(json.dumps(out, indent=2, ensure_ascii=False) + "\n")
    print(f"wrote {path} — {live} live, {len(rates) - live} failures")


if __name__ == "__main__":
    main()
