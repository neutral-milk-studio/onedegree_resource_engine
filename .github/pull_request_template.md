<!-- Spec: Requirement 4.1. The reviewer agent posts its verdict against REVIEWER_RUBRIC.md. -->

## What this PR does

<!-- One or two sentences. Link the task from tasks.md and the requirement IDs. -->

- Task:
- Requirements:
- Stream / team:

## Touches a protected surface?

<!-- Protected surfaces require human approval via CODEOWNERS. Tick any that apply. -->

- [ ] `/contracts/**` (HSDS schema, adapter/store/api/findings/website-source interfaces)
- [ ] `/infra/**` (infrastructure, IAM)
- [ ] `/.github/workflows/**` or `/.github/CODEOWNERS` (CI / governance)
- [ ] `/docs/agents-guardrails.md` (guardrails)
- [ ] secrets / data-handling config
- [ ] None of the above (routine — merges on green CI + favorable reviewer verdict)

## Guardrail checklist

- [ ] Every stored/served/exchanged record is valid HSDS v3.2.3.
- [ ] No secrets, production keys, or sample attachment URLs committed.
- [ ] Operational data kept out of the HSDS tables.
- [ ] AI proposals carry evidence; humans apply changes; applied changes write metadata history.
- [ ] Tests added/updated and passing locally.

## How this was verified

<!-- Commands run, tests added, counts, etc. -->
