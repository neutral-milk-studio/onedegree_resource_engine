# Task Brief

A self-contained brief for one delegated task (one agent session, one branch,
one PR). Keep it small enough to finish and verify in a single session.

_Spec: Requirement 6.2._

## Task

- **Task ID (from tasks.md):**
- **Title:**
- **Owning stream / team:**
- **Branch:** `feature/<stream>-<slug>`

## Goal

<!-- One paragraph: what this task delivers and why. -->

## Requirements implemented

<!-- e.g. Requirements 8.1, 8.2. The reviewer checks the PR against these. -->

## Contracts this builds against

<!-- Which committed contracts the code must honor (adapter / store / api / findings / website_source / HSDS schema). Do NOT change a contract in a routine task — that needs human approval. -->

## Definition of done

- [ ] Code implements the listed requirements.
- [ ] Tests written and passing (property tests for any mapped correctness property).
- [ ] No protected surface changed (or, if changed, human approval requested).
- [ ] Guardrails honored (see the PR template checklist).
- [ ] CI green.

## Notes / inputs

<!-- Seed data to use, sample fixtures, assumptions, links. -->
