# Seed lists

`portal fetch --seed <file.csv>` reads a CSV with a required `domain` column.
Everything else is optional:

```csv
domain,legal_name,city,postal_code,country
example.de,Beispiel GmbH,Köln,50667,DE
```

- `domain` is normalised on load — scheme, `www.`, path and case are stripped,
  so `https://WWW.Example.de/shop` and `example.de` are the same row.
- `country` must be `DE`, `AT` or `CH` when present (the `company.country`
  CHECK constraint, §4).
- Blank lines and rows whose domain starts with `#` are skipped.
- Duplicate domains are collapsed, per §5.1.
- A malformed row **fails the load**. Seed lists are hand-written and short; a
  silently dropped line is a lead that vanishes without anyone noticing.

## Which lists live here

`example.csv` contains one domain — Creative Potatoes' own site — and exists so
the pipeline can be smoke-tested end to end without touching a stranger's
server.

**Real prospect lists are Tanmay's to approve before any crawl.** Nothing in
this repository should add third-party domains to a seed file without that
approval; §5.2's politeness rules exist precisely because the other end of
every request is someone else's server.
