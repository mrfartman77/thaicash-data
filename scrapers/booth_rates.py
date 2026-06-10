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

from __future__ import annotations   # PEP 604 (dict | None) hints on Python 3.7+

import json
import re
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
      "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Safari/605.1.15")

SANITY_LO, SANITY_HI = 25.0, 45.0   # plausible THB-per-USD window


def fetch(url: str, timeout: int = 20, headers: dict | None = None) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": UA, **(headers or {})})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8", errors="replace")


def fetch_json(url: str, timeout: int = 20, headers: dict | None = None):
    return json.loads(fetch(url, timeout=timeout, headers=headers))


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


def scrape_superrich_th():
    """SuperRich Thailand (the orange chain) — the benchmark Bangkok rate.

    Its site reads a public JSON rate board at /api/v1/rates. The Basic-auth
    token below is the fixed client credential the web app ships to every
    visitor's browser (not a per-user secret); it gates the public endpoint
    the same way for everyone. We read the USD denom-"100" buy rate.
    """
    auth = "Basic c3VwZXJyaWNoVGg6aFRoY2lycmVwdXM="
    doc = fetch_json("https://www.superrichthailand.com/api/v1/rates",
                     headers={"Authorization": auth, "Accept": "application/json"})
    for cur in doc.get("data", {}).get("exchangeRate", []):
        if cur.get("cUnit") != "USD":
            continue
        for row in cur.get("rate", []):
            if str(row.get("denom")) == "100" and row.get("cBuying") is not None:
                return float(row["cBuying"]), row.get("dateTime")
    return None, None


BOOTHS = [
    # (id matching catalog.json, display name, scraper or None, pending reason)
    ("vasu",           "Vasu Exchange",      scrape_vasu, None),
    ("k79",            "K79 Exchange",       scrape_k79,  None),
    ("superrich_th",   "SuperRich Thailand", scrape_superrich_th, None),
    ("superrich_1965", "SuperRich 1965",     None, "rate API is WAF-blocked (403) to non-browser clients"),
    ("siam_exchange",  "Siam Exchange",      None, "site's own JS bundles 404 — no live rate source"),
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
