# Source data

Files are kept exactly as received (raw store, requirement R1.3). Do not edit them; transform code reads from here.

| File | Source | Records | Notes |
|------|--------|---------|-------|
| samples/ODRS/BayesExport_20250729.json | ODRS API (`data.1degree.org/v1/opportunities`) export | 50 opportunities | Page 1 of 3; API reports 127 total. Top-level keys: `paging`, `detected_language`, `opportunities` |
| samples/NDP/national-data-platform-sample-1.json | 211 NDP sample (211bayarea) | 10 organizations, 20 services | `input.skip=0, take=10`; NDP reports 1068 available |
| samples/NDP/national-data-platform-sample-2.json | 211 NDP sample (211bayarea) | 10 organizations, 16 services | `input.skip=10, take=10` |
| raw/odrs/sample/ | ODRS staging API (`rbg.1degree.org:3002`), fetched 2026-11-03 | 500 opportunities, 200 organizations | First 5 / 2 pages at 100 per page; API reports 24,129 opportunities and 6,774 organizations. ~2 MB. See `manifest.json` |

Live ODRS data: `python3 scripts/fetch_odrs.py` pulls opportunities and organizations from the One Degree staging API (`rbg.1degree.org:3002`) into `raw/odrs/<--out, default UTC fetch time>/`, one file per page plus `manifest.json` with counts. It needs `ODRS_API_KEY=...` in `.env` at the repo root (gitignored). Never commit the key; the script scrubs it from saved URLs. Options: `--resource`, `--max-pages N` (default unlimited; a stopped resource is marked `"truncated": true` with its `next_page` in the manifest), `--per-page N` (the API honours up to at least 100), `--out NAME`.

- **Committed sample** `raw/odrs/sample/` was fetched with:
  ```
  python3 scripts/fetch_odrs.py --out sample --resource opportunities --per-page 100 --max-pages 5
  python3 scripts/fetch_odrs.py --out sample --resource organizations --per-page 100 --max-pages 2
  ```
- **Full fetches** go to `raw/odrs/full/`, which is gitignored (about 95 MB; will move to S3 later):
  ```
  python3 scripts/fetch_odrs.py --out full --per-page 100
  ```

One Degree taxonomy is three levels: area (highest) → category → tag (most specific).

Shapes:
- ODRS: `opportunities[]`, each with `id`, `title`, `organization`, `locations[]`, `properties{}`, `phones[]`, `schedule` and more (technical doc section 4).
- NDP: `data[]` = organizations, each with `services[]`, `phones[]`, `contacts[]` and more. Ids look like `211bayarea-10595130`.
