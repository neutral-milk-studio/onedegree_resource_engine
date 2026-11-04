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
| `ingest/` | Data Ingest | adapters (`config/sources.yaml`), raw store, transformer/validator, pipeline |
| `maintenance/` | AI Maintenance | worker, website source, extractor, comparator, findings store, scheduler, eval |
| `api/` | Search | FastAPI app serving HSDS JSON |
| `search/` | Search | OpenSearch indexer + query (P5 stretch) |
| `ui/` | shell owner + each team | one React admin app (Records & Review, Search, Ingest) |
| `infra/` | whole team (protected) | Terraform for the optional ECS deploy |
| `.github/` | whole team (protected) | CI gates, CODEOWNERS, PR/task-brief/reviewer templates |
| `docs/` | whole team | guardrails + decisions |

## The problem

One Degree (ODRS) and 211 (NDP) describe the same community services in different
formats, listings go stale, and resource specialists verify them by hand. The Resource
Engine keeps **one HSDS v3.2.3 store** as the single source of truth, checks listings
against organization websites with AI, and gives specialists a review dashboard.

## Architecture

```mermaid
flowchart LR
  subgraph Sources
    ODRS[ODRS export]
    NDP[211 NDP pages]
    CSV[Partner spreadsheet]
  end
  subgraph Ingest["ingest/"]
    A[Adapters<br/>config/sources.yaml] --> R[(Raw store<br/>var/raw → S3)]
    R --> M[Map → validate<br/>HSDS v3.2.3]
    M -->|valid| S
    M -->|invalid| Q[(ops.quarantine<br/>with reasons)]
  end
  ODRS & NDP & CSV --> A
  S[(hsds.services<br/>hsds.objects<br/>Postgres)]
  subgraph AI["maintenance/ (step 3)"]
    W[Website source] --> B[Bedrock check] --> F[(ops.findings)]
  end
  S --> AI
  S & F & Q --> API[FastAPI<br/>bearer auth]
  API --> UI[React admin UI]
  API --> Partners[HSDS consumers]
```

| Layer | Code | What it does |
|---|---|---|
| Adapters | `ingest/adapters/` + `config/sources.yaml` | One mapper module per source (`odrs.py`, `ndp.py`). Adding a source = one module + one config entry. |
| Pipeline | `ingest/pipeline/run.py` | fetch → raw save → map → validate (each object, then the nested service as served) → store or quarantine. Deterministic UUIDv5 ids, so re-runs update. |
| Store | `shared/models.py`, `api/app/store_pg.py` | `hsds.objects` (every HSDS object), `hsds.services` (nested HSDS document + key columns), `hsds.id_map`; `ops.ingest_runs`, `ops.quarantine`, `ops.audit_log`. |
| API | `api/app/` | `GET /services`, `GET /services/{id}`, `POST /ingest/{source}`, `GET /ingest/runs`, `GET /ingest/runs/{id}/quarantine`. |

## Run it locally

Everything (Postgres, API, admin UI) with Docker:

```bash
cp .env.example .env
docker compose up --build -d
make ingest                      # loads ODRS + NDP samples; prints counts and quarantine reasons
```

Then call the API (token is `RE_BEARER_TOKEN`, `local_dev_token` locally):

```bash
curl -H "Authorization: Bearer local_dev_token" "http://localhost:8000/services?per_page=5"
curl -H "Authorization: Bearer local_dev_token" -X POST http://localhost:8000/ingest/ndp
curl -H "Authorization: Bearer local_dev_token" http://localhost:8000/ingest/runs
```

API docs: `http://localhost:8000/docs`. Admin UI: `http://localhost:5173`. LocalStack,
OpenSearch and the worker are behind a profile: `docker compose --profile full up`.

Without Docker, against any Postgres:

```bash
make setup
export RE_DATABASE_URL=postgresql://resource_engine:local_dev_only@localhost:5432/resource_engine
export RE_BEARER_TOKEN=local_dev_token
uv run python -m ingest.pipeline.run --all
make api
```

The review API contract mock still runs on its own with `make mock`.

## Data decisions

Full list in [`docs/decisions.md`](docs/decisions.md). The ones that shape the data:

- **Organization description placeholder.** ODRS has no organization description and HSDS
  requires one. We store `"<name>. Description not available."` and tag the organization
  `x_resource_engine.flags: ["missing_information:description"]`, so the gap is visible
  and fixable through review instead of quarantining an otherwise good record.
- **Pacific time offset rule.** Sources give local wall-clock hours with no offset. We store
  the local time plus `x_resource_engine.timezone` (default `America/Los_Angeles`) and add
  the UTC offset only when a record is served, for the date served (or the schedule's
  `valid_from`). `09:00` is served as `09:00-07:00` in July and `09:00-08:00` in December.
- **Quarantine rules.** Nothing invalid is stored, nothing is dropped. Each object is
  validated against the vendored HSDS v3.2.3 schema (plus tabular-required fields and a
  "phone has digits" quality rule), then each nested service is validated in its served
  form. If a record can't be mapped, or its organization or service is invalid, the
  **record** is quarantined. If only a child object is invalid (e.g. an NDP "Various
  Locations" address with no city), that **object** is quarantined with its reason and the
  rest of the record is stored. Children of a quarantined parent are quarantined too.
- **Counts reconcile** at both levels: `received == stored + quarantined` (records) and
  `objects_total == objects_kept + objects_quarantined`. A run that doesn't reconcile fails.
- **NDP `lastUpdated`** is a date; HSDS wants a date-time, so it becomes midnight UTC.
  `unavailable_at` set on an ODRS opportunity → `status: inactive`.

Sample results: ODRS 50 received / 50 stored / 0 quarantined; NDP 20 / 20 / 0 records with
7 objects held back (4 addresses without city/postal code, 3 web addresses in phone fields).

## Run the tests

```bash
make test    # Unit, integration, and property tests
make lint    # Linting and formatting
```

CI (`.github/workflows/ci.yml`) runs four gates on every PR: HSDS schema validation,
secret scanning, tests, and lint.

## Team decisions

Recorded in [`docs/decisions.md`](docs/decisions.md). Highlights:

- **Organization-description gap:** store a fallback note and flag the record as
  *missing information* (AI can later supply a real description from the website).
- **Schedule time-zone offsets:** store local time plus an IANA time zone, and
  calculate the offset when a record leaves the system; a midday closure becomes two
  intervals.
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
