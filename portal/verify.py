"""§5.5b's substring verification — and the exact limit of what it buys.

**The whole design of Phase 2 leans on one backstop**: an LLM-extracted value is
kept only if it is *literally present* in the text the model was shown. A value
that is not on the page is discarded and its signal written with `confidence=0`
for review (§5.5b). Hallucinated Impressum data is the worst failure this
project has — it puts a confident wrong name in a letter to a stranger — and
this module is the check that stands between the model and that letter.

**It takes the SENT TEXT as an argument and cannot reach an artifact.** That is
a structural choice, not an ergonomic one. M1.43's defect was a measurement
described in one place and applied in another; verifying an extraction against
a document the model was never shown is the same shape and would be undetectable
— the check would pass or fail for reasons unrelated to what the model saw. So
there is no `conn` here, no `Path`, and no way to load a body. The caller holds
the one string it sent and passes it in.

**What this proves, and what it cannot (M1.47, ratified M1.49).**

* For a **quoted value** — `legal_name`, a director's name, `owner_name` — the
  verified string *is* the scored value. Presence on the page is real evidence
  that the model read rather than invented.
* For a **boolean** — `own_brand`, `owner_named_on_site` — there is no string in
  the value for a check to find, so §5.5b gives each an `_evidence` span and
  this module verifies the span instead. **That is weaker, and weaker in a
  specific way: it proves the model did not fabricate its evidence, and it
  cannot catch the model reading the page correctly and inferring wrongly.** A
  homepage may genuinely contain *"unsere eigene Marke"* in a sentence about a
  brand the shop resells. The verified string is *adjacent* to the value rather
  than being it.

That limit is written here as well as in the spec, because a guard believed to
be stronger than it is, is how a rule ends up trusted.

**What normalisation does and does not do.** Whitespace is collapsed and case is
folded, because a page that wraps a name across two lines has still stated it,
and neither difference is evidence of anything. Nothing else is normalised — no
accent folding, no punctuation stripping, no fuzzy distance. Every one of those
would make the check *more likely to pass*, which is the wrong direction for a
guard whose failure mode is a wrong name in a letter, and none has ever been
measured on this corpus. M1.4's rule: do not build against an unobserved case.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

#: The two confidence values this module produces. §4 allows 0–1 for `method =
#: 'llm'`; §9 renders 0 red. Nothing in between is used, because nothing in
#: between has been measured — a graded score here would be a number with no
#: instrument behind it.
VERIFIED = 1.0
REJECTED = 0.0

_WHITESPACE = re.compile(r"\s+")


def normalise(text: str) -> str:
    """Collapse whitespace and fold case. Nothing else — see the module note."""
    return _WHITESPACE.sub(" ", text).strip().casefold()


@dataclass(frozen=True)
class Verdict:
    """One field's verification result, carrying what was checked.

    `value` survives a rejection deliberately. A2 §3: a rejected `legal_name` is
    written to the signal **with the rejected string in `value_text`**, because
    a red row in §9 with no value tells the operator nothing to go and check. A
    person's name is the exception and is handled by the caller, not here — §8
    keeps personal data in `contact`, and an unverified name creates no `contact`
    row and is written nowhere.
    """

    field: str
    value: str
    verified: bool

    @property
    def confidence(self) -> float:
        return VERIFIED if self.verified else REJECTED


class PageText:
    """The text that was sent to the model, and the only thing checked against.

    Constructed from the sent string. There is deliberately no constructor that
    takes an artifact id, a path or a connection: see the module note.
    """

    __slots__ = ("_normalised", "raw")

    def __init__(self, sent_text: str) -> None:
        self.raw = sent_text
        self._normalised = normalise(sent_text)

    def contains(self, value: str | None) -> bool:
        """`None` and empty are **not** verified and **not** rejected by this —
        they are absences, and the caller decides what an absence means. §5.5b
        instructs the model to return `null` for a field not on the page, so a
        `null` is the model obeying, not the model failing. Returning `False`
        here would let a caller record a rejection for a field the model
        correctly declined to answer.
        """
        if not value or not value.strip():
            raise ValueError(
                "verify.contains was given an absent value; an absent field is "
                "not a failed verification (§5.5b instructs the model to return "
                "null) and the caller must distinguish the two"
            )
        return normalise(value) in self._normalised

    def check(self, field: str, value: str) -> Verdict:
        return Verdict(field, value, self.contains(value))

    def check_boolean(self, field: str, evidence: str | None) -> Verdict | None:
        """A boolean is verified through its `_evidence` span (M1.49).

        Returns `None` where no span was supplied — which is an *absence*, the
        same state as the model returning `null` for the boolean itself, and it
        routes to §6.1's third state rather than to a rejection. A boolean with
        a value and no evidence is the one case worth naming: it is a judgement
        with nothing behind it, and it is rejected rather than trusted.
        """
        if evidence is None or not evidence.strip():
            return None
        return self.check(field, evidence)


__all__ = ["REJECTED", "VERIFIED", "PageText", "Verdict", "normalise"]
