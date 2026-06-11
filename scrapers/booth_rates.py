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

SANITY_LO, SANITY_HI = 25.0, 45.0   # plausible THB-per-USD window (Siam/CashChanger USD check)

# Corridor currencies and their plausible THB-per-unit windows. A board figure
# outside its window is treated as not-found, never published.
CURRENCIES = ("USD", "EUR", "AUD", "CNY")
SANITY = {"USD": (25.0, 45.0), "EUR": (28.0, 52.0), "AUD": (15.0, 30.0), "CNY": (3.5, 5.2)}

# Max age of a third-party (CashChanger) board reading we'll still publish.
# A live booth refreshes its tourist currencies several times a day; matches the
# app's 24h engine-freshness gate, so a borderline reading is never trusted twice.
CC_MAX_AGE_HOURS = 24

_AGE_UNITS = {"second": 1 / 3600, "minute": 1 / 60, "hour": 1,
              "day": 24, "week": 168, "month": 730, "year": 8760}


class RateUnavailable(Exception):
    """Source reached but no fresh, trustworthy rate — list the booth without one
    (an expected, handled state, not a scraper failure)."""


def relative_age_hours(phrase: str) -> float:
    """'2 hours ago' / 'just now' / '23 days ago' -> approximate hours (inf if unparseable)."""
    if "just now" in phrase:
        return 0.0
    m = re.search(r"(\d+)\s*(second|minute|hour|day|week|month|year)s?\s*ago", phrase)
    return int(m.group(1)) * _AGE_UNITS[m.group(2)] if m else float("inf")


def fetch(url: str, timeout: int = 20, headers: dict | None = None,
          data: bytes | None = None, method: str | None = None) -> str:
    req = urllib.request.Request(url, data=data, method=method,
                                 headers={"User-Agent": UA, **(headers or {})})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8", errors="replace")


def fetch_json(url: str, timeout: int = 20, headers: dict | None = None,
               data: bytes | None = None, method: str | None = None):
    return json.loads(fetch(url, timeout=timeout, headers=headers, data=data, method=method))


def first_rate_after(html: str, marker: str, window: int = 400, cur: str = "USD"):
    """First plausible THB-per-unit decimal following any occurrence of `marker`.

    Tries every occurrence: markers can also appear in image alts/nav items
    with no rate nearby (K79 does this). The first plausible figure after a
    currency's marker is the buy rate (boards render buy before sell).
    """
    lo, hi = SANITY[cur]
    for m in re.finditer(re.escape(marker), html):
        chunk = html[m.start(): m.start() + window]
        for r in re.finditer(r"(\d{1,2}\.\d{1,4})", chunk):
            v = float(r.group(1))
            if lo < v < hi:
                return v
    return None


# Marker for each currency's board row on the marker-scan sites. USD uses the
# big-note row label; EUR/AUD rows are found by code (sanity windows do the rest).
MARKERS = {"USD": "USD 100", "EUR": "EUR", "AUD": "AUD", "CNY": "CNY"}


def scrape_k79():
    html = fetch("https://www.k79exchange.com/")
    buy = {c: first_rate_after(html, MARKERS[c], cur=c) for c in CURRENCIES}
    ts = re.search(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}", html)
    return buy, (ts.group(0) + " +07:00 (site)" if ts else None)


def scrape_vasu():
    html = fetch("https://www.vasuexchange.co.th/")
    return {c: first_rate_after(html, MARKERS[c], cur=c) for c in CURRENCIES}, None


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
    buy: dict = {}
    site_ts = None
    for cur in doc.get("data", {}).get("exchangeRate", []):
        unit = cur.get("cUnit")
        if unit not in CURRENCIES:
            continue
        lo, hi = SANITY[unit]
        # Best (large-note) buy across denominations = the headline board rate.
        vals = [float(r["cBuying"]) for r in cur.get("rate", [])
                if r.get("cBuying") is not None and lo < float(r["cBuying"]) < hi]
        if vals:
            buy[unit] = max(vals)
            site_ts = site_ts or (cur.get("rate") or [{}])[0].get("dateTime")
    return buy, site_ts


def scrape_sr1965():
    """SuperRich 1965 (the green chain) — Nuxt site reads a public microservice API.

    Two public calls (no login): GET an anonymous token from the oauth2 callback,
    then POST the rate board for company A04 (= SR1965), branch "36" — their default
    headline branch, the one the website shows and which carries the best board rate.
    The USD "100-50" denomination is the large-note buy rate (the USD-100 equivalent).
    """
    token = json.loads(fetch(
        "https://api.superrich1965.com/front/exchange-rate/oauth2/callback"))["accessToken"]
    body = json.dumps({"filters": [
        {"field": "company_code", "value": "A04"},
        {"field": "branch_no", "value": "36"},
    ]}).encode()
    doc = fetch_json(
        "https://www.superrich1965.com/api/exchange-rate-service/v1/external-app-exchange-rate/get",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json",
                 "Referer": "https://www.superrich1965.com/"},
        data=body, method="POST")
    buy: dict = {}
    for row in doc.get("data", {}).get("datas", []):
        unit = row.get("currency_code")
        if unit not in CURRENCIES:
            continue
        lo, hi = SANITY[unit]
        vals = [float(d["buy_rate_amount"]) for d in row.get("denom_list", [])
                if d.get("buy_rate_amount") and lo < float(d["buy_rate_amount"]) < hi]
        if vals:
            buy[unit] = max(vals)   # large-note denom carries the best board rate
    return buy, None


def scrape_siam():
    """Siam Exchange — their own website is dead (JS/CSS bundles 404, blank page),
    but Siam is an active merchant on CashChanger, whose rates are *self-published*
    by the changer (their merchant pitch is "set/update your rates"; they don't
    scrape non-participants). So this board reading is effectively Siam's own posted
    rate. We read the WE-BUY USD rate, but only when CashChanger's per-currency
    timestamp is fresh (<24h) — otherwise refuse it; the app then lists Siam
    location-only. Source is surfaced as `via CashChanger` for honesty.
    """
    html = fetch("https://cashchanger.co/thailand/mc/siam-exchange/286")
    # Visible table renders WE BUY before WE SELL. Anchor on the last WE BUY header
    # (skips the hidden/responsive copy), then the first 'USD 1 = THB <rate>' is the
    # buy rate; the relative timestamp follows it immediately.
    anchor = html.rfind("WE BUY")
    region = html[anchor:] if anchor >= 0 else html
    m = re.search(r"USD\s*1\s*=\s*THB\s*([\d.]+)", region)
    if not m:
        return None, None                                  # layout changed
    rate = float(m.group(1))
    if not (SANITY_LO < rate < SANITY_HI):
        return None, None
    age = re.search(r"(just now|\d+\s*(?:second|minute|hour|day|week|month|year)s?\s*ago)",
                    region[m.end(): m.end() + 200])
    phrase = age.group(1) if age else ""
    if relative_age_hours(phrase) > CC_MAX_AGE_HOURS:
        raise RateUnavailable(f"CashChanger board stale ({phrase or 'no timestamp'})")
    buy = {"USD": rate}
    # Other currency rows carry their own CashChanger timestamps and can be
    # months staler than the USD row (Siam's CNY was) — gate each one.
    for cur in ("EUR", "AUD", "CNY"):
        lo, hi = SANITY[cur]
        mc = re.search(cur + r"\s*1\s*=\s*THB\s*([\d.]+)", region)
        if not (mc and lo < float(mc.group(1)) < hi):
            continue
        row_age = re.search(r"(just now|\d+\s*(?:second|minute|hour|day|week|month|year)s?\s*ago)",
                            region[mc.end(): mc.end() + 200])
        if not row_age or relative_age_hours(row_age.group(1)) > CC_MAX_AGE_HOURS:
            continue                                   # stale or undated row
        buy[cur] = float(mc.group(1))
    return buy, (f"CashChanger board · {phrase}" if phrase else "via CashChanger")


BOOTHS = [
    # (id matching catalog.json, display name, scraper or None, pending reason, source label)
    ("vasu",           "Vasu Exchange",      scrape_vasu,         None, None),
    ("k79",            "K79 Exchange",       scrape_k79,          None, None),
    ("superrich_th",   "SuperRich Thailand", scrape_superrich_th, None, None),
    ("superrich_1965", "SuperRich 1965",     scrape_sr1965,       None, None),
    ("siam_exchange",  "Siam Exchange",      scrape_siam,         None, "via CashChanger"),
]


def main() -> int:
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    rates, failures = [], 0

    for booth_id, name, scraper, pending, source in BOOTHS:
        entry = {"id": booth_id, "name": name, "ok": False}
        if scraper is None:
            entry["reason"] = pending
        else:
            try:
                buy, site_ts = scraper()
                buy = {k: v for k, v in (buy or {}).items() if v is not None}
                if buy.get("USD"):                          # USD row is the health check
                    entry.update(ok=True, buy=buy, fetchedAt=now)
                    if site_ts:
                        entry["siteTime"] = site_ts
                    if source:
                        entry["source"] = source
                else:
                    entry["reason"] = "marker/rate not found (site changed?)"
                    failures += 1
            except RateUnavailable as exc:
                entry["reason"] = str(exc)                 # handled, not a failure
            except Exception as exc:                       # noqa: BLE001
                entry["reason"] = f"fetch failed: {type(exc).__name__}"
                failures += 1
        rates.append(entry)

    out = {"version": 2, "updated": now, "rates": rates}
    out_path = Path(__file__).resolve().parent.parent / "data" / "booth-rates.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(out, indent=2) + "\n")

    live = [r for r in rates if r["ok"]]
    print(f"wrote {out_path} — {len(live)} live, {failures} failures")
    for r in live:
        print(f"  {r['name']}: {r['buy']}")
    # Exit 0 even with partial failures (stale entries are handled app-side);
    # exit 1 only if NOTHING scraped, so the Action flags total breakage.
    return 0 if live else 1


if __name__ == "__main__":
    sys.exit(main())
