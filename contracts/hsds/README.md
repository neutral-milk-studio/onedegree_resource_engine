# HSDS Data Contract

**Pinned version: HSDS v3.2.3** (Open Referral Human Services Data Specification).

_Spec: Requirements 1.1, 1.2, 1.3._

## Pinned release

- Specification repository: `github.com/openreferral/specification`
- Pinned tag: **`v3.2.3`**
- The JSON Schema files vendored under `contracts/hsds/schema/` are the
  authoritative source for validation. Field names in documentation are checked
  against these files, not the other way around.

The vendored files come from commit `74fcf85` of that tag, including
`service_capacity.json`, which `service.json` references. They are licensed under
CC BY-SA 4.0; see [`LICENSE`](LICENSE). Do not edit them.

## Boundary rule

Any record that is **stored, served, or exchanged** is valid HSDS JSON for the
pinned version. Internal storage may differ as long as it round-trips to valid
HSDS. Validation happens at: transformer output before storage, API responses
(in tests), any export, and every admin-UI save. _(Req 43)_

## Required objects and fields

A record missing any required property fails validation.

| Object | Required properties |
|--------|--------------------|
| organization | id, name, description |
| service | id, name, status |
| location | id, location_type |
| address | id, address_1, city, state_province, postal_code, country, address_type |
| phone | id, number |
| service_at_location | id |
| schedule | id |
| metadata | id, last_action_date, last_action_type, field_name, previous_value, replacement_value, updated_by |
| attribute | id |
| url | id, url |

Other objects (`language`, `accessibility`, `service_area`, `required_document`,
`cost_option`) require only `id`.

Allowed values:

- `service.status` ∈ { active, inactive, defunct, temporarily closed }
- `location_type` ∈ { physical, postal, virtual }
- `address_type` ∈ { physical, postal, virtual }

Omit a property that has no value rather than sending `null`/empty — unless the
property is required.

See `extensions.md` for the deterministic-id rule, the change-history rule, and
the single `x_resource_engine` extension convention.
