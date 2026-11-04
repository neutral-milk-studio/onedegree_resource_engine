# AI Maintenance Handoff

This file defines how the detection pipeline and the review layer connect. Change it, or `contracts/findings.py`, through a small separate PR that both owners approve.

## Ownership

| Area | Owner | Files |
|---|---|---|
| Detection | Alina | `maintenance/website_source/sample.py`, `extractor/extract.py`, `comparator/compare.py`, `worker/run_check.py`, `scheduler/eventbridge.py`, `tests/test_detection.py` |
| Storage, review, and scoring | Kiara | `maintenance/findings_store/change_requests.py`, `maintenance/worker/service.py`, `eval/harness.py`, `api/app/routers/app.py`, `api/app/services/proposals.py`, `ui/`, `maintenance/tests/test_worker.py` |
| Shared | Both | `contracts/findings.py`, `seed/`, this file |

## The handoff

```
run_checks -> Finding -> FindingsSink.save(finding) -> change request -> approve -> record and history
```

- Detection creates a `Finding` from `contracts/findings.py` and calls `save`. It doesn't write to the HSDS store or to the findings tables directly.
- Pass the store in as a `FindingsSink`, so detection doesn't need to know which one is running.
- `maintenance/findings_store/change_requests.py` is the production sink, and `memory.py` is for tests. Both apply the same rules through `finding_problems`.
- The AI proposes, and a person applies. Nothing in detection changes a record.

## Finding rules

Every finding needs at least one evidence entry with a URL and a non-empty snippet, and a confidence from 0 to 1. Any other finding raises `FindingRejected`.

| Category | When | `field` | `proposed_value` | `evidence.snippet` | `model_id` |
|---|---|---|---|---|---|
| `discrepancy` | The page states a different or additional fact | Required | Required | Quote from the page | Required |
| `missing_information` | The record lacks the field | Required | Set if the page supplies it | Quote from the page, or what is missing | Required |
| `verified` | The record matches the page | Optional | Not allowed | Quote from the page | Set if a model ran |
| `actively_bad` | The site is gone, its certificate has expired, or the organization appears closed | Optional | Optional | Failure, such as `HTTP 404`, or a quote about the closure | Set if a model ran |
| `check_failed` | The check couldn't complete | Optional | Not used | Failure, such as `Timeout` | Set if a model ran |

Rules for detection:
- A quote must appear exactly in the fetched page. Check this in code, and don't store a finding when it doesn't.
- Compare facts, not wording. A rephrased sentence isn't a discrepancy.
- A page that doesn't mention a fact isn't a discrepancy.
- A failed check is still a finding, never a silent skip.
- Emit exactly one category for each completed check. A check with several differences emits one `discrepancy` finding per field.
- Log model inputs and outputs with the model id. They don't go in HSDS records.

Rules for review:
- The review list hides `verified` by default, but it still updates the last-checked time.
- Only a person's action applies a change. Applying validates the record as HSDS and writes a history entry.

## Write path

Reviewers change records only through the admin API. Its contract is generated from the app into `contracts/api/openapi.yaml`:

| Endpoint | Use |
|---|---|
| `GET /api/admin/maintenance/results`, `GET /api/admin/records/{id}` | Records with their latest check, and one record with its history and change requests |
| `GET /api/admin/change-requests` | List AI findings, provider updates, and source clashes |
| `POST /api/admin/change-requests/{id}/approve` | Apply a proposed value, or an edited one through `overrides` |
| `POST /api/admin/change-requests/{id}/reject` | Reject a change request |
| `PUT /api/admin/records/{id}` | Edit fields directly |

- `/hsds/*` stays read-only HSDS. Internal fields and writes don't belong there.
- Direct edits send `expected_version`. A stale version returns 409, and an invalid result returns 422 with details. Neither saves anything.
- A change request records the value it was based on. If the field has changed since, approving returns 409 with `stale_fields` unless the reviewer forces it.
- A change request can be decided only once. The decision, the record, and its history are written in one transaction.
- The reviewer's identity comes from authentication, never from the request body.
- The sink builds the change request from the stored record, so detection doesn't need to know that shape. A verified result only updates `check_result`.

## Evaluation

`seed/website_content/manifest.json` is the known truth. It currently has three cases: `discrepancy`, `verified`, and `actively_bad`.

`maintenance/eval/harness.py` takes a list of findings and reports the catch rate and false-flag rate by category and field. It doesn't call detection itself, so either side can test without the other. Both owners should announce changes to `seed/`, because both depend on it.

## Working agreement

- Branches: `feature/ai-maintenance-layer` for this handoff, `feature/detection-pipeline` for detection, and `feature/review-findings` for review.
- Each PR gets a review from the other owner.
- First integration target: run the Bayview seed through `run_checks`, `save`, the review list, and apply, ending with an updated record and history. Then run all three seed cases through the harness.

## Open questions

1. **Requirements:** the code cites `.kiro/specs/resource-engine/`, which isn't in the repository. It should be shared so these rules can be checked against it.
2. **List fields:** detection proposes text. `change_requests.py` builds phones, languages, and service areas from it. Hours stay a note until a reviewer edits them.
3. **Categories:** One Degree's final category list is still pending. The working list is in `contracts/findings.py`.
