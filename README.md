# Lead Portal

A locally-run tool that gathers evidence about the state of DACH e-commerce
SMEs' content marketing, scores it reproducibly, and presents a reviewable list.

`docs/lead-portal-spec-v0.3.md` is the single source of truth. If implementation
reveals the spec is wrong, change the spec first, then the code.

Localhost only. Single operator, no auth, no deployment.

## Status

- **M0 complete** — repo scaffold, migration runner, §4 schema, `portal init`.
- **M1 complete** (branch `m1-fetch`) — `portal fetch`: robots handling,
  politeness, artifact storage, Impressum two-step discovery, A5 product-sample
  selection. See `docs/m1-handoff.md`.

Later stages arrive with their milestones. `extract-p1` (M2) is not built.

## Setup

```bash
pip install -e .            # runtime
pip install -e ".[dev]"     # runtime + test/lint tooling
portal init                 # or: python -m portal init
```

`portal init` creates the database, applies every pending migration, and prints
the resulting schema inventory. It is safe to re-run: a database already at the
current version is left untouched.

```bash
portal fetch --seed seeds/example.csv
```

`portal fetch` walks each seeded domain in the §5.2 order under hard politeness
limits — 1 request/second per host, two hosts in flight, identifiable
User-Agent, robots.txt honoured. The CLI refuses an `--interval` below the floor
or a `--max-hosts` above the ceiling. Seed-file format is in `seeds/README.md`;
**real prospect lists need the operator's approval before any crawl.**

The database defaults to `data/portal.db`. Override with the `PORTAL_DB`
environment variable or `--db`. Secrets come from the environment only; `.env`
is gitignored and never committed.

## Tests

Tests are stdlib `unittest`, so either runner works:

```bash
pytest                                        # or:
python -m unittest discover -s tests -t .
ruff check . && ruff format --check .
```

## Migrations

Numbered `.sql` files in `portal/migrations/`, named `NNN_lower_snake.sql` and
applied in order by `portal/migrate.py`. No ORM, no migration framework.

Applied state is `PRAGMA user_version`, which holds the number of the highest
applied migration — there is no bookkeeping table, so the database contains
exactly the objects §4 describes and nothing else. Each file is applied inside a
transaction together with its version bump, so a failure rolls back whole.

To add one: write the next number, never edit an applied file. The runner
rejects gaps, duplicates, malformed names, and a database newer than the code.
