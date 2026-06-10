#!/usr/bin/env python3
"""
ThaiCash booth-rate scraper.

Fetches the public USD-100 *buy* rate (THB per $1) from Bangkok exchange-chain
websites and writes data/booth-rates.json. Runs on a GitHub Actions cron; the
app fetches the committed JSON as a static file.

Design rules:
- stdlib only (no pip installs in CI)
- every booth is independent: one site breaking never blocks the others
- sanity-gate every parsed rate (25 < rate < 45) so a site redesign can only
  mark a booth stale, never publish garbage
- booths we can't parse yet ship as ok=false with a reason — the app falls
  back to reputation tiers for those
"""

import json
import re
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
      "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Safari/605.1.15")

SANITY_LO, SANITY_HI = 25.0, 45.0   # plausible THB-per-USD window


def fetch(url: str, timeout: int = 20) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8", errors="replace")


def first_rate_after(html: str, marker: str, window: int = 400):
    """First plausible THB-per-USD decimal following any occurrence of `marker`.

    Tries every occurrence: markers can also appear in image alts/nav items
    with no rate nearby (K79 does this).
    """
    for m in re.finditer(re.escape(marker), html):
        chunk = html[m.start(): m.start() + window]
        for r in re.finditer(r"(\d{2}\.\d{1,4})", chunk):
            v = float(r.group(1))
            if SANITY_LO < v < SANITY_HI:
                return v
    return None


def scrape_k79():
    html = fetch("https://www.k79exchange.com/")
    rate = first_rate_after(html, "USD 100")
    ts = re.search(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}", html)
    return rate, (ts.group(0) + " +07:00 (site)" if ts else None)


def scrape_vasu():
    html = fetch("https://www.vasuexchange.co.th/")
    return first_rate_after(html, "USD 100"), None


BOOTHS = [
    # (id matching catalog.json, display name, scraper or None, pending reason)
    ("vasu",           "Vasu Exchange",      scrape_vasu, None),
    ("k79",            "K79 Exchange",       scrape_k79,  None),
    ("superrich_th",   "SuperRich Thailand", None, "rates API requires auth — pending"),
    ("superrich_1965", "SuperRich 1965",     None, "JS-rendered — pending endpoint discovery"),
    ("siam_exchange",  "Siam Exchange",      None, "React SPA — pending endpoint discovery"),
]


def main() -> int:
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    rates, failures = [], 0

    for booth_id, name, scraper, pending in BOOTHS:
        entry = {"id": booth_id, "name": name, "ok": False}
        if scraper is None:
            entry["reason"] = pending
        else:
            try:
                rate, site_ts = scraper()
                if rate is not None:
                    entry.update(ok=True, usd100Buy=rate, fetchedAt=now)
                    if site_ts:
                        entry["siteTime"] = site_ts
                else:
                    entry["reason"] = "marker/rate not found (site changed?)"
                    failures += 1
            except Exception as exc:                       # noqa: BLE001
                entry["reason"] = f"fetch failed: {type(exc).__name__}"
                failures += 1
        rates.append(entry)

    out = {"version": 1, "updated": now, "rates": rates}
    out_path = Path(__file__).resolve().parent.parent / "data" / "booth-rates.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(out, indent=2) + "\n")

    live = [r for r in rates if r["ok"]]
    print(f"wrote {out_path} — {len(live)} live, {failures} failures")
    for r in live:
        print(f"  {r['name']}: {r['usd100Buy']}")
    # Exit 0 even with partial failures (stale entries are handled app-side);
    # exit 1 only if NOTHING scraped, so the Action flags total breakage.
    return 0 if live else 1


if __name__ == "__main__":
    sys.exit(main())
