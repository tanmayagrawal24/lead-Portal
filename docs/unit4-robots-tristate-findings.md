# Unit 4 — the robots.txt tri-state, and what M1.44 was actually measuring

Measured 2026-08-17 against the stored corpus at `5f56560`. **No crawl.** Every
number below comes from `data/portal.db`, the bodies under `data/artifacts/`,
and `data/requests.jsonl`. Nothing was fetched to produce this document.

Companion to M1.59–M1.62 in `docs/lead-portal-spec-v0.3.md`.

---

## 1. Why this measurement was taken

H1 — the defect M1.59 fixes — made every non-200 `robots.txt` response collapse
into an unrestricted policy. `robots` is a stored artifact kind (migration 001),
and **M1.44 concluded "exactly two of 521 stored bodies are disallowed by their
own company's robots.txt" by testing each URL against the newest stored
robots.txt for that company.** If any run had stored a robots artifact with a
non-200 status or an absent body, that check would have parsed `None` →
unrestricted → *allowed* for every URL of that company, and the count would be
an **undercount**.

So the question was not whether H1 exists — it does — but whether it had already
corrupted the measurement that M1.44's repair scope rests on.

## 2. Every stored robots artifact, per company, newest first

13 companies, 16 robots artifacts.

| company | id | url | status | body |
|---|---|---|---|---|
| bio-fleischer-laden.de | 61 | `https://bio-fleischer-laden.de/robots.txt` | 200 | yes (3,656 B) |
| blackpolish.de | 41 | `https://blackpolish.de/robots.txt` | 200 | yes (3,622 B) |
| doonails.de | 22 | `https://www.doonails.com/robots.txt` | 200 | yes (4,077 B) |
| doonails.de | 20 | `https://doonails.de/robots.txt` | 301 | no — `redirect_refused` |
| ekomia.de | 60 | `https://ekomia.de/robots.txt` | 200 | yes (5,590 B) |
| germanelectronic.de | 188 | `https://lampenflut.de/robots.txt` | 200 | yes (937 B) |
| germanelectronic.de | 187 | `https://germanelectronic.de/robots.txt` | 301 | no — `redirect_refused` |
| navucko.com | 32 | `https://navucko.com/robots.txt` | 200 | yes (3,604 B) |
| opulent-wohnen.com | 174 | `https://www.opulent-wohnen.com/robots.txt` | 200 | yes (930 B) |
| propellerdiscount.de | 196 | `https://propellerdiscount.de/robots.txt` | 200 | yes (905 B) |
| smile-store.de | 2 | `https://www.smile-store.de/robots.txt` | 200 | yes (738 B) |
| smoke2u.de | 173 | `https://smoke2u.de/robots.txt` | 200 | yes (659 B) |
| snocks.com | 80 | `https://snocks.com/robots.txt` | 200 | yes (7,362 B) |
| verpackungskoenig.de | 185 | `https://verpackungskoenig.de/robots.txt` | 200 | yes (1,074 B) |
| zecplus.de | 458 | `https://blog.zecplus.de/robots.txt` | 200 | yes (173 B) |
| zecplus.de | 1 | `https://www.zecplus.de/robots.txt` | 200 | yes (3,624 B) |

**Companies whose newest robots artifact is not a 200-with-body: 0 of 13.**

The two 301s are M1.18's moved-domain shape — the apex `robots.txt` redirects
off the seeded site, the hop is refused, and the host actually crawled has its
own file read before its first request (ids 22 and 188). Neither is the newest
artifact for its company.

**So H1's undercount hazard did not materialise on this corpus.** That is a
result about this corpus at this moment and not a property of the code: nothing
prevented it, and the next 5xx would have produced it.

## 3. M1.44's disallow check, re-run four ways

521 stored 200-with-body artifacts, each URL tested against a stored policy.

| method | disallowed | undecidable |
|---|---|---|
| A — newest robots artifact per company, any status (**M1.44 as described**) | **2** | 0 |
| B — newest **200-with-body** robots artifact per company | **2** | 0 |
| C — newest 200-with-body robots artifact of the **same authority** | 1 | 26 |
| D — as C, falling back to the `www.`/apex sibling | **2** | 0 |

The two, in every method that decides them:

- `snocks.com` artifact **171** — `https://snocks.com/policies/legal-notice`,
  `kind='impressum'`, under `Disallow: /policies/`.
- `smoke2u.de` artifact **186** — `https://www.smoke2u.de/Impressum`,
  `kind='impressum'`, under `Disallow: /Impressum`.

**"Exactly two" survives.** A and B agree because of §2 — there is no company
whose newest robots artifact is unreadable, so restricting to 200-with-body
changes nothing. M1.44's conclusion stands and its repair scope is unchanged in
extent.

*A measurement that confirms a prior finding is still a result*, and this one
also says something the prior finding did not: the count was not protected from
H1 by anything. It survived.

## 4. What the re-run did find — the method, not the count (M1.61)

### 4a. "The newest robots.txt for that company" is not the robots.txt that governs the URL

§5.2 says twice that robots is keyed to the **origin** (RFC 9309), and that apex
and `www.` are separate there. M1.44's check was keyed to the **company**.

On `zecplus.de` the two disagree. The newest robots artifact for that company is
**id 458 — `blog.zecplus.de`'s**, all 173 bytes of it:

```
# START YOAST BLOCK
User-agent: *
Disallow:

Sitemap: https://blog.zecplus.de/sitemap_index.xml
# END YOAST BLOCK
```

`Disallow:` with an empty value is *allow everything*. So **all 31 of that
shop's stored bodies were tested against a fully permissive file belonging to a
different origin**, while `www.zecplus.de`'s own 3,624-byte file (id 1) was
never consulted. The check passed vacuously there.

Re-run against id 1 — the file that actually governs those URLs — the answer is
the same: **0 of 31 disallowed**. The method was wrong and the answer was right,
which is exactly the combination that survives review unnoticed.

### 4b. Two origins serving identical bytes collapse into one artifact row

`uq_artifact_identity` is `(company_id, kind, content_hash)`. Two origins that
serve byte-identical `robots.txt` therefore produce **one row**, naming whichever
origin was recorded first.

Method C found 26 artifacts on authorities with no robots artifact of their own —
15 on `www.propellerdiscount.de`, 11 on `www.smoke2u.de`. The request log says
those files were read:

```
  3 https://propellerdiscount.de/robots.txt       statuses=['200']
  3 https://www.propellerdiscount.de/robots.txt   statuses=['200']
  3 https://smoke2u.de/robots.txt                 statuses=['200']
  3 https://www.smoke2u.de/robots.txt             statuses=['200']
```

Six requests, three rows collapsed into one each time. The artifact table is not
wrong about the *content* — the bytes are identical — but it is wrong about the
*origin*, and any check keyed on origin reads it as an absence.

This is why method C reports `smoke2u.de` 186 as **undecidable** rather than
disallowed: the apex file was applied to a `www.` URL by A, B and D, and strictly
speaking `www.smoke2u.de`'s own file is not in the table. It was read. It is
filed under its sibling.

### 4c. Consequence for M1.44's repair, which is M5's work and not this unit's

M1.44 (a) says selection *"excludes any artifact whose URL the company's stored
robots policy disallows, checked at selection time against the newest stored
`robots.txt`"*. **Implemented as written, that reproduces 4a**: on `zecplus.de`
it would check against the blog's permissive file. Two constraints follow:

1. The lookup is **origin-keyed**, not company-keyed.
2. A collapsed row (4b) is the policy of **every origin that served those bytes**,
   which the table does not record. Where that cannot be established, the check
   must report **not verifiable** rather than *allowed* — the H1 failure mode one
   level out.

Not fixed here. Unit 4's scope is the tri-state; this is recorded so M5 inherits
the constraint rather than the sentence.

## 5. What `audit-politeness` says now (M1.62)

Against the corpus at `5f56560`, after the change:

```
738 requests logged, 16 politeness keys
…
max hosts in flight: 2 (ceiling 2) — ok

robots.txt coverage: 2 stored artifacts are not 200
  no file       doonails.de            HTTP 301 redirect_refused: … — RFC 9309 §2.3.1.2, not a breach
  no file       germanelectronic.de    HTTP 301 redirect_refused: … — RFC 9309 §2.3.1.2, not a breach
§5.2 robots: HELD — 0 unread, 2 stating no file
§5.2: HELD
```

**Judgement call, stated because the instruction did not cover it.** The unit
asked that *any* non-200 robots artifact be reported **and exit non-zero**. It is
reported; it fails only for the **unavailable** class (5xx, 429, no status). The
`no file` class — 4xx and refused redirects — is reported and does not fail,
because on this corpus that class is exactly the two rows above, M1.18's
moved-domain shape, where the host actually crawled had its own `robots.txt`
read first. Failing on them would make `audit-politeness` red on a healthy
corpus from the day it landed, and M2 is about to make it a CI gate. A check
that is always red is a check nobody reads, which is the failure mode M1.19 was
written against. The split is in the output, so an operator sees both classes.

## 6. Limits of this measurement

Named rather than measured away.

- **`artifact` carries no `run_id`**, and failure rows update in place (§5.2). So
  "was there ever a run with a non-200 robots artifact?" is **not answerable from
  this table** — only "is there one now?". A robots artifact that 503'd in run 1
  and succeeded in run 2 leaves a failure row *and* a success row, but one that
  503'd and was never re-fetched at that URL is indistinguishable from one that
  503'd yesterday.

  The request log answers it for the runs it covers, and that is **not all
  runs**. `data/requests.jsonl` is append-only and holds 738 entries spanning
  three `fetch` runs — run 4 (2026-08-15 12:40–12:43), run 12 (15:20–15:23) and
  run 29 (2026-08-16 14:55–14:59). Runs 1–3 predate `RequestLog` (M1.19) and
  cannot be checked at all. Over the covered window, `robots.txt` requests are
  **46 × 200 and 15 × 301, with no 5xx, no 429 and no transport error** — so
  H1's disallow-all branch would not have fired in those three runs.

- **The corpus does contain the precursor, on pages rather than on robots.txt.**
  In run 29 the log records **eight 429s and one 500**: `snocks.com` returned 429
  to `/imprint`, `/legal`, `/rechtliches`, `/blogs/lifestyle` and two product
  pages, and `ekomia.de` returned 500 on a blog article and 429 on two product
  pages. This is M1.45's rate-limit storm seen from the log side. It matters
  here because a host rate-limiting our page requests is precisely the host whose
  `robots.txt` would plausibly 429 on the next run — and under H1 that response
  would have granted permission to crawl everything, at our own default pacing,
  on a server that had just said stop **eight times**. H1's exposure was not
  hypothetical; the trigger condition is in the corpus and only the ordering
  spared it.
- **The 26 undecidable artifacts in method C are undecidable, not unverified.**
  4b explains them; it does not prove that both origins served identical bytes at
  every point in time, only that they did when the collapse happened.
- **Nothing here re-reads a body against a *live* robots.txt.** Every policy used
  is a stored one. That is the point — no crawl — and it means all of it is a
  statement about the corpus as stored.

## 7. What the mandated re-run moved (M1.63)

`score --phase 1` and `diff-signals` were re-run against the stored corpus. **No
crawl.** Both were run on a copy of `data/portal.db` so the corpus is left
exactly as found, and with `5f56560`'s `ruleset.py` and `score.py` rather than
the unrelated uncommitted changes sitting in the working tree.

**Twelve of thirteen totals and bands are unchanged. One moved:**

| domain | run 34 (2026-08-16) | re-run (2026-08-17) |
|---|---|---|
| navucko.com | 17 (D) | **42 (C)** |

`neg.active_content` (−25) stopped firing. It is not Unit 4's doing, and the
proof is direct: a score run computed with the *working tree* and one computed
with *`5f56560`'s* ruleset are identical row for row. It is the clock.

```
posts:  2025-12-01  2026-01-06  2026-02-17  2026-04-11  2026-06-07  2026-06-20
SIX_MONTHS = 180
2026-08-16:  (today − 2026-02-17).days = 180  → recent = 4  → fires  (−25)
2026-08-17:  (today − 2026-02-17).days = 181  → recent = 3  → silent
```

The rule needs `>= 4`. One post crossed the 180-day line overnight and took
§6.3's largest penalty with it, across a band boundary. Direction of error:
**up** — a penalty withdrawn, which §6.4 treats as the expensive direction. No
flag was raised, correctly: `blog_post_count = 6` against 6 dated posts is a
complete enumeration, so the rule *declines* rather than abstaining and A7's
guard is not engaged.

**The general point, which is why it is numbered:** §5.4's "score is a free
recompute" is true of cost and not of result. `evaluate` is a function of
`(signals, today)` and the second argument moves without anyone touching the
repository. A diff between two score runs taken on different days is not
evidence about the code, and any "nothing changed" claim has to name its date.

`diff-signals` reports **3 changes across 3 domains on 1 key** —
`catalog.not_measurable`'s `value_text` gaining shard and URL counts on
`opulent-wohnen.com`, `smoke2u.de` and `verpackungskoenig.de`. That is the diff
between the two most recent **extract** runs already in the corpus (33 → 35),
both of which predate this unit. **Unit 4 wrote no signal and changed none**,
which is what a change confined to `fetch`, `robots` and `audit` with no crawl
should do.

## 8. Still open

The **first** external audit's section headed *"LLM-generated/hallucination
signals"* has still not been transmitted. It is **missing, not empty**, and that
audit is **not closed**. Carried forward.
