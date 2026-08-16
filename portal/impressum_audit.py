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
whose URL that company's newest stored robots.txt disallows (M1.44).

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

_ROBOTS_SQL = """
SELECT body_path FROM artifact
WHERE company_id = ? AND kind = 'robots' AND http_status = 200
  AND body_path IS NOT NULL
ORDER BY id DESC LIMIT 1
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


def _policy(conn: sqlite3.Connection, company_id: int, root: Path):
    row = conn.execute(_ROBOTS_SQL, (company_id,)).fetchone()
    if row is None:
        return None
    return robots_mod.parse(
        (root / row["body_path"]).read_text(encoding="utf-8", errors="replace")
    )


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
    policies: dict[int, object] = {}
    for row in conn.execute(_CANDIDATE_SQL):
        company_id = int(row["company_id"])
        if company_id in chosen:
            continue  # ORDER BY a.id DESC — the first survivor is the newest
        if company_id not in policies:
            policies[company_id] = _policy(conn, company_id, root)
        policy = policies[company_id]
        if policy is not None and not policy.allows(row["url"]):
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
        "its newest stored robots.txt disallows (M1.44).",
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
