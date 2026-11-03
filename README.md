# The Resource Engine

A data platform for One Degree that collects community resource data from multiple sources, converts it to [Open Referral HSDS](https://docs.openreferral.org/) v3.2.3, and serves it through a REST API. An AI maintenance layer checks records against public websites and proposes changes for resource specialists to review.

## Project status

This repository is in early setup. The development environment is ready, and application code is being added.

## Quick start

You need [uv](https://docs.astral.sh/uv/getting-started/installation/) and `make`. uv installs Python 3.12 and all dependencies for you.

```bash
git clone https://github.com/neutral-milk-studio/onedegree_resource_engine.git
cd onedegree_resource_engine
make setup
make test
```

If the tests pass, your environment is ready.

To set local configuration, copy the example file and fill in what you need:

```bash
cp .env.example .env
```

## Common commands

| Command | Purpose |
|---|---|
| `make setup` | Install Python and the locked dependencies |
| `make test` | Run the tests |
| `make lint` | Check linting and formatting, as CI does |
| `make format` | Fix linting and formatting |
| `uv add <package>` | Add a dependency and update `uv.lock` |
| `docker compose up postgres` | Start PostgreSQL (requires Docker) |
| `docker compose --profile search up opensearch` | Start OpenSearch (requires Docker) |

## How we work together

1. Update `main`, then create a branch named `<area>/<short-description>`, such as `ingest/ndp-adapter`.
2. Keep changes small, and merge at least every half day.
3. Run `make lint` and `make test` before you push.
4. Open a pull request into `main`, and get one teammate's review.
5. Merge once CI passes.

```bash
git switch main
git pull
git switch -c ingest/ndp-adapter
# Make your changes
make lint test
git add <files>
git commit -m "Add NDP adapter"
git push -u origin ingest/ndp-adapter
```

If you add a dependency, commit both `pyproject.toml` and `uv.lock`.

## Workstreams

| Area | Owns | Done when |
|---|---|---|
| **Ingest** | Source adapters, raw storage, HSDS mapping, quarantine | Received records equal stored plus quarantined, and re-runs create no duplicates |
| **Data core** | Database, field history, merge rules, HSDS API | Conflicts are surfaced, and every API response validates |
| **Maintenance** | Website checks, extraction, findings, evaluation | Seeded errors are caught, and every proposal has evidence |
| **Review** | Review queue, evidence view, editing | A specialist can go from a flagged record to a saved fix in a few steps |

Each area works mainly in its own top-level folder. Only the data core area changes the database schema. Tell the affected areas before you change anything they depend on.

## Project rules

- **HSDS:** Anything the system shares or serves must validate against HSDS v3.2.3. Internal storage can differ.
- **AI changes:** The AI proposes changes, and a person applies them. A proposal without evidence isn't a finding.
- **Secrets:** Never commit credentials or a `.env` file. Use environment variables or AWS Secrets Manager.
- **Data:** This repository is public, so don't add new data files without checking with the team. Use only sample, mock, or synthetic data, never production data.
- **Client data:** Resource data shouldn't contain personal client information. If you find any, stop and tell One Degree.

## Documentation

Project requirements and HSDS guidance are in [`docs/`](docs/).

## License

[MIT](LICENSE)
