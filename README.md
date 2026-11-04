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

> Read [known limitations](docs/known-limitations.md) before deploying.

## Pinned HSDS version

**HSDS v3.2.3.** The vendored JSON Schema files and the exact Open Referral
release tag are under [`contracts/hsds/`](contracts/hsds/README.md).

## Repository layout

| Path | Contents |
|------|----------|
| `api/app/` | FastAPI app: the UI's API under `/api`, the HSDS API under `/hsds`, services (store, change requests, ingest, chat, search, documents, seekers, users), and jobs |
| `ui/` | Web UI for help seekers, providers, and admins. No build step: the API serves these files |
| `ingest/pipeline/transform/` | Source mappers and HSDS validation for ODRS and NDP |
| `maintenance/` | Website source, fact extractor, comparator, check loop, and findings sink |
| `contracts/` | Shared contracts: pinned HSDS schemas, findings, and the generated OpenAPI file |
| `shared/` | Database layer, HSDS validation, and deterministic ids |
| `seed/`, `tools/` | Shared detection cases, and the demo data generator |
| `data/` | Real sample payloads, kept exactly as received |
| `infra/terraform/`, `.github/` | AWS infrastructure, CI gates, and the manual deploy workflow |
| `docs/` | Requirements, decisions, the deployment guide, and known limitations |

## Run it locally

Install [uv](https://docs.astral.sh/uv/getting-started/installation/), then:

```bash
make setup
make demo    # http://127.0.0.1:8000
```

`make demo` loads the real samples and generated data, creates demo accounts, and runs one
maintenance pass. It needs no database, AWS account, or keys. Model calls use an offline stand-in,
so demo findings don't show real extraction quality.

| Persona | Email | Password |
|---------|-------|----------|
| Admin | `admin@demo.org` | `demo-admin-1` |
| Provider | `provider1@demo.org` | `demo-provider-1` |
| Help seeker | `ana@demo.org` | `demo-seeker-1` |

To run on PostgreSQL 16, closer to AWS, use `docker compose up --build`. The app is on
`http://localhost:8000`. Run a maintenance pass with
`docker compose --profile jobs run --rm worker`.

## Run the tests

```bash
make test             # Python tests
make lint             # Linting and formatting
cd ui && npm ci && npm test   # UI tests
```

CI (`.github/workflows/ci.yml`) also scans for secrets, builds both images, and validates Terraform.

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
- **Aurora PostgreSQL Serverless v2** / **ECS Fargate** / **AWS Secrets Manager** /
  **Application Load Balancer** / **Amazon ECR** / **CloudWatch Logs** — the deploy path
  (`infra/terraform`, [deployment guide](docs/deployment.md)).
- **Amazon Cognito** — provisioned; the default deployment uses local accounts until it's tested.

## Governance

Branch per task → PR into protected `main` → four CI gates + impartial reviewer
verdict → human approval on protected surfaces (via `.github/CODEOWNERS`).
Non-negotiable rules: [`docs/agents-guardrails.md`](docs/agents-guardrails.md).
