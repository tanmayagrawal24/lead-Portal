# Implementation Brief — Lead Portal

**For:** Claude Code
**Inputs:** `lead-portal-spec-v0.2.md`, `lead-portal-spec-v0.3-delta.md`
**Operator:** Tanmay Agrawal, Creative Potatoes. Solo. Runs on localhost only.

---

## Task 0 — before writing any code

Read both documents in full. Apply the delta to v0.2 and save the merged result as `docs/lead-portal-spec-v0.3.md` in the repo. The delta blocks replace named sections wholesale; delete v0.2's `§10 Resolved review questions`.

Then report back: a list of anything in the merged spec that is ambiguous, self-contradictory, or that you believe is wrong. **Do not start building until that list has been reviewed.** The spec has already survived two review passes; a third set of eyes on the merged text is cheaper than discovering the problem in code.

The merged v0.3 is the single source of truth from that point. If implementation reveals the spec is wrong, change the spec first, then the code — never let them diverge.

## Stack

Python 3.11+, FastAPI, SQLite (WAL), `httpx`, `selectolax`, `anthropic`, `pydantic`, Jinja2, HTMX. Standard library for everything else.

No Node, no Docker, no build step, no ORM, no migration framework, no LLM orchestration framework. Migrations are numbered `.sql` files applied in order by a ~30-line runner.

**Adding any dependency beyond this list requires asking first.** The dependency list is a design decision, not an accident.

**`anthropic` and `pydantic` are live as of `f1f9732` onward (M1.58).** An external audit recommended deferring or dropping both as unused; that was accurate against an earlier tree and is wrong now. `portal/llm_anthropic.py` imports `anthropic` — lazily, inside the function that needs a key, so the module imports and tests without one — and §5.5b's `ImpressumExtract` / `HomepageExtract` are `pydantic` models. Recorded here rather than left to be re-raised.

## Build order

Vertical slices. Each milestone ends with something that runs and produces visible output. Do not build ahead.

| M | Deliverable | Done when |
|---|---|---|
| **M0** | Repo scaffold, migration runner, full schema applied, `portal init` | `portal init` creates the DB; every table and the view exist; re-running is a no-op |
| **M1** | `portal fetch` — robots, politeness, artifact storage, Impressum two-step discovery | Runs against 5 real German shop domains from a seed CSV; artifacts on disk; robots respected; 1 req/s observed |
| **M2** | `portal extract-p1` — all deterministic parsers (§5.3) | Fixture tests pass (see below); every signal row has a real `evidence_url` |
| **M3** | `portal score --phase 1` — ruleset v3, blog ladder chain, `ADVANCE_THRESHOLD` | Re-running produces identical scores; every `score_component.reason` is a complete German sentence |
| **M4** | `portal serve` — read-only list, row expansion, filters | Table renders; expanding a row shows every component with its evidence link |
| **M5** | `portal extract-p2` (Impressum + homepage), `portal reconcile`, batch handling, substring verification | A batch survives process restart and reconciles; unverified names land with `confidence=0` and render red |
| **M6** | AI-visibility check | **Built 2026-09-03 (Unit 10, M1.105) — `portal ai-check`, spend-gated behind `--submit`; never run.** Earlier note kept: **Unblocked 2026-08-16 (M1.54).** Both conditions are met: the per-search rate is confirmed at $10/1,000 (§5.5c) and Claude Haiku 4.5 **does** support web search, via the basic `web_search_20250305` variant. Price one thing in before scheduling: the newer `web_search_20260209` (dynamic filtering — results are filtered by code execution *before* reaching context) needs Opus 4.6+ / Sonnet 4.6+, so on Haiku raw results land in context in full and **tokens per search exceed what the per-search fee implies** |
| **M7** | Brief export, outreach logging, `portal purge`, `portal forget` | **Done 2026-09-03 (Unit 10, M1.106).** Export fails loudly when `ai.*` basis fields are missing; `forget --domain X` leaves zero rows anywhere |
| **M8** | `portal discover` — Places API | **Built 2026-09-03 (Unit 10, M1.107), never run.** Field mask is exactly `displayName`, `websiteUri`, `formattedAddress`; quota cap confirmed in Cloud Console first |

**Discovery is deliberately last.** Everything upstream can be validated against a hand-written seed CSV of 20 domains, at zero cost. Building the paid discovery layer first would mean spending money to test parsers.

M2 is where the real risk lives and deserves the most time. If the deterministic parsers are unreliable on real German shop HTML, nothing downstream matters.

## Testing

Ordinary unit tests where they're cheap. The one thing that genuinely needs coverage:

**Fixture tests for every parser in §5.3.** Save real HTML from 8–10 actual German/Austrian SME shops — a mix of Shopware 6, Shopify, WooCommerce, and JTL — into `tests/fixtures/`. Assert the expected signal value for each. These fixtures are the regression suite; they are what proves `content.blog_last_post` reads the blog index correctly rather than trusting sitemap `lastmod`.

Include at least one adversarial fixture per parser: a blog index with unparseable dates (must produce `needs_review`, not a guess), a Shopware sitemap mixing content and product URLs, a footer with a logo-only agency credit.

> **Impressum fixtures contain real names, addresses, and phone numbers — personal data.** Do not commit them. Either redact to `Max Mustermann` / `Musterstraße 1` while keeping the structural markup intact, or gitignore the fixture directory and keep it local. Redacted is better: the tests then run anywhere.

## Conventions

- Fail loudly. Missing config, a renamed signal key, a `PHASE2_MAX_POINTS` that disagrees with the live ruleset — raise at startup, don't warn and continue.
- Scoring is a pure function of `company_profile`. It must never issue a network call.
- Every signal write carries a real `evidence_url`. No placeholder, no empty string, no synthesised URL.
- `score_component.reason` is German and letter-ready. `"Letzter Blogbeitrag: März 2023."` — not `blog_stale=true`.
- Secrets from environment only. `.env` gitignored. Never log a key, never log a full API response containing one.
- Type hints throughout. `ruff` for lint and format, default config.
- **Comments are normative or historical, and it should be obvious which (M1.58).** *Normative*: what the code must do and why — a constraint a future editor would otherwise break. *Historical*: what was measured, when, on how many shops, and what the wrong answer cost. An external audit recommended periodic comment and documentation pruning; **that is refused.** The historical narrative in `fetch.py`, `extract.py`, `sitemap.py` and the migrations *is* the amendment-table discipline that has produced 58 numbered defects each carrying its measurement, and every one of the last four was found by reading a claim back against its evidence rather than by a test. Pruning it for density would delete the instrument that finds the next M1.43. The split is what makes the volume navigable; the volume itself is the asset.

## Stop and ask

Do not proceed on any of these without checking first:

- Adding a dependency outside the named stack
- Anything that sends email, or adds an SMTP or mail-API dependency — this is a hard non-goal, not a backlog item (§8)
- Any network write to an endpoint other than: Google Places, PageSpeed Insights, the Anthropic API
- Changing a scoring weight, band threshold, or rule predicate — the spec owns those
- A parser that appears to need a headless browser
- Anything that would make the tool multi-user, deployable, or authenticated

## Scope guard

The following are explicitly out of scope. If a plausible-sounding reason to add one appears mid-build, that is the signal to stop and ask, not to proceed:

email sending · LinkedIn/Xing/Instagram scraping · paid contact databases (Apollo, ZoomInfo, Cognism) · CRM features beyond the flat `outreach` table · auth · deployment · Docker · any LLM framework

## First-run target

The definition of success for M0–M4 is a single sequence, on 20 seeded domains, costing nothing:

```bash
portal init
portal fetch --seed seeds/nrw-shops.csv
portal extract-p1
portal score --phase 1
portal serve
```

...producing a browsable, sorted list where every score traces to a stored page. Get there before anything paid is wired up.
