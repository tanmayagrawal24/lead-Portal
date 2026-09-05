"""Which Impressum fields a deterministic parser could plausibly reach (M1.48).

A2 §8 measured this once, in a script that was never committed. Eight of its
nine rows reproduced later; one did not, and the disagreement could not be
adjudicated because there was nothing to compare instruments with. A number
whose basis cannot be re-read is M1.42 one level up — the value and the citation
computed by different expressions, where here the second expression is a
throwaway script in a transcript. So the instrument is a command.

Two properties this module exists to hold:

**Counts, never values.** §8 forbids putting an extracted personal value in a
report or a transcript. Every default output is a count of *pattern presence* on
a page — never what the pattern matched. `--show-values` is the one exception and
it prints to the operator's terminal and writes nothing; it exists because A2's
item 10 (is the PLZ + Ort candidate *accurate*, not merely present?) cannot be
answered without looking, and looking is the operator's to do.

**One selection, shared.** `select_inputs` is the only expression that decides
which artifact a company is measured on, and both the counts and the values read
it. A second expression describing what the first one does is the defect class
this project has now found five times (M1.40, M1.42, M1.43, M1.44). The rule it
implements is A2 §7 as amended: the newest 200-with-body `impressum` artifact,
excluding any whose content hash matches that company's homepage (M1.43) and any
whose URL the robots.txt **served by that body's own origin** disallows (M1.44 as
re-keyed by M1.75). "That company's newest robots.txt" was the ratified wording
and it is what produced `zecplus.de`'s vacuity: a permissive 173-byte file from
`blog.zecplus.de` applied to bodies on `www.zecplus.de`. Where no robots.txt for
a body's own origin is on disk the policy is `unavailable`, which allows
nothing — see `policy_for`.

**These are pattern-presence counts, not extraction accuracy** (§10.4). A
USt-IdNr shape on the page may belong to a payment provider; an e-mail may be in
a cookie policy. Everything here is labelled an observation, and nothing here
claims a parser would work.
"""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from portal import robots as robots_mod
from portal.parsers import provider_block, visible_text
from portal.urls import authority_of

# Named separately because `plz_ort_values` reads it directly: A2 item 10's
# accuracy check is about this one candidate.
#
# Two exclusions, both from a fixture that caught the first version matching
# things that are not addresses:
#
#   * The **HRA/HRB lookbehind.** `Registernummer: HRB 12345` followed by any
#     capitalised word is five digits and a capitalised token — the exact shape
#     of a postal code and a city. Since §5.3's provider block is precisely where
#     the register line also lives, this was not a hypothetical false positive.
#   * The **bounded city.** `visible_text` collapses whitespace *across elements*,
#     so `<p>…12345 Musterstadt</p><p>Vertreten durch…</p>` becomes one string and
#     a run of capitalised words swallows the start of the next sentence. A German
#     city name does not simply concatenate capitalised words: it joins them with
#     a hyphen (`Villingen-Schwenningen`) or a lowercase connector (`Frankfurt am
#     Main`, `Rothenburg ob der Tauber`), and `Bad` is a prefix. Encoding that
#     shape stops the span at the city, which matters most in `--show-values`,
#     where an over-long span makes a correct match look wrong.
#
# Deliberately still loose about *which* names are real: this counts a shape, and
# deciding accuracy is the operator's job (§10.4).
PLZ_ORT = re.compile(
    r"(?<!HRA )(?<!HRB )(?<![-\w])\d{5}\s+"
    r"(?:Bad\s)?[A-ZÄÖÜ][a-zäöüß]+(?:-[A-ZÄÖÜ][a-zäöüß]+)*"
    r"(?:\s(?:am|an|im|ob|bei|vor|der|den|auf)\s(?:der\s)?[A-ZÄÖÜ][a-zäöüß]+)?"
)

# Candidate patterns. Each is a *shape on the page*, deliberately loose: the
# question is "could a deterministic parser find an anchor here", not "is this
# value correct". `Inh\.` carries no trailing \b on purpose — the abbreviation is
# followed by a space, and \b after `.` requires a word character next, so the
# obvious spelling silently reports zero.
CANDIDATES: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("USt-IdNr shape", re.compile(r"\b[A-Z]{2}\s?\d{7,12}\b")),
    ("HRA/HRB number", re.compile(r"\bHR[AB]\s?\d+", re.IGNORECASE)),
    ("Amtsgericht", re.compile(r"Amtsgericht", re.IGNORECASE)),
    (
        "Tel/Telefon label",
        re.compile(r"\bTel(?:efon)?\.?\s*[:.]?\s*[+\d(]", re.IGNORECASE),
    ),
    ("e-mail shape", re.compile(r"[\w.\-+]+@[\w.\-]+\.[a-z]{2,}", re.IGNORECASE)),
    ("Inh./Inhaber marker", re.compile(r"\bInh\.|\bInhaber(?:in)?\b")),
    ("Geschaeftsfuehrer label", re.compile(r"Geschäftsführer", re.IGNORECASE)),
    ("PLZ + Ort shape", PLZ_ORT),
)

# A2 §8's table, restated on the ratified selection. Kept beside the patterns so
# a future run that disagrees is visible as a disagreement rather than as a new
# baseline — §10.4's "observed on N shops" discipline applied to the instrument.
#
# The denominator is **11, not 12**, and that is the point of committing this.
# The restatement was first written from a scratch measurement that applied only
# M1.43's homepage-hash guard; running the real command applied M1.44's robots
# guard too, `snocks.com` lost its last usable Impressum, and every count that
# page had contributed fell by one. A2 §8's numbers have now been wrong twice for
# the same reason — the selection was described in one place and measured in
# another — which is why `select_inputs` is the only expression allowed to decide.
OBSERVED_2026_08_16: dict[str, int] = {
    "provider block locatable": 10,
    "USt-IdNr shape": 10,
    "HRA/HRB number": 4,
    "Amtsgericht": 3,
    "Tel/Telefon label": 8,  # in-block; 9 whole-page
    "e-mail shape": 10,
    "Inh./Inhaber marker": 1,
    "Geschaeftsfuehrer label": 5,
    "PLZ + Ort shape": 8,
}
#: The 2026-08-16 baseline, kept at its measured value. **It predates M1.75 and
#: the current corpus yields 9, not 11**: `smoke2u.de` and `propellerdiscount.de`
#: were measured under their apex sibling's robots.txt and are now refused as not
#: verifiable. The number is not moved to match, because it is a record of what
#: was observed on a date and the divergence is the finding — the report prints
#: `<-- was n/11` and an operator should see it.
OBSERVED_PAGES = 11

# Newest 200-with-body impressum artifact per company, minus the homepage-hash
# class (M1.43). The robots exclusion (M1.44) needs the parser and so is applied
# in Python, over candidates ordered newest-first.
_CANDIDATE_SQL = """
SELECT c.id AS company_id, c.domain, a.id, a.url, a.body_path
FROM company c
JOIN artifact a ON a.company_id = c.id
WHERE a.kind = 'impressum'
  AND a.http_status = 200
  AND a.body_path IS NOT NULL
  AND NOT EXISTS (
        SELECT 1 FROM artifact h
        WHERE h.company_id = a.company_id
          AND h.kind = 'homepage'
          AND h.content_hash = a.content_hash)
ORDER BY c.domain, a.id DESC
"""

# Every readable robots.txt the company has, newest first. The origin match is
# applied in Python because `authority_of` is the project's single expression for
# it (`urls.authority_of`, and `fetch.py` filters the same way); re-deriving it
# in SQL would be a second expression for one fact, which is M1.42's shape.
_ROBOTS_SQL = """
SELECT url, body_path, http_status FROM artifact
WHERE company_id = ? AND kind = 'robots' AND http_status IS NOT NULL
ORDER BY id DESC
"""


@dataclass(frozen=True)
class Input:
    """The one artifact a company is measured on, and why it was chosen."""

    company_id: int
    domain: str
    artifact_id: int
    url: str
    body_path: str


@dataclass(frozen=True)
class Skipped:
    domain: str
    reason: str


@dataclass(frozen=True)
class Audit:
    inputs: list[Input]
    skipped: list[Skipped]
    present: dict[str, int]  # pattern -> pages where it appears anywhere
    in_block: dict[str, int]  # pattern -> pages where it appears in the block


def policy_for(
    conn: sqlite3.Connection, company_id: int, url: str, root: Path
) -> robots_mod.RobotsPolicy:
    """The robots.txt **served by `url`'s own origin**, or a policy that allows
    nothing because we cannot tell whose file governed it (M1.75).

    Never returns None, and never falls back to a sibling origin. `authority_of`
    keys the match, so `www.zecplus.de` and `blog.zecplus.de` are separate
    authorities that do not stand in for one another — which is RFC 9309's rule
    and was the whole of M1.61.

    The two negative outcomes are different objects and must stay that way:

    * `unrestricted` — a robots.txt for this origin was read and states no rules,
      **or the origin answered 4xx**, which is the same statement made by
      absence (M1.122, RFC 9309 §2.3.1.2). The shop declared nothing.
    * `unavailable` — **no robots.txt for this origin is on disk.** We cannot
      establish whose file this was. Nothing is allowed, and the reason names the
      authority so an operator knows which host to go and fetch.

    Collapsing those two would restore exactly the vacuity this closes: the
    permissive reading is the one that lets a body through under a policy nobody
    read (H1), and it is the reading `None` used to produce.
    """
    authority = authority_of(url)
    for row in conn.execute(_ROBOTS_SQL, (company_id,)):
        if authority_of(str(row["url"])) != authority:
            continue
        # M1.122: the status decides, exactly as it does live. A 4xx row is an
        # ANSWER — this origin serves no robots.txt — and `robots.for_stored`
        # is the same expression `for_response` uses, so the two cannot drift
        # again. Only a 200 has a body worth reading.
        status = int(row["http_status"])
        body = None
        if status == 200 and row["body_path"]:
            body = (root / row["body_path"]).read_text(
                encoding="utf-8", errors="replace"
            )
        elif status == 200:
            # A 200 whose body never landed says nothing; keep looking.
            continue
        return robots_mod.for_stored(status, body)
    # M1.75. A content-hash collapse can absorb this origin's fetch into a
    # sibling's row, so "no row" does not mean "never fetched" — it means the
    # table cannot say, and only a re-fetch can. Over-reporting here costs a
    # company a run and says why; under-reporting it is H1.
    return robots_mod.unavailable(f"no robots.txt stored for origin {authority}")


def select_inputs(
    conn: sqlite3.Connection, root: Path
) -> tuple[list[Input], list[Skipped]]:
    """A2 §7 as amended by M1.43 and M1.44 — the single selection expression.

    Returns the chosen artifact per company plus the companies that have none,
    because "no usable Impressum" is a finding (it is `snocks.com`'s state after
    both repairs) and not an empty row to be dropped silently.
    """
    chosen: dict[int, Input] = {}
    rejected: dict[int, str] = {}
    policies: dict[tuple[int, str], robots_mod.RobotsPolicy] = {}
    for row in conn.execute(_CANDIDATE_SQL):
        company_id = int(row["company_id"])
        if company_id in chosen:
            continue  # ORDER BY a.id DESC — the first survivor is the newest
        # Keyed on the body's own origin (M1.75), so the cache key is the
        # authority and not the company — a company with two origins has two
        # policies and they do not substitute for each other.
        url = str(row["url"])
        key = (company_id, authority_of(url))
        if key not in policies:
            policies[key] = policy_for(conn, company_id, url, root)
        policy = policies[key]
        if not policy.allows(url):
            if policy.unavailable is not None:
                # Not "this was disallowed" — "we cannot tell what governed it".
                # A different finding, and it sends a person somewhere else.
                rejected.setdefault(company_id, f"{policy.unavailable} (M1.75)")
            else:
                # M1.44: fetched before the redirect guard existed, still on disk.
                rejected.setdefault(company_id, "robots-disallowed body (M1.44)")
            continue
        chosen[company_id] = Input(
            company_id=company_id,
            domain=row["domain"],
            artifact_id=int(row["id"]),
            url=row["url"],
            body_path=row["body_path"],
        )

    skipped: list[Skipped] = []
    for row in conn.execute("SELECT id, domain FROM company ORDER BY domain"):
        company_id = int(row["id"])
        if company_id not in chosen:
            skipped.append(
                Skipped(
                    row["domain"],
                    rejected.get(company_id, "no 200 Impressum artifact with a body"),
                )
            )
    return sorted(chosen.values(), key=lambda i: i.domain), skipped


def audit(conn: sqlite3.Connection, root: Path) -> Audit:
    inputs, skipped = select_inputs(conn, root)
    present = dict.fromkeys([n for n, _ in CANDIDATES], 0)
    in_block = dict(present)
    present["provider block locatable"] = 0
    in_block["provider block locatable"] = 0

    for chosen in inputs:
        text = visible_text(
            (root / chosen.body_path).read_text(encoding="utf-8", errors="replace")
        )
        block = provider_block(text)
        if block:
            present["provider block locatable"] += 1
            in_block["provider block locatable"] += 1
        for name, pattern in CANDIDATES:
            if pattern.search(text):
                present[name] += 1
            if block and pattern.search(block):
                in_block[name] += 1
    return Audit(inputs, skipped, present, in_block)


def report(result: Audit) -> str:
    """Counts only. No matched text reaches this string (§8)."""
    n = len(result.inputs)
    lines = [
        f"Impressum Phase-1 candidates — {n} page(s), one per company",
        "selection: newest 200-with-body impressum artifact, excluding any whose",
        "content hash matches that company's homepage (M1.43) and any whose URL",
        "the robots.txt served by its own origin disallows (M1.44/M1.75).",
        "An origin with no robots.txt on disk is NOT VERIFIABLE, not allowed.",
        "",
        "PATTERN PRESENCE, NOT EXTRACTION ACCURACY (§10.4).",
        "",
        f"  {'candidate':30} {'whole page':>12} {'in block':>10} {'2026-08-16':>12}",
    ]
    order = ["provider block locatable"] + [name for name, _ in CANDIDATES]
    for name in order:
        was = OBSERVED_2026_08_16.get(name)
        # `Tel/Telefon label` is recorded in-block, where the whole-page count is
        # the noisier one; every other row is recorded whole-page.
        now = (
            result.in_block[name]
            if name == "Tel/Telefon label"
            else result.present[name]
        )
        mark = ""
        if was is not None and (was != now or n != OBSERVED_PAGES):
            mark = f"  <-- was {was}/{OBSERVED_PAGES}"
        lines.append(
            f"  {name:30} {result.present[name]:>7}/{n:<4} "
            f"{result.in_block[name]:>5}/{n:<4} "
            f"{'' if was is None else f'{was}/{OBSERVED_PAGES}':>12}{mark}"
        )
    if result.skipped:
        lines.append("")
        lines.append("  no usable Impressum artifact:")
        for row in result.skipped:
            lines.append(f"    {row.domain:26} {row.reason}")
    return "\n".join(lines)


def plz_ort_values(conn: sqlite3.Connection, root: Path) -> list[tuple[str, list[str]]]:
    """The PLZ + Ort spans, for the operator's accuracy check (A2 item 10).

    Returns values rather than counts, and is therefore the one function in this
    module whose output must never be written anywhere. Only the matched span is
    returned — not the surrounding block, which carries the street and, on a sole
    trader's Impressum, a natural person's name.
    """
    inputs, _ = select_inputs(conn, root)
    out: list[tuple[str, list[str]]] = []
    for chosen in inputs:
        text = visible_text(
            (root / chosen.body_path).read_text(encoding="utf-8", errors="replace")
        )
        block = provider_block(text)
        spans = [m.group(0).strip() for m in PLZ_ORT.finditer(block or "")]
        seen: list[str] = []
        for span in spans:
            if span not in seen:
                seen.append(span)
        out.append((chosen.domain, seen))
    return out
