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
  subgraph AI["maintenance/"]
    W[Website source<br/>sample pages → live] --> X[Extract facts<br/>Bedrock or offline rules]
    X --> C[Compare in code<br/>facts, not wording] --> F[(ops.findings<br/>ops.checks)]
    X <--> K[(ops.llm_cache<br/>by content hash)]
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
| API | `api/app/` | `GET /services`, `GET /services/{id}`, `POST /ingest/{source}`, `GET /ingest/runs`, `GET /ingest/runs/{id}/quarantine`, `GET /metrics`, `GET /audit`. |
| AI maintenance | `maintenance/` | Website source → extractor (Amazon Bedrock Converse, or offline rules) → comparator → findings. Harm priority, trust score, apply. |
| Conflicts | `ingest/pipeline/conflicts.py`, `api/app/routers/conflicts.py` | Cross-source matching after every ingest; resolve by choosing the winning value. |
| Add a partner | `ingest/adapters/spreadsheet.py`, `ingest/mapping.py`, `ingest/partners.py`, `api/app/routers/sources.py` | Upload CSV → suggested mapping with confidence → confirm → import. |
| Review API | `api/app/routers/review.py`, `maintenance.py` | `POST /maintenance/run`, `GET /maintenance/checks`, `GET /review/findings`, `POST /review/findings/{id}/apply`, `POST /review/findings/{id}/dismiss`, `GET /review/records/{id}/history`. |
| Search | `search/`, `api/app/routers/search.py` | `GET /search?q=`: plain-language query plan → Postgres full-text search behind a swappable `SearchBackend`. |
| Admin UI | `ui/` | React + Vite + TypeScript + Tailwind. Overview (live metrics, findings chart, sources, recent activity), plus the review screen. Light/dark/device theme. |

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

API docs: `http://localhost:8000/docs`. Admin UI: `http://localhost:5173`. Sign in with any
email and the bearer token as the password (`local_dev_token` locally). LocalStack,
OpenSearch and the worker are behind a profile: `docker compose --profile full up`.

Without Docker, against any Postgres:

```bash
make setup
export RE_DATABASE_URL=postgresql://resource_engine:local_dev_only@localhost:5432/resource_engine
export RE_BEARER_TOKEN=local_dev_token
uv run python -m ingest.pipeline.run --all
make api
```

The admin UI on the host:

```bash
cd ui && npm install
VITE_API_MODE=live npm run dev    # against the API on :8000 (proxied by Vite)
npm run dev                       # demo mode: simulated data, labelled as such
npm test                          # UI tests (vitest)
```

The review API contract mock still runs on its own with `make mock`.

## AI maintenance and review

1. **Website source** (`RE_WEBSITE_SOURCE=sample`): `data/sample_websites/manifest.json` maps
   9 stored services to fictional `.example` URLs and a page or a simulated failure. No
   network. A URL without a page is a failed check, never a guess.
2. **Extract** what the page states (phone, hours, service area, eligibility, languages,
   accessibility, closure), each with the exact quote. With `BEDROCK_MODEL_ID` set this is
   one Bedrock Converse call per record returning strict JSON (validated with Pydantic);
   otherwise a labelled offline rules extractor (`model_id: offline-rules`) is used.
3. **Compare in code**: a fact whose quote isn't on the page is dropped; values are
   normalized (phone digits, day/time slots, case, "accessible yes/no") so rewording is never
   flagged; a page that doesn't mention a fact is not a discrepancy.
4. **Categories**: `verified`, `discrepancy`, `missing_information`, `actively_bad` (site gone:
   NXDOMAIN, 404/410, or a closure notice), `check_failed` (blocked, timeout, unreadable).
   `suspicious` when the risk check below reaches the flag threshold.
5. **Harm priority**: actively bad > suspicious > discrepancy in phone/hours/address > other
   discrepancy > missing information > check failed.
6. **Trust score** (0-100) with a badge like "Verified 2 days ago" or "1 open issue": verified
   recently is high and decays with age; each open finding lowers it by its harm.
7. **Review**: Apply checks the record version (stale → 409), applies the value, appends an
   HSDS `metadata` entry, re-validates the served record (invalid → 422 with paths, nothing
   saved), saves an override so re-ingest keeps the fix, and writes the audit log (who, field,
   old, new, when, source of change). Keep current is audited too.

8. **Suspicious-listing detection** (`maintenance/risk.py`, same check, no extra model call
   required). The system never decides alone: it only ever flags a listing for a specialist
   to review. Deterministic rules score a page 0-100 and flag it (`suspicious`) once the score
   reaches `RE_SUSPICIOUS_THRESHOLD` (default 40, one settings file:
   `maintenance/suspicious_settings.py`):
   - **Strong** (40 each): a fee demanded before help; a request for sensitive data (SSN,
     bank account, card, password).
   - **Medium** (20 each): urgency or guaranteed-approval pressure; claims to be official or
     government on a non-.gov domain; the page demands a fee the record doesn't have.
   - **Weak** (10 each): the domain doesn't resemble the organization's name, is a URL
     shortener, or is a free-hosting subdomain; the domain was registered in the last 90 days
     (RDAP, cached, 3s timeout, fails open and is skipped offline); the organization isn't in
     `data/registry/known_orgs.json` (absence alone never flags); contact is messaging-app or
     free-email only with no physical address on file (a PO box counts as an address).

     Phrasing like "no fee", "free of charge", "we never ask for payment", or "application fee
     waived" is recognized and never flags. Each signal carries a plain-language reason and,
     where it comes from the page, a short quote (never the full page, never logged beyond
     that quote).
   - With `BEDROCK_MODEL_ID` set, one additional Bedrock call (temperature 0, strict JSON) may
     add extra reasons grounded in the page's own quotes -- it can never raise or lower the
     rules score, and is skipped silently if AWS isn't configured or the call fails.

   A flagged listing is excluded from every public path (search, semantic search, SMS, the
   map, the public API) until a specialist decides; the signals themselves are never exposed
   publicly. In the review queue, a specialist sees the score, each signal's reason, the
   evidence quote and the page link, and picks one of three audited actions: **Hide from
   search** (the default -- keeps it out of every public path, record unchanged), **Mark
   safe** (requires a short note; stops future checks from re-flagging that listing or its
   domain), or **Confirm and remove** (applies an inactive status). Everywhere -- UI, API,
   logs, tests -- this says "flagged for review" or "suspicious", never "scam" or "fraud" as a
   conclusion, and never names an individual.

```bash
make check                        # run website checks against the running stack
make eval-suspicious              # precision/recall of the rules against data/eval/suspicious_cases.json
```

### Try it: Quick Rent Rescue

1. `make up` then `make ingest` (loads the fictional "Quick Rent Rescue" community listing,
   which asks for a fee, an SSN and guarantees approval) and `make check`.
2. Open the review queue: **Quick Rent Rescue** appears as **Suspicious**, ranked just after
   actively-bad listings, with its risk score, each signal's plain-language reason, the
   evidence quote, and a link to the page.
3. Click **Hide from search**.
4. Search "I can't pay rent" (web search, or text it to the SMS number) -- Quick Rent Rescue
   never appears. The control listing, **Oak Street Legal Clinic** (explicitly "no processing
   fee... application fee waived"), stays unflagged throughout and is still searchable.

## Conflicts between sources

After every ingest, organizations are matched across sources by normalized name or website
domain, and phone, email and website are compared after normalizing (so
`http://www.coastsidehope.org` and `https://coastsidehope.org` are the same, and two sources
sharing any phone number agree). The real samples produce one conflict: **Coastside Hope's
email** (`admin@` in 211 NDP, `assistance@` in ODRS). On the Conflicts page a specialist picks
one side or types another value; it is applied to every service of the organization in the
losing source(s) with HSDS metadata, an override (re-ingest keeps it) and the audit log
(`change_source: conflict`). API: `GET /conflicts`, `POST /conflicts/detect`,
`POST /conflicts/{id}/resolve`.

The demo also has a fictional **community submissions** source
(`ingest/adapters/community.py` + one entry in `config/sources.yaml`), which brings in the fake
rent relief listing and a harmless community fridge. It shows that adding a source is one
module plus configuration.

The planted problems and what each should produce are in the manifest; the test
`test_evaluation_every_planted_problem_is_caught_and_nothing_else` asserts a 100% catch rate
with no false flags on that set.

## Add a partner (spreadsheets)

On the **Add a partner** page a specialist names the partner and uploads a CSV (up to 2 MB).
`POST /sources/preview` stores the upload and suggests which field each column holds, with
a confidence and a plain reason:

- With `BEDROCK_MODEL_ID` set: one Bedrock Converse call sees the headers and 5 sample rows
  and returns strict JSON, validated (every column, known fields, confidence 0-1). An invalid
  or failed reply falls back to the offline rules and says so.
- Otherwise: offline rules from header synonyms plus value patterns (phones, emails, ZIPs,
  web addresses, hours, `Key: value | Key: value` packed columns).

The specialist checks or changes each column (low-confidence ones are marked "please check")
and confirms. `POST /sources/confirm` saves the mapping (`ops.source_mappings`, audited with
what was changed) and imports the rows through the normal pipeline: raw file saved as
uploaded, every row validated, stored or quarantined with the reason. The page shows rows
received / stored / quarantined, parts held back with plain-language reasons, and the time
from upload to stored. The partner then re-runs like any source: `POST /ingest/{source}`.

The sample `data/samples/spreadsheet/county_food_pantries.csv` (fictional) imports as
7 received, 7 stored, 0 quarantined, with 2 parts held back: Vacaville Mobile Pantry's address
(no street) and Rio Vista's phone ("call the church office"). Its phones come in five formats
and all normalize to `(707) 555-01xx`; "1st and 3rd Friday" hours stay as text instead of being
guessed into a weekly schedule.

## Search

`GET /search?q=` takes plain language ("I just lost my job and can't pay rent") and returns
ranked active services with their trust badge ("Verified 2 days ago", "1 open issue",
"Not checked yet"), a highlighted snippet, phone and website.

- **Query plan** (`search/query/intent.py`): the person's own words, minus filler words, plus
  service words for recognized needs (rent → rental, housing, eviction, utilities, financial
  assistance; job loss → employment, unemployment, cash assistance…). Own words rank higher.
  The page says what was searched for and why.
- **Backend** (`search/query/search.py`): Postgres full-text search over a derived
  `search_text` column (name, organization, description, eligibility, service area,
  languages, city, taxonomy terms) with a GIN index, prefix matching and `ts_headline`
  snippets. Query text reaches SQL only as bound parameters. `SimpleSearch` does the same in
  Python for SQLite (tests). Both implement `SearchBackend`, so OpenSearch or Bedrock-embedding
  semantic search plugs in without touching the API or UI.
- **Never returned**: listings with an open `suspicious` or `actively_bad` finding, and inactive
  ones. The response counts how many matching listings were hidden.

## Cost per record

Measured on the sample pages: the prompt (instructions + page text) is about **400-470
input tokens**; the reply is about 150-300 output tokens. Pages are capped at 12,000
characters (about 3,300 input tokens) for real sites.

| Case | Input tokens | Output tokens | Cost per check at $3 / $15 per million tokens |
|---|---|---|---|
| Sample page | ~450 | ~250 | ~$0.005 |
| Typical real page | ~2,500 | ~300 | ~$0.012 |
| Largest page (cap) | ~3,300 | ~400 | ~$0.016 |
| Page unchanged since last check | 0 (cache hit) | 0 | $0 |

Prices are configuration (`BEDROCK_INPUT_USD_PER_MTOK`, `BEDROCK_OUTPUT_USD_PER_MTOK`); set
them to the published Amazon Bedrock price of the model you choose. A smaller model at about
$1 / $5 per million tokens brings a typical check to about $0.004. With content-hash caching,
only pages that changed cost anything: checking 10,000 records weekly with 10% of pages
changing is roughly 1,000 model calls, about $12 a week at the $3 / $15 rate. The dashboard
shows the real average from `ops.checks`.

## Scaling plan

- **Pagination everywhere**: `/services` and the review list are paginated; ingest streams
  page by page and writes each raw page before parsing.
- **Queue the checks**: `POST /maintenance/run` runs inline for the pilot. In AWS, an
  EventBridge schedule puts due record ids on **SQS**; Fargate/Lambda workers run
  `check_one` per message, so checks scale out and a failure retries one record, not a batch.
- **Only re-check what changed**: the page text hash and the record facts key the model
  cache, so unchanged pages cost nothing; store `ETag`/`Last-Modified` for live fetches and
  skip downloading unchanged pages altogether.
- **Daily NDP updates using `lastUpdated`**: fetch with the `lastUpdated` watermark of the
  previous successful run (the adapter `fetch(since=...)` hook), so a daily run moves only
  changed records; deterministic ids make it an update in place.
- **Search** moves from Postgres full-text to OpenSearch (and Bedrock embeddings for semantic
  ranking) behind the `SearchBackend` interface; `search/indexer/build_index.py` already builds
  the text to index from the HSDS store, which stays the single source of truth.

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
