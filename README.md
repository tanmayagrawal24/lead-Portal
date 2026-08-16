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
- **M2 complete** — `portal extract-p1`: deterministic §5.3 signals off stored
  artifacts, no requests, free to re-run.
- **M3 complete** — `portal score --phase 1`: the §6 ruleset as a ranked list,
  with every abstention recorded as a component and the §5.4 per-company gate.
- **M4 complete** — `portal serve`: the §9 review page.

Phase 2 (`enrich-p2`, M5) is not built.

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

```bash
portal serve                # http://127.0.0.1:8000
```

`portal serve` is the §9 page: the ranked list, and the only way to read what
the pipeline has been recording. Every row expands to its score components —
each with its German reason and a link to the stored bytes the value was read
off — and the states that have no other outlet are rendered as themselves:

- **Abstentions as abstentions.** A rule that fired in neither direction shows
  as *Enthaltung* with its reason, never as `0` and never as a missing row. It
  is the difference between "no blog" and "we could not tell".
- **The review queue, one flag at a time.** Each open `review_flag` carries its
  `raised_note` and clears independently, writing `resolved_at`,
  `resolved_by_human = 1` and an optional note. §6.4 made them distinct reasons
  because they send a person to different pages.
- **The contact block, explained.** Where an unresolved too-high abstention
  refuses the `outreach` insert (A7, migration 008), the row says so and the
  panel names the reason and its rationale — a block a human cannot see is a
  block that gets worked around by hand.

Read-only except flag resolution. Binds to `127.0.0.1` by default and should
stay there: §1 is a single-operator tool with no authentication.

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
