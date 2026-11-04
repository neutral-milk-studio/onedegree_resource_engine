# onedegree_resource_engine

The Resource Engine takes information about community services (food banks, housing help,
clinics and so on) from different sources and converts it into one shared standard format,
[Open Referral HSDS](https://docs.openreferral.org/) **version 3.2.3**.

## How it works, in plain language

### What is HSDS?

HSDS (Human Services Data Specification) is a public standard for describing community
services. It sets out exactly what information to record about a service (its name, its
opening hours, its phone numbers and so on) and how to write it down. Because everyone uses
the same rules, data from different organizations can be combined and shared.

### Where the transform fits

```
Extraction (teammate)  ──▶  Transform (this part)  ──▶  Saving to the real database (later)
pulls data from the         translates it into HSDS      not built yet; for now the results
ODRS API and saves it       and checks it                go into a practice (mock) database
```

- **Extraction** is handled by a teammate. It downloads the data from One Degree's system
  (called ODRS) and saves it exactly as received. For the second source, 211's National Data
  Platform (NDP), there is no live connection during the hackathon, so we use two saved
  sample files instead.
- **Transform** is this part of the project, described below.
- **Saving to the real database** comes later. Until then, the transform puts its results
  into a small practice database on your own computer, so they can be checked and tested.

### What the transform does

The transform is like a translator:

1. **It reads** the saved data, which is in each source's own format. It currently
   understands two sources: **ODRS** (One Degree) and **NDP** (211).
2. **It translates** each record into HSDS. For example, ODRS calls a service's name its
   "title", and HSDS calls it "name"; ODRS writes a phone extension inside the number
   ("650-276-4101 x 4"), and HSDS keeps it in its own field.
3. **It checks** every translated record against the official HSDS rules. Anything that
   breaks a rule is set aside with a note explaining why. We call this **quarantine**.
   Nothing broken gets through. Real examples from the NDP sample: four addresses that just
   say "Various Locations" (no city or postal code), and three "phone numbers" that are
   actually web addresses.
4. **It keeps** the records that passed, ready to be saved.

### How the data is organized

HSDS splits each service into small linked pieces instead of one big record. For example, a
food pantry becomes:

- one **organization** (who runs it)
- one **service** (what it offers)
- one **location** and its **address** (where it is)
- several **phone** entries and **schedule** entries (how to call, when it's open)

Each piece has its own ID, and the pieces point to each other by ID ("this phone belongs to
that service"). If several services share the same organization or location, that piece is
stored only once and shared. The practice database follows the same layout HSDS describes:
one table for each kind of piece.

### What the transform does not do yet

- **One Degree's categories and tags** (such as "Housing" or "Eviction prevention") are not
  translated yet. We are waiting on One Degree to tell us how their categories are organized.
  NDP's categories already come with their levels (for example *Education > Educational
  Institutions/Schools > School Districts*), so those are translated.
- **Some details**, such as languages spoken and documents needed to apply, are not
  translated yet. They are not lost: extraction keeps the original data, so they can be
  added later.

## Technical details: Transform (MVP)

Raw API pages → source mapper → HSDS validation → keep or quarantine.

| Module | Role |
|---|---|
| `transform/hsds.py` | Validates objects against the vendored v3.2.3 schemas in `transform/schema/`; id, provenance and cleanup helpers |
| `transform/odrs.py` | ODRS `/v1/opportunities` page → HSDS objects |
| `transform/ndp.py` | NDP page (`data[]`, one organization per record) → HSDS objects, including taxonomy |
| `transform/run.py` | Runs map → validate → keep/quarantine; parent failures quarantine their children; checks counts |
| `transform/store.py` | Local SQLite in the official HSDS v3.2.3 table layout (24 tables from `transform/schema/datapackage.json`) for running and testing the transform. Loading into the production database is a separate workstream |

Output is checked so it is valid in table form whatever database it lands in: `validate()` checks the JSON Schema plus each schema's `tabular_required` fields (e.g. `service.organization_id`), and `run.split()` quarantines any object whose parent is neither in the batch nor already stored.

### Run it

```sh
uv sync
uv run python -m pytest
uv run python -m transform.run odrs     # transforms the ODRS sample file
uv run python -m transform.run ndp      # transforms the NDP sample files
```

Run these from the project folder. With no file names they use the sample files in
`data/samples/`; to transform other saved pages, list them after the source name
(`uv run python -m transform.run odrs page-1.json page-2.json`). Results go to
`resource_engine.db` unless you pass `--db other.db`.

Both sources can go into the same database; every record keeps `x_resource_engine.source` so you can tell them apart.

### Decisions

- **Ids:** UUIDv5 of `source:kind:native_id`, so re-ingesting updates records instead of duplicating them.
- **Provenance:** every organization, service and location carries `x_resource_engine.source = {system, record_id}`.
- **ODRS organization description:** ODRS has none and HSDS requires one. The MVP uses `"<name>. Description not available."` and flags the record with `x_resource_engine.flags: ["missing_information:description"]`.
- **ODRS status:** `unavailable_at` set → `inactive`; null → `active`.
- **Schedule times:** kept as America/Los_Angeles wall-clock `HH:MM` without a UTC offset, because a fixed offset would be wrong for half the year. Days with the same hours are grouped into one schedule (`byday: "MO,WE"`). ODRS free-text notes go to `schedule.notes`.
- **Location hours** in ODRS belong to one opportunity, so they attach to the `service_at_location` link, not the location.
- **Phones:** `"x 4"` style extensions are split into the numeric `extension` field. ODRS `phone_type` is a free-text label, so it goes to `description`; `type` is `textphone` for TDD/TTY labels, otherwise `voice`.
- **Quality rule (ours, not HSDS):** a phone `number` must contain a digit. HSDS only requires a string, and NDP has web addresses in that field. Rules like this live in `QUALITY_RULES` in `transform/hsds.py` and their errors say "Resource Engine quality rule".
- **NDP:**
  - Service `status` comes from `meta.status`. Every service uses its parent record's organization, because 4 inactive stub services have `idOrganization: null`.
  - `meta.lastUpdated` is a date only; `last_modified` must be a date-time, so it becomes midnight UTC (`2025-06-13T00:00:00Z`).
  - Address `mailing` → `postal`; country `United States` → `US`. "Various Locations" addresses are quarantined and their locations kept.
  - Phone `fax` → `fax`, `tty` → `textphone`, everything else → `voice`.
  - `languages.description` ("English, Spanish") → one `language` per name, with an ISO 639 code for known names.
  - `servicesAtLocations` with no `idLocation` (4, all inactive) are skipped: there is nothing to link.
  - **Taxonomy:** one `taxonomy` ("211 Taxonomy"). `taxonomyTermLevel1..6` become a chain of `taxonomy_term`s linked by `parent_id`, with ids from the level path so shared levels are one term. Only the deepest level has a code in NDP; higher levels get none rather than a guessed one. `targets[]` (e.g. YB-8000 "Older Adults") become flat terms. Each service gets an `attribute` per most-specific term.
- **Not mapped yet (ODRS):** tags, categories and areas (taxonomy, waiting on One Degree's term hierarchy), eligibility and community flags (attributes), languages, required documents, contacts. They remain in the raw pages saved by extraction.
- **Output form:** HSDS tabular form (flat objects joined by id), which HSDS allows for storage. Loading into the real database is later work; until then `transform/store.py` is the mock.

### HSDS extensions

| Property | On | Contents |
|---|---|---|
| `x_resource_engine.source` | organization, service, location | `{system, record_id}` of the source record. In the SQLite tables it lives in `hsds_extension`, because HSDS tables have no column for it |
| `x_resource_engine.flags` | organization | Data-quality flags, e.g. `missing_information:description` |
