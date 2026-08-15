# M1.14 — read before M3, requested after the M1.32 guard landed

**Status:** analysis. Nothing here is implemented, and one candidate instrument is recommended **against**.
**Question asked:** with the guard in place, the blog ladder has two independent ways to be wrong about a blog. How do they interact, and what should be done about the one that is left?
**Measured on:** the stored corpus, 2026-08-15. No requests — every instrument below was tested against homepages already on disk.

---

## 1. The short version

M1.14 is not one instrument short of a fix. It is **A7 (§5) applied to the largest award in the ruleset, and not applied**. `content.blog_exists = 0` is the one place in the blog ladder where the pipeline writes a confident measurement it has documented that it cannot make, fires +25 on it, and tells nobody.

The instrument gap is real and is smaller than §10.1 assumed: **anchor text reaches both unreachable shapes, and both are already on disk.** But closing the instrument gap does not close the blocker, because "we searched with a better vocabulary and found nothing" is still a vocabulary claim, and `opp.no_blog` is an award for an absence.

---

## 2. How the two errors compound

They are not two errors in one rule. They are the same error — *manufacturing the opportunity the outreach letter is about* — arriving through two doors, and the guard closed the cheaper one.

| | M1.32 (closed) | M1.14 (open) |
|---|---|---|
| the wrong claim | this blog is stale | this shop has no blog |
| award | +20 | **+25 — the largest in ruleset v3** |
| shops affected | 2 of 13 | 6 of 13 receive the award; **≥ 2 of those 6 wrongly** |
| points at stake | 20 | 150 awarded, ≥ 50 manufactured |
| status | fires in neither direction, routes to a human | fires, silently |

**Three ways they compound, beyond both being wrong in the same direction:**

1. **The ladder is first-match-wins, so M1.14 sits upstream of everything M1.32 protects.** A false `blog_exists = 0` does not merely add a wrong +25 — it short-circuits at rung 1 and makes rungs 2 through 5 unreachable. On `zecplus.de`, all of M1.32's care about what a lower bound can and cannot support **never executes**. The guard is downstream of a gate that can be wrong, which means the gate now bounds the value of the guard.

2. **The guard raised M1.14's relative importance rather than lowering it.** Before, the ladder had two routes to a manufactured opportunity. Now it has one, it is the bigger one, and it is the only rung in the ladder with no abstention behind it.

3. **Both are invisible in a score, in opposite signs.** §10.3 already forbids calibrating §6.5 on this corpus because ~a quarter of it is systematically ~25 points *light*. The blog ladder now adds a second instrument distortion pointing the other way on a different, disjoint subset. Two silent distortions of opposite sign on disjoint populations cannot be corrected by any constant, and neither is visible from the score alone.

---

## 3. What the stored homepages actually reach

§10.1 names anchor text and `Article` JSON-LD as the right instruments and defers them to M2 as unassessed. M2 is done; here is the assessment. Tested against all 13 stored homepages, and reported on the 6 that carry `blog_exists = 0`, which is the population where the +25 fires.

| shop | platform | feed link | `Article` LD | blog-vocabulary anchor | verdict |
|---|---|---|---|---|---|
| germanelectronic.de → lampenflut.de | JTL | `/rss.xml` | **yes** | **"Licht-Ratgeber", "Mehr News …" → `lampenflut.de/Lampenflut-Licht-Ratgeber`** | **blog, missed** — root-level slugs |
| zecplus.de | Shopify | — | no | **"Blog" → `https://blog.zecplus.de/`** | **blog, missed** — subdomain |
| opulent-wohnen.com | JTL | `/rss.xml` | no | none | no evidence of a blog |
| smoke2u.de | JTL | `/rss.xml` | no | none | no evidence of a blog |
| verpackungskoenig.de | JTL | `/rss.xml` | no | none | no evidence of a blog |
| propellerdiscount.de | WooCommerce | `/feed/`, `/comments/feed/` | no | none | no evidence of a blog |

**Anchor text reaches both unreachable shapes, and nothing else.** Two true positives, four true negatives, on data already fetched. This is the instrument.

**`Article` JSON-LD on the homepage reaches one of the two.** It catches the root-slug shape (`lampenflut.de` renders posts on its home page) and cannot catch the subdomain shape, where the homepage links out and carries no post markup. Worth having as corroboration; not sufficient alone.

**Feed autodiscovery must be rejected, and the measurement is why.** It looked like the cheapest instrument available — one `<link>` in a `<head>` already on disk, reaching both shapes in principle. It fires on **4 of the 6**, and on every one of those four it is a **platform default**: JTL ships `/rss.xml` on every install, WordPress ships `/feed/` and `/comments/feed/` on every install. All four are shops with no blog. This is `"Powered by JTL-Shop"` again — §10.4's rule, that a removable, platform-shipped string is weak evidence of a platform and *no* evidence of anything else — and it would have converted four correct negatives into four false positives while looking like a fix.

**One honest caveat on anchor text.** Its clean sheet above is on n = 6. In the `blog_exists = 1` population the same regex fires on `doonails.de`'s *"Tipps & Tricks"* → `/pages/doonails-academy-tips-tricks`, a Shopify **page**, and on `ekomia.de`'s *"Ratgeber"* → a tag listing. Neither costs anything there, because those shops already have a detected blog — but it means the instrument's precision is untested where it would matter, i.e. on a shop with no blog and a "Ratgeber" link to a static advice page. That is the shape to watch, and it is an argument for anchor text raising detection rather than settling it.

---

## 4. What I would do, and in what order

**(a) Add anchor text as a positive detector — cheap, measured, no new requests.** Anchor *text* in the blog vocabulary, with the href taken **wherever it points**, including subdomains and adopted hosts. This is the one change here that is already evidenced: it converts the two known-false `0`s into `1`s, and A6 then samples them like any other blog. It does not close M1.14 on its own.

**(b) Reject feed autodiscovery explicitly, with the numbers**, so it is not proposed again in six weeks. It is the obvious idea, it is cheap, and it is wrong here in a way only measurement shows.

**(c) Then apply A7, which is the actual resolution.** `opp.no_blog` awards points for an absence, so by A7's one-question test it qualifies: *if this signal is missing or weak, does the rule award points?* Yes, +25. A `0` produced by a search that could not have been exhaustive is **not measured**, and must fire in neither direction and route to a person.

The discriminator between a trustworthy `0` and an untrustworthy one is available and does not need new vocabulary: **did we have anything to search?** A shop that serves a fully expanded sitemap, no content shard, no blog-vocabulary path, no blog-vocabulary anchor to any host and no `Article` markup has been searched about as well as this pipeline can search, and its `0` is a fact. A shop that serves **no sitemap at all** has not been searched — `/sitemap.xml` 404s and `robots.txt` declares none — and its `0` is an artifact of what we could not read.

On this corpus that splits the six cleanly: `germanelectronic.de`/`lampenflut.de` and `propellerdiscount.de` have **no sitemap on disk at all**; the other four serve sitemaps that were expanded. So the abstention population after (a) is small — and, worth noting, `lampenflut.de` is in it twice over, since it is also one of the two shops (a) rescues.

**(d) What it costs, stated plainly.** Under (c), `opp.no_blog` stops firing on shops with no searchable sitemap and they go to review instead. That is points *removed* from the corpus — and it lands on top of §10.3's existing warning. It is the right trade regardless: a +25 that manufactures the letter's premise is worse than a +25 not awarded, because the second is visible in a queue and the first is visible only to the recipient.

---

## 5. What this needs from you

Three things are ratification decisions, not coding ones:

1. **Whether `opp.no_blog` abstains at all** — (c) is a scoring-model change to the largest award in §6.2, in the same class as A1/M1.22.
2. **The exhaustiveness test.** "Served a sitemap we expanded" is proposed because it is the discriminator this corpus supports; it is not the only possible one.
3. **The fifth review reason.** A7 requires routing, and this would be its fifth instance. Naming it `blog_undetectable` keeps it distinct from `blog_date_unparseable` (index found, dates unreadable) and from `blog_date_unbounded` (date found, cannot bound the rule) — three different things a human does three different things about.

Item (a) — the anchor-text instrument — needs none of that. It is a §5.3 detection improvement with measured behaviour on both blocking shapes, and it can land whenever.
