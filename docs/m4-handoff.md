# M4 — `portal serve`

The §9 page. FastAPI + Jinja2 + HTMX, server-rendered, no SPA, no build step,
no Node. `htmx.min.js` is vendored under `portal/static/`, so the page loads
nothing from the network — §2 and §3 stand. `uvicorn` is the one dependency
added, approved for this milestone under M1.6.

## Why it came before M5

§5.4's safety claim now reads *nothing recoverable is discarded **without a
human being told*** (M1.41), and the dependency was written down with it: the
claim holds only if the queue is read. There was no way to read it. Five review
reasons, a contact block and five abstention rules existed only as CLI output.

M5 also spends money, and the gate admits 12 of 13 — Phase 2 would have run at
~$0.065 each over a list nobody could inspect.

## What it renders, and why each item is here

| | |
|---|---|
| Ranked table | Band with phase, score, gate decision (`P2` / `Stopp`) with the score-plus-upside arithmetic in its tooltip, city, platform, last post, AI-visibility, hook, state badges. |
| Row expansion | Every `score_component` with its German reason, its rule id and §-section, and every signal the rule **declared** it reads — each linking to the stored artifact. |
| Abstentions | Rendered as *Enthaltung* with the reason, on a tinted row, never as `0` and never omitted. Counted on the summary row too. |
| Review flags | Individually, each with `raised_note`, each independently clearable — writing `resolved_at`, `resolved_by_human = 1` and an optional note. |
| Contact block | A red panel naming the flag and its rationale, read out of `contact_blocking_reason`. |
| LLM marking | Dotted underline plus a `confidence` tooltip; `confidence = 0` in red with a *nicht verifiziert* tag. |
| Filters | Band, platform, country, excluded, needs_review, contact_blocked — all six, composable, state in the URL. |

**Three decisions worth keeping.**

*The evidence link is derived, never mapped by hand.* `Rule.reads` names profile
columns; `company_profile`'s own definition in `sqlite_master` names the signal
key behind each column; the signal row carries `evidence_url` and `artifact_id`.
`leadlist.evidence_keys` parses the view rather than restating it, and
`assert_evidence_reachable` fails at startup if any rule input cannot be traced
— the same discipline as `ruleset.assert_declared`. A hand-kept copy of that
mapping would be a second expression describing what the view does, which is
precisely the shape of M1.40 and M1.42.

*Only finished runs are authoritative, here too.* `company_profile` enforces
migration 007's rule for signals. `score` has no view, so `leadlist` applies the
same rule in SQL: a crashed run that reached 10 of 13 companies is not the
account the list is read from.

*Artifacts are served as `text/plain`.* The operator is checking a citation
against what was scored. Rendering a third party's stored HTML would run their
scripts on this origin and show something other than the bytes we parsed.

**One dependency was refused.** `Form(...)` and `request.form()` both require
`python-multipart`, which the brief says to ask about before adding — for a
content type htmx does not send. The flag-resolution body is
`application/x-www-form-urlencoded` and is parsed with `urllib.parse.parse_qs`.

## Not built, and deliberately

- **§9's other row actions** — mark excluded with a reason, log an outreach
  attempt, export the research brief. The brief export belongs with M5 (it
  asserts on `ai.*` signals that do not exist yet); the other two are ordinary
  writes with no open question behind them.
- **Phase-2 columns** render `—`: `ai_visibility` returns `—` rather than `0/0`
  where no query was run, because zero checks finding zero mentions is not the
  claim two checks finding none is (§8).
- **`contact_blocking_reason.rationale` is stored in English** (migration 008)
  while the rest of the operator-facing text is German. The panel shows it
  verbatim rather than translating in the template — a translation in the view
  layer is another second expression. Worth a migration if it matters.

## Running it

```bash
portal serve                      # 127.0.0.1:8000
portal serve --port 9000
```

Binds to loopback by default and should stay there: §1 is a single-operator
tool with no authentication, so binding anywhere reachable publishes an
unauthenticated database.

## Tests

`tests/test_serve.py`, 27 cases, driven through the app with `TestClient` and
parsed with `selectolax`. The properties under test are not "the page renders"
but that it renders the states which would otherwise be invisible and does not
flatten them into each other: an abstention is not a `0`, an unscored company is
not a `0`, a `confidence IS NULL` signal is not red, each flag clears alone, and
the lifted block actually lets the `outreach` trigger through — asserting the
badge without asserting the trigger would test the paint, not the door.
