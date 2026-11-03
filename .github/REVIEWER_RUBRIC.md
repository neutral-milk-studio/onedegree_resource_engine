# Reviewer Rubric

The impartial reviewer agent posts a verdict on every PR, evaluated against each
item below. A PR touching only routine paths with all CI gates green and no unmet
rubric item may merge without human approval. A PR touching a protected surface
always also needs a human CODEOWNER approval.

_Spec: Requirements 4.1, 4.2, 6.3._

## Verdict

State one of: **APPROVE** (no unmet items), **REQUEST CHANGES** (one or more unmet
items — list them), or **NEEDS HUMAN** (touches a protected surface, or the change
is risky/ambiguous).

## Rubric items

1. **Requirements coverage** — the PR implements the requirement IDs it claims and
   does not silently expand scope.
2. **HSDS validity** — any record the change stores, serves, or exchanges is valid
   HSDS v3.2.3. No path stores or serves an invalid record.
3. **Contracts honored** — code conforms to the committed contracts (adapter,
   store, api, findings, website-source). No contract was changed in a routine PR.
4. **Operational-data separation** — findings/evidence/review state/model logs are
   not written into the HSDS tables; provenance that travels uses
   `x_resource_engine`.
5. **Evidence + human-in-the-loop** — AI proposals carry a non-empty evidence
   snippet; applied changes are human-initiated and write an HSDS metadata entry.
6. **Secrets & data handling** — no secrets, production keys, or sample attachment
   URLs committed; embedded credentials stripped; permitted data sources only.
7. **Determinism & idempotency** — ids are UUIDv5 from source + native id;
   re-ingest updates rather than duplicates (where applicable).
8. **Tests** — tests exist for the behavior changed; each mapped correctness
   property has a property-based test; tests actually run in CI.
9. **Counts & quarantine** (ingest PRs) — received == stored + quarantined; no
   record dropped or stored invalid.
10. **Readability & handoff** — code is clear and documented enough for One Degree
    to operate; any new AWS service or HSDS extension is documented.

## Protected-surface check

If the PR changes `/contracts/**`, `/infra/**`, `/.github/workflows/**`,
`/.github/CODEOWNERS`, `/docs/agents-guardrails.md`, or any secrets/data-handling
config, the verdict is **NEEDS HUMAN** regardless of the other items.
