# Agents Guardrails — Non-Negotiable Rules

Every agent and developer working in this repo follows these rules. They are
enforced by CI gates where mechanically checkable, by the reviewer agent against
`REVIEWER_RUBRIC.md`, and by human CODEOWNER approval on protected surfaces. This
file is itself a protected surface — changing it requires human approval.

_Spec: Requirements 6.1, 43, 44, 45, 46, 12, 23, 24, 26._

## 1. HSDS validity at every boundary

- Every record that is **stored, served, or exchanged** is valid HSDS **v3.2.3**.
- Validate at: transformer output before storage, API responses (in tests), any
  export, and every admin-UI save.
- A record that cannot be made valid is **quarantined with the reason** — never
  dropped, never stored invalid.

## 2. Deterministic ids & idempotency

- Every HSDS id is `uuid5(source_name, native_id)`.
- Re-ingesting a source **updates** existing records; it never duplicates.
- Keep the HSDS-id → source-record map (`id_map`) and record provenance in
  `x_resource_engine.source`.

## 3. Raw data preserved

- Write every fetched payload to the raw store **exactly as received**, keyed by
  source + fetch time, **before** any other processing.

## 4. Counts reconcile

- For each source, **received == stored + quarantined**, and the counts are
  reported. No silent drops.

## 5. Secrets & data handling

- **Never commit** secrets, production API keys, or sample attachment URLs.
- Keep secrets in environment variables or AWS Secrets Manager.
- **Strip embedded credentials** from any source attachment URL before the data
  enters the repo.
- Use **only** the sample file, the mock NDP API, event-issued staging
  credentials, and synthetic data. Never use or request production keys.
- If personal client data is found anywhere, **stop and notify One Degree**.

## 6. Operational data separation

- Findings, evidence, review state, model logs, and user accounts live in
  **separate** tables/stores from the HSDS records.
- Provenance that must travel with a record uses the single `x_resource_engine`
  extension. **Document every HSDS extension** in `contracts/hsds/extensions.md`.

## 7. AI proposes, humans apply

- The AI maintenance layer **proposes**; a person **applies**.
- Every AI finding requires **evidence** (source URL + a non-empty supporting
  snippet) or it is dropped / marked low-confidence — never stored evidence-free.
- Compare **facts, not wording** — do not flag three-word rephrasings.
- Every applied change writes an HSDS **metadata** entry (who / field / from /
  to / when).

## 8. Least privilege & encryption

- Least-privilege IAM role per component.
- Encryption in transit, and at rest for S3, the database, and OpenSearch.

## 9. Change control

- Branch per task; PR into protected `main`; CI gates must pass.
- Protected surfaces (`/contracts/**`, `/infra/**`, `/.github/workflows/**`,
  `/.github/CODEOWNERS`, this file, secrets/data-handling config) require human
  approval via CODEOWNERS.
