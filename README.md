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

- **LLM provider layer** (proposal v2 build steps 1–3) — `portal/llm.py` and
  `portal/llm_anthropic.py`: prices as dated data, per-model limits as declared
  data, and the batch failure taxonomy including the prepaid-balance case. No
  caller yet; inspect it with `portal llm-prices`.

Phase 2 (`extract-p2`, M5) is not built.

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

Redirects are followed one hop at a time, and **every** hop is checked twice:
against the robots.txt of the origin it lands on, and against its **address**
(M1.68). The second check is there because the first one asks the target's own
server for permission — a service on `127.0.0.1` or `169.254.169.254` has no
robots.txt, and "no rules stated" reads as "everything permitted". Loopback,
private, link-local and reserved destinations are refused before a socket
opens; the refusal is recorded in `artifact.error` like any other failed fetch.

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

Read-only except flag resolution. **Binds to `127.0.0.1` and refuses to bind
anything else** unless `--allow-public-bind` is passed explicitly (M1.56): §1 is
a single-operator tool with no authentication, and §8's rows are third-party
personal data. Every loopback spelling works with no flag; `0.0.0.0` and `::`
count as public, because a wildcard binds every interface the machine has.

```bash
portal llm-prices               # what a call costs, and as of when
portal llm-prices --reserve 40  # a real §7 control 4 reservation (needs a key)
```

`portal llm-prices` prints the two tables the LLM layer is built on — token
prices with their as-of dates, and the per-model facts an interface must not
generalise away (Haiku 4.5 rejects `output_config.effort`, caps output at 64K
rather than 128K, and will not cache a prompt under 4096 tokens). It touches no
database and makes no paid call. `--reserve` performs a real `count_tokens`
measurement and therefore needs `ANTHROPIC_API_KEY`; without one it says so
rather than substituting a heuristic, because a fallback estimate is how an
unmeasured number gets into the cost ledger looking measured.

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

Nothing here contacts a third-party host. The one live request in the project is
`tests/test_live_smoke.py`, opt-in behind `PORTAL_LIVE_SMOKE=1` and targeting
`creative-potato.global` only; everything else runs against loopback fixture
servers. The apex→www tests use `shop.invalid` / `www.shop.invalid`, resolved to
127.0.0.1 by the suite's own shim (M1.64) — `.invalid` is reserved by RFC 2606
and resolves nowhere, so the suite never asks a resolver a question that a
different machine could answer differently.

`.github/workflows/ci.yml` runs ruff, the suite on Python 3.11 and 3.12, and
`audit-politeness` against a corpus built from fixtures — the last one twice, so
the gate has to prove it can still go red (M1.65). CI has no `ANTHROPIC_API_KEY`
and fails the build if one appears.

## Migrations

Numbered `.sql` files in `portal/migrations/`, named `NNN_lower_snake.sql` and
applied in order by `portal/migrate.py`. No ORM, no migration framework.

Applied state is `PRAGMA user_version`, which holds the number of the highest
applied migration — there is no bookkeeping table, so the database contains
exactly the objects §4 describes and nothing else. Each file is applied inside a
transaction together with its version bump, so a failure rolls back whole.

To add one: write the next number, never edit an applied file. The runner
rejects gaps, duplicates, malformed names, and a database newer than the code.
