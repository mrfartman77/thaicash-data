# thaicash-data

Public data feed for the ThaiCash iOS app.

- `data/booth-rates.json` — USD→THB cash buy rates scraped from Bangkok
  exchange-chain rate boards every 2h (Bangkok business hours) by the
  GitHub Action in this repo. Each entry sanity-gated; a broken scraper
  marks a booth stale, never wrong.
- `catalog.json` — the app's method/fee catalog (fees, caps, booth
  directory). The app ships a bundled seed and prefers this hosted copy.
- `scrapers/booth_rates.py` — stdlib-only Python, one isolated parser per
  chain. PRs welcome for the pending chains (SuperRich TH/1965, Siam).

## License

Data is **CC BY-NC 4.0** (attribution required, non-commercial only —
commercial use needs a separate license); code is all rights reserved.
See [LICENSE.md](LICENSE.md).
