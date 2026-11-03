# Team Decisions — The Resource Engine

This file records the decisions the specification leaves to the team, plus the
collaboration model. It is linked from the root `README.md`. Where the spec says
"teams decide and document," the decision lives here.

_Spec: Requirements 1.4, 6.1, 14.3, 15.3._

## 1. Collaboration & integration model

- **Monorepo.** One shared repository holds all three streams (Data Ingest, AI
  Maintenance, Search), the shared contracts, the unified admin UI, infra, and
  governance. _(Req 2.1)_
- **Branch per task.** Every task is developed on its own branch named
  `feature/<stream>-<short-slug>` (e.g. `feature/ingest-ndp-adapter`) and
  integrated through a pull request. No direct commits to `main`. _(Req 2.2)_
- **Protected `main`.** `main` is protected: changes reach it only through a
  reviewed, CI-green pull request. _(Req 2.3)_

### Branch-protection settings to apply on the GitHub remote

Apply these on `main` (Settings → Branches → Branch protection rules):

1. Require a pull request before merging.
2. Require status checks to pass before merging — select the CI job(s) from
   `.github/workflows/ci.yml` (HSDS schema validation, secret scan, tests, lint).
3. Require review from Code Owners (`.github/CODEOWNERS`) — this forces human
   approval on the protected surfaces (contracts, infra, CI, guardrails, secrets).
4. Dismiss stale approvals on new commits.
5. Do not allow bypassing the above (including for admins) during the hackathon.
6. Restrict direct pushes to `main`.

> The repo was initialized before this scaffold. These settings are configured
> on the GitHub remote by a maintainer; this document is the source of record.

## 2. Language & frameworks

- **Python + FastAPI** for all backend services (default from the Technical
  Document). **React + TypeScript + a data grid** for the admin UI.
- **Hypothesis** for property-based tests; **pytest** for unit/integration.

## 3. Datastores

- **HSDS store:** PostgreSQL, starting from Open Referral's published
  SQL/PostgreSQL schema generated from HSDS. _(Req 11.4)_
- **Operational data** (findings, evidence, review state, model logs, user
  accounts): a separate `ops` schema / separate tables in the same Postgres
  instance — never mixed into the HSDS tables. _(Req 24.1)_
- **Raw store:** Amazon S3 (LocalStack locally), payload written exactly as
  received, keyed `source/<fetched_at ISO8601>/<native_id|batch_seq>.json`. _(Req 10)_
- **Search index:** Amazon OpenSearch, a derived read model rebuilt from the
  HSDS store (P5 stretch). _(Req 36)_

## 4. HSDS version

- Pinned to **HSDS v3.2.3**. The exact Open Referral `specification` release tag
  and the vendored JSON Schema files live under `contracts/hsds/`. _(Req 1.2, 1.3)_

## 5. Organization-description gap

HSDS requires `organization.description`, but some sources do not provide one.

**Decision (default): fallback note + flag.** When a source record lacks an
organization description, store a plain fallback (the organization name plus a
note that no description is available), mark the field in
`x_resource_engine.ai_generated_fields` when later AI-generated, and flag the
record in the **missing information** category so a specialist and/or the AI
maintenance layer can supply a real description from the website.

> Rationale: keeps ingest from quarantining otherwise-valid records on a single
> missing field, while making the gap visible and fixable through the normal
> review flow. Teams may switch to "AI-generate on ingest" once the maintenance
> extractor is reliable. _(Req 14.1, 14.2, 14.3)_

## 6. Schedule time-zone offset rule

HSDS `opens_at`/`closes_at` should carry a UTC offset; source data often has
local Pacific times with no offset, and the correct offset shifts with daylight
saving.

**Decision:** interpret bare local times as **America/Los_Angeles** and attach
the offset that applies on the schedule's effective date (so DST transitions are
handled by the zone, not a fixed `-08:00`/`-07:00` constant). A midday closure in
free text is converted to **two** schedule intervals, never one spanning the gap.
_(Req 15.1, 15.2, 15.3)_

## 7. `unavailable_at` → `service.status` mapping

- `null` → `active`.
- A past date → `inactive` (the opportunity is no longer available). A future
  date is treated as still `active` until it passes.

## 8. Deployment target

- **Local Docker Compose is the default and acceptable target.** Terraform under
  `infra/` describes the optional ECS Fargate path; deploying to AWS is optional
  and never required. _(Technical Document §3.3)_
