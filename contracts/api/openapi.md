# API Contract

The REST API serves HSDS v3.2.3 JSON and follows the HSDS API reference as its
model. FastAPI generates an OpenAPI document that is compared against the
canonical Open Referral file.

_Spec: Requirements 1.1, 29.3, 29.4, 29.5, 30, 31, 32, 33._

## Canonical OpenAPI (for comparison)

`https://raw.githubusercontent.com/openreferral/specification/3.2/schema/openapi.json`

The FastAPI-generated document is diffed against this file in tests (Req 32.4).

## Endpoints

| Endpoint | Status | Purpose |
|----------|--------|---------|
| `GET /` | Required | Information about the API and its relationship to HSDS |
| `GET /services` | Required | Paginated, filterable list of services (Page schema) |
| `GET /services/{id}` | Required | One fully nested HSDS service; 404 if unknown |
| `GET /organizations`, `GET /organizations/{id}` | Optional | Behind config |
| `GET /service_at_locations`, `GET /service_at_locations/{id}` | Optional | Behind config |
| `GET /taxonomies`, `GET /taxonomies/{id}` | Optional | Behind config |
| `GET /taxonomy_terms`, `GET /taxonomy_terms/{id}` | Optional | Behind config |

POST endpoints are not needed for the MVP.

## `GET /services` query parameters

`page`, `per_page`, `modified_after`, `organization_id`, `taxonomy_term_id`,
`taxonomy_id`, `minimal`, `full`, and (P5 stretch) `search`.

Rules:

- Supplied parameters **combine with AND** — a record must match every supplied
  parameter. _(Req 30.4)_
- No `page` ⇒ return the first page. _(Req 30.5)_
- Invalid parameter type/format ⇒ error response naming the offending parameter,
  no records returned. _(Req 30.6)_
- Zero matches ⇒ a Page with empty `contents` and `empty = true`. _(Req 30.7)_
- `search` is resolved against the OpenSearch index (P5). _(Req 30.8)_
- `metadata` is omitted from listed records by default; it may be included on a
  single-record response where it helps. _(Req 30.9, 31.3)_

## Page schema (list responses)

```json
{
  "total_items": 0,
  "total_pages": 0,
  "page_number": 1,
  "size": 50,
  "first_page": true,
  "last_page": true,
  "empty": true,
  "contents": []
}
```

Internal consistency: `total_pages == ceil(total_items / per_page)`,
`len(contents) <= per_page`, `empty` iff `contents` is empty, and
`first_page`/`last_page` reflect `page_number` relative to `total_pages`.

## Authentication & source scoping

- **Bearer token required on every endpoint.** Missing/invalid ⇒ 401, no record
  data returned. _(Req 33.1, 33.2)_
- A key may be **scoped to a single source**: the caller sees only that source's
  records on every record-returning endpoint, and a by-id request for another
  source's record returns the same not-found response as a non-existent record
  (no disclosure). _(Req 33.3, 33.4)_

The API is REST, implemented in Python + FastAPI. Responses validate against the
pinned HSDS v3.2.3 JSON Schema in tests, including at least one paginated list
against the Page schema. _(Req 32.1–32.3)_

## Internal review API

`/review/*` and `/ingest/reports` are internal endpoints for the review tool. They may
carry operational fields, while `/services` stays strict HSDS. The machine-readable
contract for them is [`openapi.yaml`](openapi.yaml), generated from
`contracts/mock_api.py` with `make openapi`. A test fails if the two differ. Run the
mock with `make mock`.

| Endpoint | Purpose |
|----------|---------|
| `GET /review/records`, `GET /review/records/{id}` | Review queue and one record with findings, clashes, and version |
| `PATCH /review/records/{id}` | Edit fields |
| `GET /review/findings` | List findings |
| `POST /review/findings/{id}/apply`, `/dismiss` | Decide on a finding |
| `GET /review/clashes`, `POST /review/clashes/{id}/resolve` | Decide on a source clash |
| `GET /ingest/reports` | Received, stored, and quarantined counts by source |

Writes send `expected_version`. A stale version returns 409, and an invalid result
returns 422 with field paths. Nothing is saved in either case. The reviewer's identity
comes from the bearer token.

