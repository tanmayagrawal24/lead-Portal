# Unit 6 — a name for the policy, and a gate on the address

Measured 2026-08-18 against `d57ea64`. **No crawl, no API call, no spend.**
Every HTTP request in this unit went to a loopback fixture server, and the one
DNS lookup of a public name was made by a throwaway script, not by the suite.

Companion to M1.67–M1.68 in `docs/lead-portal-spec-v0.3.md`.

---

## 0. Baseline, taken before anything moved

| | result |
|---|---|
| `d57ea64`, clean tree | **537 passed, 2 skipped, 98 subtests** |
| lint | `ruff check .` and `ruff format --check .` both clean |
| working tree | clean; **no stash present in this container** |

The two skips are `tests/test_live_smoke.py`, gated on `PORTAL_LIVE_SMOKE`.

**The `interrupted-M5-remnant` stash does not exist here, and cannot.** Unit 5
stashed it on 2026-08-17 and a stash is local to a working copy; this session
began from a fresh clone in a new container, so `git stash list` is empty. The
work is not lost *if* the machine Unit 5 ran on still exists — and it is
unrecoverable from the repository alone, because nothing was committed. This is
recorded as a finding rather than a footnote: §10.4b currently reads as though
the remnant is retrievable by anyone picking up M5, and it is retrievable only
by whoever holds that container. **The inventory in the Unit 5 report is
therefore the surviving artifact**, and M5 should be planned as a rebuild from
that description rather than as a `git stash apply`.

## 1. M3 — still open, and it is still the headline

```
$ curl -s https://api.github.com/repos/tanmayagrawal24/lead-Portal
{ ..., "private": false, "visibility": "public", ... }
```

**The repository is public.** Verified a third time, 2026-08-18. One caveat
stated rather than glossed: the outbound proxy in this environment injects
GitHub credentials — the response carries `X-RateLimit-Limit: 15000`, where an
unauthenticated caller gets 60 — so this was an *authenticated* read. It is
still dispositive: `private` and `visibility` are the repository's own fields
and an authenticated read of a private repository returns `"private": true`.
The external reviewer's independent check on 2026-08-17 was an unauthenticated
`git clone`, which succeeded.

13 named prospects, written assessments of their marketing, and §6's weights and
band thresholds are published together, so any named company can compute why it
scored low. **The visibility change is the operator's and nothing in this unit
can perform it** — there is no repository-settings tool in this session. It has
now outlived three units.

## 2. H2 — the defect, measured before it was fixed

`net.Fetcher` walks redirect chains itself, one hop at a time. Every hop target
was put to `hop_allowed`, which asks: *does this target's `robots.txt` permit
the fetch?* **That is the target's own account of itself**, and it is the wrong
authority to consult when the target is chosen by the redirecting site.

The PoC ran the **real** `portal.serve` app — the §9 review page, as
`portal serve` starts it by default: `127.0.0.1`, no authentication, over the
operator's own database — and pointed a fixture shop's homepage at it:

```
shop /robots.txt  ->  200 "User-agent: *  Allow: /"
shop /            ->  302  http://127.0.0.1:8009/
```

Result on `d57ea64`:

```
robots     http://127.0.0.1:36127/robots.txt   status=200
robots     http://127.0.0.1:8009/robots.txt    status=404  error=http_404
homepage   http://127.0.0.1:8009/              status=200
      stored 9537 bytes of the internal page
      title: ['<title>Lead Portal</title>']
      names a real prospect: True

VERDICT: internal loopback service reached and stored = True
```

Three things in that output, and the second is the one that was not obvious:

1. **The internal service was fetched.** Its `robots.txt` 404s, RFC 9309
   §2.3.1.2 reads a 404 as *no rules stated*, and the hop was therefore allowed
   by a correctly-implemented rule.
2. **The body was stored in `artifact`, under the shop's `company_id`, with
   `kind = 'homepage'`.** This is not only an exfiltration route. Every later
   stage reads `artifact` by kind, so the operator's own UI would have been
   parsed as a German shop's front page — a corpus-integrity defect that would
   have produced signals about a company from a document that has nothing to do
   with it. M1.17 and M1.43 are the same family.
3. The stored body **names a real prospect**, because the page it fetched
   renders the operator's lead list.

`169.254.169.254` is the identical hole with cloud credentials behind it, and
needs no cooperating service on the machine at all.

## 3. Why the fix is in the transport, and why it is not a `hop_allowed` check

`net.py`'s own standing argument: *"The rules are hard requirements, not
options, so they live in the transport rather than in the callers: nothing in
this codebase can issue an HTTP request that skips the rate limiter, because
there is no other way to issue one."*

The address gate takes the same place, and gains something by it: it applies to
the **first** URL as well as to every hop. A seed row naming `127.0.0.1` or
`192.168.1.1` is the same defect arriving through the front door —
`urls.normalise_domain` accepts both, since its only structural test is that a
dot is present. Putting the check in `fetch`'s `hop_allowed` would have covered
redirects and left the seed path open.

Measured after the fix, same PoC, unchanged:

```
robots  http://127.0.0.1:39375/robots.txt  status=None
        error=address_refused: 127.0.0.1 → 127.0.0.1 (loopback)

VERDICT: internal loopback service reached and stored = False
```

Note *which* request it refused: the **seeded shop's own** `robots.txt`, because
under the production policy a loopback fixture server is exactly as forbidden as
the victim. That is the correct behaviour and it is why the suite needs a named
seam rather than a special case.

## 4. The six decisions inside the guard

Each of these had a lazier option that would have passed the same tests.

1. **The refused networks are written down**, not delegated to
   `ipaddress.is_private`. That property's membership has changed between
   CPython releases — 100.64.0.0/10 among them — and CI runs **3.11 and 3.12**.
   A guard whose behaviour differs between two interpreters in the same matrix
   is a guard nobody can state the behaviour of.
2. **An IP literal is never resolved.** `http://169.254.169.254/` is refused by
   parsing, so the case that matters most cannot be defeated by anything a
   resolver does. Asserted by injecting a resolver that raises if called.
3. **IPv4-mapped v6 is unwrapped.** `::ffff:127.0.0.1` sits in no refused v6
   network; judged as v6 alone it reads as public.
4. **Every resolved address is judged, and one bad answer refuses the name.** A
   resolver returning one public and one loopback address is the shape of a
   rebinding attack, and picking the reassuring half of an answer is how a guard
   is talked out of firing.
5. **Unresolvable is `address_unverifiable` and refused**, not allowed. This is
   M1.59's ruling one layer down, and the reason string keeps it distinct from a
   refusal: *this pointed somewhere it must not* and *DNS was down* send an
   operator to different places.
6. **The resolver is looked up at call time, not import time.** This one is
   load-bearing and easy to get wrong. `tests/fixture_server.resolves_to_loopback`
   installs its shim by rebinding the `socket.getaddrinfo` **attribute** (M1.64).
   A `resolver=socket.getaddrinfo` default evaluated at class-definition time
   would capture the original function, and the guard would then judge a name
   *differently from the client that is about to connect to it* — approving or
   refusing an address that is not the one httpcore will use. A guard that asks a
   different resolver than the caller is not a guard; it is a second opinion
   about a different question. It is pinned by a test rather than a comment,
   because the two outcomes are distinguishable: bound early the reason reads
   `did not resolve`, looked up late it reads `loopback`.

## 5. The seam, and why it does not switch the guard off

The entire suite fetches from loopback, so an exemption has to exist.
`AddressPolicy.loopback_permitted()` is that exemption, named in
`HostRateLimiter.unthrottled()`'s idiom — one greppable token per call site
rather than a boolean nobody can find, and nothing in `portal/` constructs it.

**It widens loopback and nothing else.** A test running against 127.0.0.1 still
refuses `169.254.169.254`, `10.0.0.1`, `192.168.0.1` and `172.20.0.1`, so the
guard is *live* in the suite rather than disabled by it — which is what lets
`test_a_redirect_into_a_private_address_is_refused` be an end-to-end test
through a real socket under the same policy every other test uses.

The anti-vacuity test for the seam itself is
`test_the_production_default_refuses_the_fixture_server`: a plain `Fetcher()`
against a live fixture server, asserting both the refusal **and that the
server recorded zero requests**. The guarantee is that no socket opens, not that
the response is discarded.

## 6. Negative control — the tests were run against the absent fix

"Run it, don't just test it", applied to the tests. With the check in
`Fetcher.get` disabled:

| | result |
|---|---|
| the 5 new transport-level tests | **all 5 fail** |
| the 9 new `AddressPolicy` unit tests | pass — they do not depend on the wiring |
| **the 537 pre-existing tests** | **all pass** |

The last row is the finding. **Nothing in 8,255 lines of tests could see this
defect**, which is M1.65's observation one level in: the suite measured what it
was built to measure and this was never in it.

## 7. L1 — the extraction, and how "no behaviour change" was measured

`policies`, `unfetchable`, `policy_for`, `hop_allowed` and `allowed` were four
closures over three mutable dicts inside `run_company`, the longest method in
the project. They are now `portal/sitepolicies.SitePolicies`.

It landed as its **own commit, before the guard**, because combining a security
behaviour change with a 110-line extraction makes a defect in either
attributable to both.

A green suite is weak evidence for a refactor, so the claim was measured
directly. The fixture corpus was built from identical fixtures at `d57ea64` and
at the extraction, and the two databases compared field by field:

| compared | result |
|---|---|
| every `artifact` url / status / error / bytes | identical |
| every `signal` key / value / `evidence_url` | identical |
| every `company.excluded_reason`, every `review_flag` | identical |
| every line of the request log | identical |
| `content_hash` + `bytes` of the 4 gzipped sitemap shards | **differ** |

And the noise floor, which is why that last row is not a finding: **the same
commit run twice differs in the same fields and more of them** — 4 differing
`bytes` values against the extraction's 2 — because the fixture embeds its
ephemeral port in the XML it serves. The measured difference is strictly inside
the instrument's own noise.

Both corpora were checked in both directions afterwards:

```
healthy corpus  -> exit=0
breached corpus -> exit=1
```

**Seeding deliberately stayed with the caller.** An unreadable seeded
`robots.txt` stops the run (M1.59) and a seeded `Crawl-delay` over the cap
**excludes the company** — a standing verdict about a lead, which a
per-authority memo has no business taking. `SitePolicies` is one instance per
company run and never shared: a `robots.txt` is a statement about one afternoon,
and a memo outliving the run would let one shop's 503 silence another's.

## 8. What this unit did not close

- **The rebinding residual (§10.5).** The guard resolves and httpx resolves
  again. IP literals are immune; a name under a hostile authoritative resolver
  is not. Closing it means pinning the resolved address into a custom transport,
  carrying the hostname in `Host:`, TLS SNI and certificate verification — real
  production surface for a threat never observed here, and M1.4 is the standing
  lesson about that. **Labelled, not fixed.**
- **The guard classifies addresses, not architectures.** A shop on a public
  address that proxies to something internal is not covered and cannot be.
- **M3.** Public, third verification, operator's action.
- **M5's three prerequisites**, unchanged and none started: §7's monthly-rolling
  cost ceiling as a startup assertion; the origin-keyed robots lookup (M1.61);
  score-date pinning (M1.66, checked and **not** closed).
- **The first external audit's "LLM-generated/hallucination signals" section**
  has still never been transmitted. Five units have now reported around it. It
  is missing, not empty, and that audit is not closed.

## 9. Where the instructions were wrong

- **The review's heading order for Unit 6 reads "H2 and L1, in that order"
  while its own body says the extraction lands first.** The body is right and is
  what was done; §10.4b now says so, because the heading would otherwise be
  re-derived and re-derived wrongly — which is exactly what happened to M2/M4 in
  Unit 5.
- **"Do the visibility flip first — it costs nothing"** is right about the cost
  and wrong about who can pay it. No tool in this session can change repository
  visibility; three units have now recorded the decision and none could execute
  it. It needs the operator, once, in the GitHub UI.
- **The handoff note about `pytest-xdist` and the `.invalid` shim was checked
  and is correct**, and now has a second occupant: `AddressPolicy`'s default
  resolver reads `socket.getaddrinfo` at call time and therefore also sees that
  process-wide patch. If the suite is ever parallelised with threads rather than
  processes, both the shim and the guard's view of it are affected together.
