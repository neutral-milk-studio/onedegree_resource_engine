# Documented HSDS Extensions & Conventions

_Spec: Requirements 7.6, 24.2, 24.3, 12.1._

HSDS permits extra properties where no HSDS term fits. The Resource Engine keeps
**all** of its extras in a single property per record, `x_resource_engine`, so
they are easy to find and strip. Every extension used by the system is documented
here. This is the authoritative "list of documented HSDS extensions" for handoff.

## The `x_resource_engine` extension

A single object attached to a served record carrying provenance that must travel
with the record:

```json
{
  "id": "…",
  "name": "…",
  "x_resource_engine": {
    "source":     { "system": "ndp_a", "record_id": "22560", "fetched_at": "2026-10-20T15:04:05Z" },
    "last_check": { "category": "discrepancy", "checked_at": "2026-10-20T16:00:00Z" },
    "evidence":   [ { "field": "eligibility_description", "url": "https://example.org/apply", "snippet": "…", "model": "…" } ],
    "ai_generated_fields": ["organization.description"]
  }
}
```

| Key | Meaning |
|-----|---------|
| `source.system` | stable source/adapter name used in the deterministic id |
| `source.record_id` | the source's native record id |
| `source.fetched_at` | ISO 8601 fetch time (matches the raw-store key) |
| `last_check` | category + timestamp of the most recent AI maintenance check |
| `evidence` | supporting evidence entries (url, snippet, model) for proposals |
| `ai_generated_fields` | HSDS field paths whose value was AI-generated |

Operational data (findings not yet applied, model logs, review state) does **not**
live here — it lives in the findings store (`ops.*` tables).

## Deterministic id rule

Every HSDS `id` is a UUID. Record ids are generated deterministically:

```
hsds_id = uuid5(NAMESPACE, f"{source_name}:{native_id}")
```

so re-ingesting a source updates existing records instead of duplicating them.
A map from HSDS id → source record is maintained in the `id_map` table.
_(Req 12.1, 12.2, 12.3)_

## Change-history rule

Every **applied** change writes an HSDS `metadata` object recording: the
resource, `field_name`, `previous_value`, `replacement_value`, `updated_by`,
`last_action_date`, and `last_action_type`. Evidence, unapplied proposals, and
model logs do **not** go in `metadata` — they belong to the findings store.
_(Req 26.2)_
