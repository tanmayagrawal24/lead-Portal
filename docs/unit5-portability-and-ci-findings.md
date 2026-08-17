# Unit 5 — the suite stops asking a resolver, and something starts running it

Measured 2026-08-17 against `6a5e266`. **No crawl, no API call, no spend.**
Every HTTP request in this unit went to a loopback fixture server.

Companion to M1.64–M1.66 in `docs/lead-portal-spec-v0.3.md`.

---

## 0. Baseline, taken before anything moved

The instruction predicted "green apart from the three known `www.localhost`
failures". **That is not what this machine reports**, and the difference is the
whole of M4:

| | result |
|---|---|
| before the stash | `1 failed, 531 passed, 2 skipped, 94 subtests` |
| the one failure | `test_schema.py::TestObjectsExist::test_every_spec_table_exists` |
| after the stash | `531 passed, 2 skipped, 94 subtests` — **fully green** |

The three apex→www tests **pass here**. This codespace resolves *every*
`.localhost` subdomain — `www.localhost`, and `foo.localhost` too — to `::1`.
That is precisely the defect: the tests pass on the machines the project is
developed on and fail where CI runs, so the baseline they are measured against
depends on who is looking. §5 below constructs the failing condition rather than
taking it on trust.

The two skips are `tests/test_live_smoke.py`, gated on `PORTAL_LIVE_SMOKE=1`.
They are the one thing in the suite that would leave the machine, they are
correctly skipped, and CI now fails the build if that variable is ever set.

## 1. The interrupted M5 work, inventoried before it was moved

Stashed as `interrupted-M5-remnant`, **not deleted**. Recoverable with
`git stash list` / `git stash apply`. Stashed **by explicit path**: a bare
`git stash push -u` would have swept in 391 MB of untracked `node_modules/`.

**Worth salvaging.** It is coherent, documented work, and most of it is the
answer to a real question:

- **`Rule.phase2_input_settled`** (`portal/ruleset.py`) — a per-rule callable
  answering *has Phase 2 already answered this?*, distinct from
  `phase2_reachable`'s *could it?*. `assert_declared` requires it on every
  Phase-2-reachable rule and refuses it on the others, so the next rule cannot
  quietly forget. Deliberately not derived from outcomes, because a rule that
  DECLINES is recorded as no component at all and a Phase-2 `false` — the
  commonest case — would be invisible to any outcome-based reading.
- **The `settled` term in `score.evaluate`** — with it, §5.4's admission gate
  can *tighten*; without it the gate only ever loosens, and a company keeps
  carrying upside for a question Phase 2 has already answered and is re-admitted
  on it.
- **Three-state `_own_brand` and `_owner_operated`** — *not run* declines,
  *ran and answered* fires or declines, *ran and could not tell* abstains with
  `own_brand_undetermined` / `owner_named_undetermined`. A7 applied where the
  substring backstop cannot reach a boolean.
- **`portal/pagespeed.py`** (228 lines) — the §5.5a writer for
  `perf.lighthouse_performance`, with an injected client and a per-run call
  ceiling written to `run.pagespeed_calls`. Never run live, and says so.
- **`portal/verify.py`** (135 lines) — §5.5b substring verification, which
  takes the *sent text* as an argument and cannot reach an artifact, so it
  structurally cannot repeat M1.43's "verified against a different page".
- **`portal/migrations/010_phase2_writers.sql`** (337 lines) — five changes.

**Why none of it could simply be committed, and this is the recorded
judgement.** Migration 010 creates `llm_batch_request`. Verified rather than
assumed:

```
$ grep -rn llm_batch_request portal/*.py      # (no output)
$ grep -n  llm_batch_request docs/lead-portal-spec-v0.3.md   # (no output)
```

**No writer in `portal/`, and no registration in the spec.** That is M1.45(c)'s
shape — *a documented path with no writer is a claim the tool does not keep* —
and it is what 010's **own header comment** says the migration exists to avoid:
*"Splitting them would put schema on disk ahead of the code that fills it."*
It is also the direct cause of the `test_every_spec_table_exists` failure.

Rebuild it in M5 **with** its writer, or register it in §10.6 as
ahead-of-writer deliberately. Either is fine; arriving as a side effect of
`git stash pop` is not.

## 2. M3 — still open, and this is the headline

```
$ gh repo view tanmayagrawal24/lead-Portal --json visibility,isPrivate
{"isPrivate":false,"visibility":"PUBLIC"}
```

**The repository is public.** Verified twice on 2026-08-17 — once at Unit 4 and
again here. 13 named real prospects, written assessments of their marketing, and
§6's scoring weights and band thresholds are published together, so any named
company can compute why it scored low. The decision to go private was recorded
at Unit 4; **a decision recorded is not a change made.** It remains the
operator's action and nothing in this unit can perform it.

## 3. Which resolution call actually happens (M4, and it was not assumed)

The instruction said not to assume `socket.getaddrinfo`. Traced by wrapping both
candidate functions and printing the call stack for one real `Fetcher.get`:

```
httpx 0.28.1 / httpcore 1.0.9

socket.create_connection(('localhost', 34987))
   via: handle_request -> handle_request -> handle_request -> _connect -> connect_tcp
socket.getaddrinfo('localhost', 34987)
   via: handle_request -> _connect -> connect_tcp -> cc -> create_connection
```

So httpcore's sync backend calls **`socket.create_connection`**, and
`socket.create_connection` reaches **`socket.getaddrinfo`** as a module global
in `socket.py` — which means patching the attribute intercepts it. Both are
viable seams; `getaddrinfo` is the narrower waist and does not break the day
httpcore builds a socket itself, so the shim goes there.

## 4. The two rejected fixes, and the one that was checked in the code

**`@skipUnless` — refused.** It turns the suite green while never exercising
M1.8's shared politeness budget, on exactly the machines where the suite runs
most often. A skipped politeness test is worse than a failing one, because a
failing one gets fixed.

**`Host:` header against `127.0.0.1` — refused, and the reason was verified
rather than accepted.** The claim was that the politeness key comes from the URL
authority and not the header. In the code:

- `net.Fetcher.get` → `self.limiter.wait(host_of(current))`, where `current` is
  the URL string being fetched.
- `urls.host_of(url)` → `authority_of(url).removeprefix("www.")`, and
  `authority_of` is `urlsplit(url).netloc`.

No header is read anywhere on that path. **The instruction's reading is
correct**: both the apex and the www case would key on `127.0.0.1:PORT`, the
test could no longer tell them apart, and it would pass while measuring nothing
— which is the exact defect class it exists to catch. Pinned now by
`TestTheResolverShim::test_the_shim_is_what_makes_the_apex_www_tests_reachable`.

## 5. Reproducing the failure, then fixing it (the evidence)

A pytest plugin makes `*.localhost` subdomains unresolvable — `localhost` itself
still resolves, because RFC 6761 §6.3 requires that and leaves only the
**subdomain** optional, and the subdomain is what these tests needed. Applied to
a **worktree at `6a5e266`**, so the working tree was never disturbed.

| run | condition | result |
|---|---|---|
| `6a5e266` | container-like resolver | **3 failed**, 528 passed, 2 skipped |
| this unit | container-like resolver | **535 passed**, 2 skipped |
| this unit | this machine's resolver | **537 passed**, 2 skipped, 98 subtests |

The three, with the exact error the review reported (`AssertionError: None != 200`):

```
FAILED tests/test_fetch.py::TestApexToWwwWithinTheSeededSite::test_the_robots_fetch_follows_the_hop_and_the_run_proceeds
FAILED tests/test_fetch.py::TestApexToWwwWithinTheSeededSite::test_the_www_policy_is_seeded_so_the_homepage_hop_costs_no_second_fetch
FAILED tests/test_politeness.py::TestRedirectsAreRateLimited::test_apex_and_www_hops_to_one_server_share_one_budget
```

**A fourth test was passing vacuously in that condition, which the count of
three hides.** `test_an_apex_to_www_redirect_is_not_a_move` asserts
`site_domain IS NULL` and no `domain_moved` flag — both satisfied when the
redirect target simply never resolves. It is an absence assertion met by the
request failing, so it went green while testing nothing. It is converted with
the other three and now exercises the shape it names.

## 6. Why the fixture names are `.invalid` and not `.localhost`

Mapping `www.localhost` would have worked. It would also have been **dead weight
on every machine that already resolves it** — this one included — so the shim
could be deleted and no test would notice locally. The suite would go back to
passing by accident, which is how this defect survived long enough for an
external reviewer to find it.

`shop.invalid` / `www.shop.invalid` are reserved by RFC 2606 §2 and resolve
nowhere, on any machine, ever. The shim is therefore load-bearing everywhere:
break it and the suite fails on the maintainer's laptop and in CI alike. That
property replaces *"assert the shim is installed"* with *"the tests cannot run
without it"* — a structural guarantee rather than an assertion that could itself
rot. `TestTheResolverShim` pins the non-resolution directly, so the guarantee is
measured and not merely argued.

## 7. CI, and the proof it is wired to something

`.github/workflows/ci.yml`, three jobs, `permissions: contents: read`.

- **lint** — `ruff check .` and `ruff format --check .`, both.
- **test** — pytest on 3.11 (the `pyproject.toml` floor) and 3.12 (development).
  **3.13 is deliberately absent**: no interpreter above 3.12 has ever run this
  suite and none is available here to try, and putting an unverified version in
  a first CI is how a green light gets ignored the week it goes red. Adding it
  should be a deliberate act with a run behind it.
- **politeness** — builds a corpus from a loopback fixture server and audits it.

**The migration runner is not a fourth job**, and the instruction allowed for
this. `tests/test_migrate.py::TestApplyPending::test_applies_then_is_a_no_op`
already applies every migration to a fresh database, and
`test_user_version_matches_highest_migration` checks the head. A separate job
would re-run that path with weaker assertions.

**`audit-politeness` is a real job, not a deferred one.** Unit 4 changed its
preconditions — it reads the artifact table now and requires a database — so
`tests/fixture_corpus` builds one at the real 1 req/s floor (anything faster
would fail its own spacing audit; the healthy corpus takes ~9 s). It runs
**twice**:

```
$ python -m tests.fixture_corpus corpus-ok
corpus at corpus-ok/portal.db: run 1, 10 artifacts,
  kinds=['blog_article','blog_index','homepage','impressum','product_page','robots','sitemap']
$ portal --db corpus-ok/portal.db audit-politeness   → §5.2: HELD          exit 0

$ python -m tests.fixture_corpus corpus-bad --breached
corpus at corpus-bad/portal.db: run 1, 1 artifacts, kinds=(none)
$ portal --db corpus-bad/portal.db audit-politeness  → §5.2: BREACHED      exit 1
      *** UNREAD *** 127.0.0.1   HTTP 503 http_503 — 0 bodies stored for this company
```

The second is the anti-vacuity half: spacing still measures perfectly there, and
M1.62's check is what refuses it. The breached corpus storing **1 artifact and
no bodies** is Unit 4's tri-state visible end to end.

**Does CI catch the bug it shipped alongside?** Its own pytest command, run
against `6a5e266` in the container resolver condition:

```
$ env -u ANTHROPIC_API_KEY python -m pytest -q -p noresolve
3 failed, 528 passed, 2 skipped
```

Red, on exactly those three. This is not a green light wired to nothing.

## 8. No key, and that is enforced rather than requested

The suite runs under `env -u ANTHROPIC_API_KEY` — the variable **removed**, not
blanked — and a step before it fails the build if `ANTHROPIC_API_KEY` or
`PORTAL_LIVE_SMOKE` is present at all. A comment saying *"do not add a secret"*
is read only by people already being careful. This is what makes Unit 2's
injected-client seam a measurement: `llm_anthropic._client` raises
`MissingKeyError` before any network attempt, and every provider test supplies
its own fake. M5 is the next unit and the first that spends; the door is shut
before it, not after.

## 9. What could not be verified

- **The workflow has never run on GitHub Actions.** Every command in it was run
  locally and the failure condition was reproduced locally, but the runner
  itself, `actions/setup-python`, and Python **3.11** specifically are unproven
  — only 3.12 exists on this machine (`/usr/bin/python3.12`, and nothing else).
  If 3.11 fails, it fails on something this unit could not see.
- **Whether the container base image GitHub uses resolves `.localhost`.** It
  does not matter any more — the tests use `.invalid`, which resolves nowhere —
  but the original claim about *that specific image* is still second-hand.
- **`package.json` / `package-lock.json`** are untracked and declare
  `@github/copilot-sdk`. They are unexplained by anything in the repository and
  are **not** part of the M5 remnant, so they were left exactly as found.
  `node_modules/` is now gitignored: 391 MB of build output in a public
  repository, one `git add -A` away. Flagged as a judgement call the instruction
  did not cover.

## 10. Still open

The first external audit's section headed *"LLM-generated/hallucination
signals"* has **still** not been transmitted. Five units have now reported
around it. It is missing, not empty, and that audit is not closed.
