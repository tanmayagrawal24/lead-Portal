"""§5.5b's paid extraction — the entry point, gated, with nothing spent (M1.83).

**What this is, and what it deliberately is not.** 9a builds the *seam*: the
selection of what to send, the cleaning and capping of the text, the batch
requests, and the ledger gate they cannot get past without a clearance. It stops
at the point where §7 control 4's reservation would be made, because that
reservation has to make its two writes in one transaction (M1.72) and that needs
the caller 9b builds. **Nothing here contacts a provider, and every test drives
a fake.**

**Why the gate is the point of landing this now.** `llm.assert_ledger_guarded`
has been correct and unexercised since Unit 7: it fails the **import** on a
callable in `portal/llm.py` that is classified as neither paid nor free, and on
a paid one with no `@requires_ledger_clearance`. Until now no caller existed to
prove it engages on a real path. `submit` below is registered paid and
decorated, so it cannot run without a `LedgerClearance`, which only
`ledger.check_ceiling` constructs — and the negative control for this unit
removes the registration and watches the import fail.

**The client is injected (Unit 2's shape).** `llm.LLMProvider` is a Protocol, so
this module names no vendor, imports no SDK, and every test passes a fake. That
is what makes a paid stage testable under the CI M1.65 built, which forbids
`ANTHROPIC_API_KEY` outright.
"""

from __future__ import annotations

import sqlite3
import sys
from dataclasses import dataclass
from pathlib import Path

from portal import impressum_audit, llm, parsers
from portal.ledger import LedgerClearance

#: §5.5b, verbatim. The instruction is part of the specification, not a prompt
#: detail: hallucinated Impressum data is the worst failure this stage has.
PROMPT_DISCIPLINE = (
    "Return null for any field not present on the page. Do not infer, do not "
    "guess, do not fill from general knowledge. If the page is not an "
    "Impressum, return all nulls."
)

#: §5.5b: *"cap at 60 KB"*, and it is called the primary defence against
#: unbounded token spend rather than an optimisation. Measured in bytes of the
#: cleaned text, not characters, because the cap exists to bound what crosses
#: the wire.
INPUT_CAP_BYTES = 60 * 1024

#: §5.5b: pages over the cap are truncated **from the end** — Impressum content
#: is near the top of an Impressum page, so the tail is what can be lost.
TRUNCATION_MARKER = "\n[… gekürzt auf 60 KB, §5.5b]"

#: Haiku 4.5 caps output at 64K (M1.50), and `llm.reserve_batch` refuses a
#: request asking for more. These extractions are a few hundred tokens of JSON;
#: the number is a bound for the reservation, not a target.
MAX_OUTPUT_TOKENS = 2048


@dataclass(frozen=True)
class Prepared:
    """One company's extraction input, with the text that will be sent.

    `sent_text` is carried on the object rather than recomputed, so that
    `verify.PageText` is built from **the string that was sent** and cannot be
    handed a different rendering of the same page (M1.43).
    """

    company_id: int
    domain: str
    artifact_id: int
    url: str
    kind: str
    sent_text: str
    truncated: bool


def clean(html: str) -> tuple[str, bool]:
    """§5.5b's input preparation, and M1.78's ruling on which text is sent.

    **Cleaned visible text, not raw HTML.** §5.5b's own requirement — strip
    `<script>`, `<style>`, `<svg>`, `<nav>`, comments; reduce to text — *is*
    `parsers.visible_text`'s contract, so this is one expression for it rather
    than a second one that could drift. Two consequences, both wanted: the model
    sees what a human reading the page sees, which is the only footing on which
    a substring check against "the cleaned page text" means anything; and
    §10.2's `Inh.`/`Inhaber` base rate is fixed at the visible-text reading
    (1 of 11) rather than the raw-HTML one, whose extra hits are tokens inside
    `<script>` blocks that `visible_text` decomposes on purpose.
    """
    text = parsers.visible_text(html)
    encoded = text.encode("utf-8")
    if len(encoded) <= INPUT_CAP_BYTES:
        return text, False
    kept = encoded[:INPUT_CAP_BYTES].decode("utf-8", errors="ignore")
    return kept + TRUNCATION_MARKER, True


def impressum_schema() -> dict[str, object]:
    """`ImpressumExtract` (§5.5b) as a JSON schema, made strict.

    Written out rather than derived from the Pydantic model at import time: the
    contract the provider is held to is the thing under review, and generating
    it means reviewing a generator instead. `llm.strict_json_schema` then adds
    `required` and `additionalProperties: false` for every object, so a field
    the model omits is an error rather than a silent `None` (§5.5b's null is an
    explicit null).
    """
    string = {"type": ["string", "null"]}
    return llm.strict_json_schema(
        {
            "type": "object",
            "properties": {
                "legal_name": string,
                "legal_form": string,
                "street": string,
                "postal_code": string,
                "city": string,
                "country": string,
                "managing_directors": {"type": "array", "items": {"type": "string"}},
                "owner_name": string,
                "register_court": string,
                "register_number": string,
                "vat_id": string,
                "email": string,
                "phone": string,
            },
        }
    )


def homepage_schema() -> dict[str, object]:
    """`HomepageExtract` (§5.5b), including M1.49's two `_evidence` spans.

    The spans are what give the two booleans anything to verify at all — a
    boolean has no string in it for a substring check to find — and §5.5b states
    the limit of what they buy: they prove the model did not fabricate its
    evidence, not that it reasoned correctly from it.
    """
    string = {"type": ["string", "null"]}
    return llm.strict_json_schema(
        {
            "type": "object",
            "properties": {
                "one_line_offer": string,
                "product_categories": {"type": "array", "items": {"type": "string"}},
                "audience": string,
                "owner_named_on_site": {"type": "boolean"},
                "owner_named_evidence": string,
                "own_brand": {"type": ["boolean", "null"]},
                "own_brand_evidence": string,
                "agency_credit": string,
            },
        }
    )


_SYSTEM = {
    "impressum": (
        "Du liest eine deutschsprachige Impressum-Seite eines Onlineshops und "
        "gibst die gesetzlich geforderten Angaben strukturiert zurück. "
        + PROMPT_DISCIPLINE
    ),
    "homepage": (
        "Du liest die Startseite eines deutschsprachigen Onlineshops und gibst "
        "zurück, was der Shop verkauft und wie er sich darstellt. Belege jede "
        "Ja/Nein-Angabe mit der wörtlich zitierten Stelle der Seite, an der du "
        "sie abliest. " + PROMPT_DISCIPLINE
    ),
}


def prepare(
    conn: sqlite3.Connection, root: Path
) -> tuple[list[Prepared], list[impressum_audit.Skipped]]:
    """What would be sent, per company. **Free, and makes no request.**

    Selection is `impressum_audit.select_inputs` — A2 §7 as amended by M1.43 and
    M1.44 — reused rather than re-expressed. It is already the project's single
    expression for *which stored Impressum a company is measured on*, it already
    excludes an artifact whose content hash matches that company's homepage (the
    `snocks.com` row that is the homepage filed as an Impressum) and any body the
    company's own origin's robots.txt disallows, and re-deriving the same rule
    here is exactly the second expression M1.42 is about. A company with no
    usable Impressum is returned as `Skipped`, not dropped: "no usable
    Impressum" is a finding.
    """
    inputs, skipped = impressum_audit.select_inputs(conn, root)
    prepared: list[Prepared] = []
    for chosen in inputs:
        body = (root / chosen.body_path).read_text(encoding="utf-8", errors="replace")
        sent, truncated = clean(body)
        prepared.append(
            Prepared(
                company_id=chosen.company_id,
                domain=chosen.domain,
                artifact_id=chosen.artifact_id,
                url=chosen.url,
                kind="impressum",
                sent_text=sent,
                truncated=truncated,
            )
        )
    return prepared, skipped


def build_requests(prepared: list[Prepared]) -> list[llm.BatchRequest]:
    """One `BatchRequest` per prepared page. **Free.**

    `custom_id` carries the company id and the artifact id, because batch
    results come back in **arbitrary order** (M1.51) and this is the only thing
    tying a returned legal name to the company it was read for. Reading result
    *n* as request *n* attributes a name and a set of directors to the wrong
    company — M1.17's failure with a new cause — and **substring verification
    does not catch it**, because the values are genuinely present on the page
    they came from. The artifact id rides along so the signal's `evidence_url`
    and `artifact_id` come from the row the text was read off (M1.42).
    """
    schema = {"impressum": impressum_schema(), "homepage": homepage_schema()}
    return [
        llm.BatchRequest(
            custom_id=f"{page.kind}:{page.company_id}:{page.artifact_id}",
            system=_SYSTEM[page.kind],
            user_text=page.sent_text,
            json_schema=schema[page.kind],
            max_tokens=MAX_OUTPUT_TOKENS,
        )
        for page in prepared
    ]


def parse_custom_id(custom_id: str) -> tuple[str, int, int]:
    """The inverse of `build_requests`' key, in one place so the two cannot
    disagree about what a `custom_id` means."""
    kind, company_id, artifact_id = custom_id.split(":")
    return kind, int(company_id), int(artifact_id)


@llm.requires_ledger_clearance
def submit(
    provider: llm.LLMProvider,
    requests: list[llm.BatchRequest],
    *,
    clearance: LedgerClearance,
) -> str:
    """Hand the batch to the injected provider. **The one paid surface here.**

    It is registered in `PAID_SURFACES` below and decorated above, so
    `assert_ledger_guarded` fails the **import** if either is ever removed, and
    the decorator refuses the call without a `LedgerClearance` — which only
    `ledger.check_ceiling` constructs, after reading §7 control 2's rolling
    30-day window against the ceiling.

    **9a stops here and 9b continues.** What is missing is §7 control 4's
    reservation: `llm.reserve_batch` measures the tokens and prices them, and
    the resulting estimate must be written to `llm_batch.est_cost_usd` and
    `run.est_cost_usd` **in one transaction** (M1.72), or a crash between the
    two leaves the batch on the books and the ledger blind to it. That is the
    one path in §7 that fails open, it needs the caller, and building it half
    way would be worse than not building it: a reservation that usually commits
    both writes is a ledger that is usually right.
    """
    return provider.submit_batch(requests, clearance=clearance)


#: §7 control 2's classification, in `assert_ledger_guarded`'s shape. The free
#: list is written out longhand for the reason the assertion exists: the check
#: that matters is the third one, the new paid path nobody classified, and it
#: only works if every callable is named somewhere.
PAID_SURFACES: tuple[str, ...] = ("submit",)
FREE_SURFACES: tuple[str, ...] = (
    "build_requests",
    "clean",
    "homepage_schema",
    "impressum_schema",
    "parse_custom_id",
    "prepare",
)

llm.assert_ledger_guarded(
    sys.modules[__name__],
    paid=PAID_SURFACES,
    free=FREE_SURFACES,
    where="portal.extract_p2",
)


__all__ = [
    "FREE_SURFACES",
    "INPUT_CAP_BYTES",
    "MAX_OUTPUT_TOKENS",
    "PAID_SURFACES",
    "PROMPT_DISCIPLINE",
    "Prepared",
    "build_requests",
    "clean",
    "homepage_schema",
    "impressum_schema",
    "parse_custom_id",
    "prepare",
    "submit",
]
