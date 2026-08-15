# extract-p1 fixtures

Two kinds of file live here, and the difference is deliberate.

**Harvested (`platform-*.html`).** Real markup fragments taken from the first
crawl's stored homepages, trimmed to the smallest span carrying the platform
signature. These contain no personal data — they are script tags, CSS class
names and asset paths — so they are committed as they were served. Provenance
is in a comment at the top of each file.

**Hand-written (`impressum-*.html`).** Modelled on the structures observed in
the crawled Impressum pages, with `Max Mustermann` / `Musterstraße 1` / `50667
Musterstadt` throughout.

They are hand-written rather than redacted, and that was a decision made after
trying the other way. An Impressum is *made of* personal data: names appear in
positions no role-anchored pattern anticipates (`Geschäftsführer: A · B`, a
surname trailing a company name), and the first redaction pass left a real
director, a real town and a partial VAT number in a file that was about to be
committed. A redactor that has to be perfect on adversarial input is the wrong
tool when the alternative is under our control — and the real names were never
what the parser needed. What it needs is the *structure*: which anchor phrase
introduces the block, what noise precedes it, whether a legal form is stated at
all. That is reproduced exactly.

`impressum-vendor-noise-first.html` carries the case that matters most: a
cookie-consent vendor's own `GmbH` appearing before the operator's details.
A naive first-match-in-page returned the vendor on two real shops, and a
trust-seal `e.V.` on a third.
