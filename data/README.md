# Source data

Files are kept exactly as received (raw store, requirement R1.3). Do not edit them; transform code reads from here.

| File | Source | Records | Notes |
|------|--------|---------|-------|
| samples/ODRS/BayesExport_20250729.json | ODRS API (`data.1degree.org/v1/opportunities`) export | 50 opportunities | Page 1 of 3; API reports 127 total. Top-level keys: `paging`, `detected_language`, `opportunities` |
| samples/NDP/national-data-platform-sample-1.json | 211 NDP sample (211bayarea) | 10 organizations, 20 services | `input.skip=0, take=10`; NDP reports 1068 available |
| samples/NDP/national-data-platform-sample-2.json | 211 NDP sample (211bayarea) | 10 organizations, 16 services | `input.skip=10, take=10` |

ODRS pages 2–3: run `python scripts/fetch_odrs.py` (needs network and possibly `ODRS_API_KEY`). It saves `odrs_page<N>_<date>.json` next to the sample. Never commit API keys; put them in `.env` (already gitignored).

Shapes:
- ODRS: `opportunities[]`, each with `id`, `title`, `organization`, `locations[]`, `properties{}`, `phones[]`, `schedule` and more (technical doc section 4).
- NDP: `data[]` = organizations, each with `services[]`, `phones[]`, `contacts[]` and more. Ids look like `211bayarea-10595130`.
