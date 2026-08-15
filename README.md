# Lead Portal

A locally-run tool that gathers evidence about the state of DACH e-commerce
SMEs' content marketing, scores it reproducibly, and presents a reviewable list.

`docs/lead-portal-spec-v0.3.md` is the single source of truth. If implementation
reveals the spec is wrong, change the spec first, then the code.

Localhost only. Single operator, no auth, no deployment.

## Status

**M0 complete** — repo scaffold, migration runner, §4 schema, `portal init`.
Nothing else is built yet; the remaining stages arrive with their milestones.

## Setup

```bash
pip install -e .
portal init          # or: python -m portal init
```

`portal init` creates the database, applies every pending migration, and prints
the resulting schema inventory. It is safe to re-run: a database already at the
current version is left untouched.

The database defaults to `data/portal.db`. Override with the `PORTAL_DB`
environment variable or `--db`. Secrets come from the environment only; `.env`
is gitignored and never committed.

## Tests

```bash
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
