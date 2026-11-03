# The Resource Engine

A resource data platform for One Degree. It ingests community resource data from
multiple sources, keeps each raw payload exactly as received, converts every
record to **Open Referral HSDS v3.2.3**, and serves it through a REST API. An AI
maintenance layer checks stored records against each organization's website,
flags problems with evidence, and surfaces them to resource specialists in a
unified internal admin app for human review. It replaces the legacy ODRS over
time and does not sync back into it.

This repository is a whole-team monorepo. Three teams build in parallel against
shared, committed contracts: **Data Ingest**, **AI Maintenance**, and **Search**.

> Full requirements, design, and the task plan live in the spec at
> `.kiro/specs/resource-engine/`.

## Pinned HSDS version

**HSDS v3.2.3.** The vendored JSON Schema files and the exact Open Referral
release tag are under [`contracts/hsds/`](contracts/hsds/README.md).

## Repository layout

| Path | Owner | Contents |
|------|-------|----------|
| `contracts/` | whole team (protected) | the five shared contracts: HSDS data, adapter, store, API, findings + website source |
| `shared/` | whole team | HSDS validation, deterministic uuid5, DB helpers |
| `seed/` | whole team | seeded HSDS dataset + sample website content (seeded discrepancies/failures) |
| `ingest/` | Data Ingest | adapters, raw store, transformer/validator, pipeline |
| `maintenance/` | AI Maintenance | worker, website source, extractor, comparator, findings store, scheduler, eval |
| `api/` | Search | FastAPI app serving HSDS JSON |
| `search/` | Search | OpenSearch indexer + query (P5 stretch) |
| `ui/` | shell owner + each team | one React admin app (Records & Review, Search, Ingest) |
| `infra/` | whole team (protected) | Terraform for the optional ECS deploy |
| `.github/` | whole team (protected) | CI gates, CODEOWNERS, PR/task-brief/reviewer templates |
| `docs/` | whole team | guardrails + decisions |

## Run it locally

Local Docker Compose is the default, acceptable target (AWS deployment is
optional — see `infra/`).

```bash
docker compose up --build
```

Services: API on `http://localhost:8000`, admin UI on `http://localhost:5173`,
Postgres on `5432`, OpenSearch on `9200`, S3 (LocalStack) on `4566`. The AI
maintenance worker defaults to the **sample** website source (no network).

Load the shared seed dataset (dry-run validates without a database):

```bash
python -m seed.load_seed --dry-run
```

## Run the tests

```bash
pip install pytest hypothesis jsonschema ruff
pytest -q          # unit, integration, and property tests (Hypothesis)
ruff check .       # lint
```

CI (`.github/workflows/ci.yml`) runs four gates on every PR — HSDS schema
validation, secret scanning, tests, and lint — all of which must pass to merge
into protected `main`.

## Team decisions

Recorded in [`docs/decisions.md`](docs/decisions.md). Highlights:

- **Organization-description gap:** store a fallback note and flag the record as
  *missing information* (AI can later supply a real description from the website).
- **Schedule time-zone offsets:** interpret bare local times as
  `America/Los_Angeles` and attach the offset in effect on the schedule's date
  (DST handled by the zone); a midday closure becomes two intervals.
- **Datastores:** PostgreSQL for HSDS; a separate `ops` schema for operational
  data; S3 for the raw store; OpenSearch for the search index.

## Documented HSDS extensions

The single `x_resource_engine` extension, documented in
[`contracts/hsds/extensions.md`](contracts/hsds/extensions.md).

## AWS services used

- **Amazon S3** — raw store (payloads as received).
- **Amazon Bedrock** (Anthropic models) — fact extraction and, for the search
  stretch, embeddings.
- **Amazon OpenSearch Service** — search index (P5 stretch).
- **Amazon EventBridge Scheduler** — recurring AI maintenance checks (never
  ingest; ingest is on demand).
- **Amazon Textract** — optional PDF extraction (spreadsheet/PDF stretch).
- **Amazon RDS for PostgreSQL** / **ECS Fargate** / **AWS Secrets Manager** —
  the optional deploy path (`infra/`).

## Governance

Branch per task → PR into protected `main` → four CI gates + impartial reviewer
verdict → human approval on protected surfaces (via `.github/CODEOWNERS`).
Non-negotiable rules: [`docs/agents-guardrails.md`](docs/agents-guardrails.md).
