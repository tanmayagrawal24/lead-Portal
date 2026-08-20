"""Ruleset v3 (§6), declared as data rather than as branches.

**Why this is a table of objects and not an if/elif chain (B6, §5.4).** Two
requirements in the spec cannot be met by control flow:

1. `remaining_upside(c)` is "the sum of the maximum positive points of every
   rule Phase 2 can still influence for `c`". That is a query over the ruleset.
   A chain of branches has no ruleset to query, so the number would have to be
   maintained by hand next to the rules it summarises — which is exactly how D1
   arrived at a `PHASE2_MAX_POINTS` of 35 against a correct 50 (M1.21).
2. §5.4 requires an assertion **at startup** that every rule declares its
   Phase-2 reachability, so that adding a rule cannot silently widen or narrow
   the gate. There is nothing to assert about a branch.

So every rule carries its id, its points, the profile fields it reads, its
Phase-2 reachability, and its German reason — and `remaining_upside` and the
assertion both read the collection.

**Three outcomes, not two.** A rule `FIRES`, `DECLINES`, or `ABSTAINS`. The
third is A7 (§5) made structural: a rule whose inputs cannot carry it fires in
neither direction and says why, and the reason is written as a `score_component`
with **0 points** so the abstention is visible per company in the score itself
rather than inferred from a gap.

**Reasons are German, letter-ready, and interpolate the evidence.** They are the
deliverable, not a label: `"Letzter Blogbeitrag: März 2023."` and never
`"blog_stale=true"`.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import date

#: §6.5. Named because §5.4's gate compares against the B floor, not the A one.
BAND_FLOOR = {"A": 75, "B": 55, "C": 30}
RULESET_VERSION = "v3"

#: §6.2 and §6.3 both call six months 180 days; kept in one place so they
#: cannot drift apart.
SIX_MONTHS = 180
ONE_YEAR = 365

FIRES, DECLINES, ABSTAINS = "fired", "declined", "abstained"

_MONTHS_DE = (
    "Januar", "Februar", "März", "April", "Mai", "Juni",
    "Juli", "August", "September", "Oktober", "November", "Dezember",
)  # fmt: skip


def month_year(day: date) -> str:
    """`März 2023` — how a date is named in a letter, not `2023-03-12`."""
    return f"{_MONTHS_DE[day.month - 1]} {day.year}"


def months_since(day: date, today: date) -> int:
    return max(0, (today.year - day.year) * 12 + today.month - day.month)


@dataclass(frozen=True)
class Outcome:
    """What one rule decided about one company."""

    state: str
    reason: str
    #: §6.4 routing, raised the moment the rule abstains (A7a — a measurement
    #: limit, which running again does not change).
    review_reason: str | None = None
    #: §6.4 routing for an A7b transient: the request may land next time, so
    #: this is raised only once the abstention has persisted N runs on N
    #: distinct days. `score` counts; the rule only names where it would go.
    persistent_review_reason: str | None = None


def fires(reason: str) -> Outcome:
    return Outcome(FIRES, reason)


def declines() -> Outcome:
    return Outcome(DECLINES, "")


def abstains(
    reason: str,
    review_reason: str | None = None,
    persistent_review_reason: str | None = None,
) -> Outcome:
    return Outcome(ABSTAINS, reason, review_reason, persistent_review_reason)


#: A7b's routing, once the retry policy is exhausted (migration 009). Every
#: instance is a page that was identified and never returned 200, and they share
#: one reason because they send a person to answer one question: does this URL
#: load for you?
PERSISTENT_FETCH = "fetch_persistently_failing"


#: A company row from `company_profile`, as a plain mapping. Scoring never sees
#: the database, which is what makes it a pure function of this one argument.
Profile = Mapping[str, object]


@dataclass(frozen=True)
class Rule:
    """One §6 rule. Every field is required; none has a default.

    `phase2_reachable` in particular has no default **on purpose** — a rule that
    forgets to declare it must fail construction rather than inherit a guess.
    Inference from signal names is what produced the wrong bound in the first
    place (M1.22).
    """

    id: str
    points: int
    section: str
    reads: tuple[str, ...]
    phase2_reachable: bool
    evaluate: Callable[[Profile, date], Outcome]
    #: *Has Phase 2 already answered this, for **this** company?* — a different
    #: question from `phase2_reachable`'s *could it, for anyone?*, and the one
    #: §5.4's bound needs (M1.82). Required on every Phase-2-reachable rule and
    #: refused on every other; `assert_declared` enforces both directions.
    #:
    #: **Deliberately not derived from outcomes.** `evaluate` records no
    #: component at all for a rule that DECLINES, and `assert_declared` refuses
    #: a rule worth zero, so `score_component` holds fired rules and abstentions
    #: and nothing else. A Phase-2 `false` — the commonest outcome, and exactly
    #: the one that should tighten the bound — is a decline, and would be
    #: invisible to any outcome-based reading and indistinguishable from *never
    #: evaluated*.
    phase2_input_settled: Callable[[Profile], bool] | None = None
    #: Ladder membership (§6.2). Rules sharing a chain are evaluated in
    #: declaration order and the first that does not decline stops the chain.
    chain: str | None = None

    @property
    def upside(self) -> int:
        """What Phase 2 could still add. A negative rule can only subtract, so
        it contributes nothing to a bound on the maximum (§5.4)."""
        return max(0, self.points)


# ── helpers over the profile ────────────────────────────────────────────


def _num(profile: Profile, key: str) -> float | None:
    value = profile.get(key)
    return None if value is None else float(value)


def _text(profile: Profile, key: str) -> str:
    value = profile.get(key)
    return "" if value is None else str(value)


def _day(profile: Profile, key: str) -> date | None:
    value = profile.get(key)
    if value is None:
        return None
    return value if isinstance(value, date) else date.fromisoformat(str(value))


def _dates(profile: Profile, key: str) -> list[date]:
    raw = _text(profile, key)
    return [date.fromisoformat(part) for part in raw.split(",") if part]


# ── §5.4's settledness predicates (M1.82) ───────────────────────────────
#
# These read A2's **stage facts** — `llm.homepage_extracted` and
# `llm.impressum_extracted`, written whenever the extraction ran whatever it
# returned — and nothing else can answer what they answer. Every A7 guard in
# this project works by declining to write, so without a positive fact beside
# the silence there is no way to tell *the model read the page and could not
# tell* from *Phase 2 never ran here*, and those two states must move the bound
# in opposite ways.


def _homepage_read(profile: Profile) -> bool:
    """`qual.own_brand`'s only input is the homepage extraction."""
    return _num(profile, "homepage_extracted") == 1


def _impressum_read(profile: Profile) -> bool:
    return _num(profile, "impressum_extracted") == 1


def _owner_operated_settled(profile: Profile) -> bool:
    """Disjuncts 2 and 3 are Phase 2's, and they read *different pages* — the
    Impressum for `gf_count`, the homepage for `owner_named`. The rule is
    settled only when both have been read: one page alone leaves a disjunct
    that could still award the +15, and dropping it from the bound then would
    stop a company on points that are genuinely still available.
    """
    return _impressum_read(profile) and _homepage_read(profile)


def _never_settled(_profile: Profile) -> bool:
    """`opp.ai_invisible`'s input is M6's, not M5's. Declared explicitly rather
    than left off, because `assert_declared` refuses a Phase-2-reachable rule
    with no declaration and *"M6 has not run"* is a real answer to the question
    the field asks — not an omission. Revisit when M6 writes `ai.checked_at`.
    """
    return False


def _slow_site_settled(profile: Profile) -> bool:
    """§5.5a's PageSpeed measurement is a number or it is nothing: there is no
    *ran and could not tell* state, so a written score settles the rule and an
    absent one does not. It needs no stage fact of its own — unlike the two
    booleans, where the value and the fact of having looked are separable.
    """
    return _num(profile, "lighthouse_perf") is not None


def _has_agency_settled(profile: Profile) -> bool:
    """`neg.has_agency` contributes `max(0, -20) = 0` to the bound, so this can
    never change an admission. Declared anyway, because `assert_declared`
    requires it of every Phase-2-reachable rule and an exemption for "it does
    not matter here" is how the next −20 rule slips through undeclared.

    It reads the homepage fact rather than `agency.footer_credit_llm`, because
    M1.77 gives that key no reader — including this one.
    """
    return _homepage_read(profile)


# ── §6.1 qualification ──────────────────────────────────────────────────

OWNER_OPERATED_FORMS = ("e.K.", "Einzelunternehmen", "GbR")


def _ecommerce_platform(profile: Profile, _today: date) -> Outcome:
    platform = _text(profile, "platform")
    if not platform:
        # Not an abstention: the rule awards points for a platform being
        # *present*, so a missing signature costs points rather than inventing
        # them. Recorded here only because M1.11 makes "absent" and "invisible"
        # indistinguishable for Shopware 5, and that is a −15 bias worth
        # remembering when this rule is next read.
        return declines()
    return fires(f"Der Shop läuft auf {platform}, einem gängigen Shopsystem.")


def _owner_operated(profile: Profile, _today: date) -> Outcome:
    form = _text(profile, "legal_form")
    if form in OWNER_OPERATED_FORMS:
        return fires(
            f"Die Rechtsform ({form}) weist auf ein inhabergeführtes Unternehmen hin."
        )
    directors = _num(profile, "gf_count")
    # `1 <=`, not just `<= 2` (M1.46). §6.1's disjunct is "names 1–2 natural-person
    # Geschäftsführer", and naming none is not naming ≤ 2. A2's mapping already
    # withholds the key on an Impressum that names nobody, but that is a
    # convention every future writer has to remember; this is the invariant that
    # holds when one does not, on a rule worth +15 that also raises the company's
    # effective Phase-2 threshold from 5 to 20 (§7.1).
    if directors is not None and 1 <= directors <= 2:
        named = "eine Geschäftsführerin bzw. einen Geschäftsführer"
        if directors != 1:
            named = f"{int(directors)} Geschäftsführende"
        return fires(
            f"Das Impressum nennt {named} – eine überschaubare "
            "Führungsstruktur, in der Entscheidungen kurz sind."
        )
    named = _num(profile, "owner_named")
    if named == 1:
        return fires(
            "Die Inhaberin bzw. der Inhaber ist auf der Website namentlich genannt."
        )
    # Disjunct 3, three-state (M1.81). Reached ONLY when disjuncts 1 and 2 have
    # both declined — a company that already won its +15 on `legal_form` or
    # `gf_count` has nothing to abstain about, and flagging it would be a queue
    # item with no action behind it.
    if named is None and _homepage_read(profile):
        return abstains(
            "Nicht bewertbar: Die Startseite wurde ausgewertet, ob dort eine "
            "Inhaberin oder ein Inhaber namentlich genannt ist, ließ sich "
            "daraus aber nicht bestimmen.",
            review_reason="owner_named_undetermined",
        )
    return declines()


def _product_depth(profile: Profile, _today: date) -> Outcome:
    count = _num(profile, "product_url_count")
    if count is None or count < 20:
        return declines()
    return fires(
        f"Der Katalog umfasst {int(count)} Produktseiten – genug "
        "Sortimentstiefe, um dauerhaft über Inhalte zu tragen."
    )


def _own_brand(profile: Profile, _today: date) -> Outcome:
    """§6.1's +10, in three states rather than two (M1.81).

    Until M1.76 this returned `declines()` unconditionally with `reads=()`, and
    the reason was not that the answer is no — **no signal key existed to
    read**. A2 ruled the mapping on 2026-08-16 and the ruling lived in a
    proposal document; `brand.own_brand` and its view column arrive with
    migration 011.

    The third state is the one that needed the ruling. §5.5b instructs the model
    to return `null` for a field it cannot find, and a `null` after the
    extraction has run is A7's shape exactly: a measurement exists, it cannot
    carry the rule, so the rule fires in neither direction and a person is told.
    Collapsing it to `false` would read *unverified* as *absent* on a rule that
    awards points; collapsing it to a decline would leave the company showing
    nothing, because a decline records no component at all.

    **Direction: the abstention errs too low** — a withheld award, which the
    queue repairs — so it blocks nothing (migration 013). It is the *unverified
    value* that errs high, and §6.1's verification gate is what stops that value
    ever reaching a score.
    """
    if not _homepage_read(profile):
        # Phase 2's turn has not come. A rule whose turn has not come has not
        # abstained, and abstaining here would put "Phase 2 has not run yet" in
        # the review queue for every company in the corpus — §6.4's
        # "a queue that refills itself stops being read", manufactured.
        return declines()
    own_brand = _num(profile, "own_brand")
    if own_brand is None:
        # Reached two ways: the model returned `null`, or it returned a boolean
        # whose `_evidence` span failed verification and migration 012's
        # confidence filter removed the row. One reason for both, deliberately —
        # they send a person to the same page to answer the same question.
        return abstains(
            "Nicht bewertbar: Die Startseite wurde ausgewertet, ob der Shop "
            "eigene Marken führt oder ausschließlich weiterverkauft, ließ sich "
            "daraus aber nicht bestimmen.",
            review_reason="own_brand_undetermined",
        )
    if own_brand != 1:
        return declines()
    return fires(
        "Der Shop führt eigene Marken statt reiner Handelsware – ein "
        "Sortiment, über das sich eigenständig Inhalte aufbauen lassen."
    )


def _own_domain_shop(profile: Profile, _today: date) -> Outcome:
    count = _num(profile, "product_url_count")
    if count is None or count < 5:
        # §6.1, B7: unwritten fires neither this nor `possible_marketplace_only`.
        return declines()
    return fires(
        f"Der Shop verkauft mit {int(count)} Produktseiten über die "
        "eigene Domain und nicht nur über Marktplätze."
    )


def _product_strength(profile: Profile, _today: date) -> Outcome:
    if _num(profile, "trusted_shops") == 1:
        return fires("Die Startseite bindet ein Trusted-Shops-Siegel ein.")
    reviews = _num(profile, "review_count")
    if reviews is not None and reviews >= 50:
        return fires(f"Der Shop weist {int(reviews)} Kundenbewertungen aus.")
    return declines()


# ── §6.2 the blog ladder, as an ordered chain ───────────────────────────


def _no_blog(profile: Profile, _today: date) -> Outcome:
    exists = _num(profile, "blog_exists")
    searched = _num(profile, "blog_search_exhaustive") == 1
    if exists is None or (exists == 0 and not searched):
        limit = _text(profile, "blog_search_limit")
        transient = limit.startswith("transient:")
        return abstains(
            "Nicht bewertbar: Ob der Shop einen Blog betreibt, konnte nicht "
            "abschließend geprüft werden"
            + (
                " – der Blog wurde gefunden, seine Übersichtsseite war aber "
                "nicht abrufbar."
                if transient
                else ", weil nicht beide Suchwege zur Verfügung standen."
            )
            + " Die gesamte Blog-Bewertung entfällt.",
            # A7a routes now; A7b retries first and routes on the third
            # consecutive run over three distinct days (migration 009).
            review_reason=None if transient else "blog_undetectable",
            persistent_review_reason=PERSISTENT_FETCH if transient else None,
        )
    if exists == 0:
        return fires(
            "Auf der Website ist kein Blog und kein Ratgeberbereich zu finden – "
            "es fehlen also genau die Inhalte, über die neue Kundinnen und "
            "Kunden den Shop bei einer Suche entdecken könnten."
        )
    return declines()


def _blog_stale(profile: Profile, today: date) -> Outcome:
    newest = _day(profile, "blog_last_post")
    if newest is None:
        return abstains(
            "Nicht bewertbar: Der Blog ist vorhanden, es ließ sich aber kein "
            "Datum eines Beitrags auslesen.",
            review_reason="blog_date_unparseable",
        )
    if (today - newest).days <= ONE_YEAR:
        return declines()
    bounded = _text(profile, "blog_last_post_basis") in ("index", "both")
    if not bounded:
        return abstains(
            f"Nicht bewertbar: Der jüngste gesicherte Beitrag stammt vom "
            f"{month_year(newest)}, beruht aber nur auf einer Stichprobe und "
            "belegt daher keinen Stillstand.",
            review_reason="blog_date_unbounded",
        )
    return fires(
        f"Letzter Blogbeitrag: {month_year(newest)}. Seit "
        f"{months_since(newest, today)} Monaten ist nichts Neues erschienen."
    )


def _thin_blog(profile: Profile, _today: date) -> Outcome:
    count = _num(profile, "blog_post_count")
    # M1.34: an uncounted blog does not win a rule that awards points for
    # *few* posts — and unlike rung 1 this one falls through, because the rung
    # below asks a separately measured question.
    if count is None or count >= 10:
        return declines()
    return fires(
        f"Der Blog umfasst bisher nur {int(count)} Beiträge – zu wenig, um in "
        "der Suche für ein Themenfeld zu stehen."
    )


def _blog_slowing(profile: Profile, today: date) -> Outcome:
    newest = _day(profile, "blog_last_post")
    if newest is None or (today - newest).days < SIX_MONTHS:
        return declines()
    if _text(profile, "blog_last_post_basis") not in ("index", "both"):
        return abstains(
            f"Nicht bewertbar: Der jüngste gesicherte Beitrag stammt vom "
            f"{month_year(newest)}, beruht aber nur auf einer Stichprobe und "
            "belegt daher kein Nachlassen.",
            review_reason="blog_date_unbounded",
        )
    return fires(
        f"Letzter Blogbeitrag: {month_year(newest)} – seit gut einem halben "
        "Jahr ist die Veröffentlichung eingeschlafen."
    )


# ── §6.2 independent opportunity rules ──────────────────────────────────


def _no_article_schema(profile: Profile, _today: date) -> Outcome:
    if _num(profile, "blog_exists") != 1:
        # "Never fires together with opp.no_blog" (§6.2), and equally never on
        # a ladder that abstained: an unconfirmed blog cannot be missing markup.
        return declines()
    present = _num(profile, "article_schema")
    if present is None:
        # A7, new in M3's audit. A6.1 leaves this unwritten where no article was
        # sampled, and a `0` read from absence would award +8 for missing markup
        # on a page never fetched — A5.5's error, one signal over.
        return abstains(
            "Nicht bewertbar: Es konnte kein einzelner Blogbeitrag abgerufen "
            "werden, daher lässt sich die Auszeichnung der Beiträge nicht prüfen.",
            persistent_review_reason=PERSISTENT_FETCH,
        )
    if present == 1:
        return declines()
    return fires(
        "Die Blogbeiträge tragen keine Article-Auszeichnung im Quelltext – "
        "Suchmaschinen erkennen sie dadurch nicht als redaktionelle Inhalte."
    )


def _no_product_schema(profile: Profile, _today: date) -> Outcome:
    present = _num(profile, "product_schema")
    if present is None:
        # A5.5/A5.6: absent the sampled page the rule fires in neither
        # direction. A7b's routing, open since M1.34, is migration 009.
        return abstains(
            "Nicht bewertbar: Es konnte keine Produktseite abgerufen werden, "
            "daher lässt sich die Auszeichnung der Produktdaten nicht prüfen.",
            persistent_review_reason=PERSISTENT_FETCH,
        )
    if present == 1:
        return declines()
    return fires(
        "Die Produktseiten tragen keine Product-Auszeichnung im Quelltext – "
        "Preis, Verfügbarkeit und Bewertungen erscheinen deshalb nicht in den "
        "Suchergebnissen."
    )


def _ai_invisible(profile: Profile, _today: date) -> Outcome:
    checked = _num(profile, "ai_queries_checked")
    if checked is None or checked < 2:
        return declines()  # Phase 2; the threshold is itself the A7 guard
    if _num(profile, "ai_brand_mentions") != 0:
        return declines()
    return fires(
        "In KI-Antworten zu den eigenen Themen taucht die Marke nicht auf – "
        "bei zwei geprüften Suchanfragen keine einzige Nennung."
    )


def _slow_site(profile: Profile, _today: date) -> Outcome:
    score = _num(profile, "lighthouse_perf")
    if score is None:
        # A7, new in M3's audit. Latent until Phase 2 runs, guarded now: a NULL
        # must never read as "< 50".
        return declines()
    if score >= 50:
        return declines()
    return fires(
        f"Die Startseite erreicht im Performance-Test nur {int(score)} von 100 "
        "Punkten – Ladezeit kostet hier messbar Reichweite."
    )


def _de_only(profile: Profile, _today: date) -> Outcome:
    languages = _num(profile, "hreflang_count")
    if languages is None:
        return abstains(
            "Nicht bewertbar: Die Startseite konnte nicht auf Sprachversionen "
            "geprüft werden."
        )
    if languages > 1:
        return declines()
    return fires(
        "Die Website wird ausschließlich auf Deutsch ausgeliefert – für die "
        "Nachbarmärkte fehlen die Inhalte vollständig."
    )


# ── §6.3 negative signals ───────────────────────────────────────────────


def _has_agency(profile: Profile, _today: date) -> Outcome:
    credit = _text(profile, "agency_credit").strip()
    if not credit:
        # Under-detects by design: a logo-only credit is invisible here, which
        # is why §6.3 treats this as a bonus signal and never as a gate.
        return declines()
    return fires(
        f"Im Footer ist eine Agentur genannt („{credit[:60]}“) – die Betreuung "
        "liegt vermutlich bereits extern."
    )


def _active_content(profile: Profile, today: date) -> Outcome:
    """§6.3's −25, with the data path M3's audit had to define for it.

    The asymmetry is the whole rule. A partial enumeration can *establish*
    activity — four dated posts inside six months means at least four, however
    many are undated — and can never establish its absence. So the rule fires on
    a lower bound, declines only on a complete enumeration, and abstains in
    between. See `docs/m3-absent-input-audit.md` §7.

    **Its abstention is the one that errs upward** (A7's third axis, migration
    008): it withholds a penalty rather than an award, so the lead reads
    stronger than the evidence supports and an over-scored lead gets called.
    That is why this routing also blocks outbound contact, and why every other
    abstention in this ruleset does not.
    """
    exists = _num(profile, "blog_exists")
    if exists == 0 and _num(profile, "blog_search_exhaustive") == 1:
        return declines()  # no blog, therefore no posts — sound
    if exists != 1:
        return declines()  # the ladder already abstained; not this rule's job

    dates = _dates(profile, "blog_post_dates")
    recent = [day for day in dates if (today - day).days <= SIX_MONTHS]
    if len(recent) >= 4:
        return fires(
            f"Der Shop veröffentlicht regelmäßig: {len(recent)} Beiträge in den "
            "letzten sechs Monaten."
        )

    listed = _num(profile, "blog_post_count")
    if listed is not None and len(dates) >= listed:
        return declines()
    # An uncounted index abstains too — without a total there is nothing for
    # the dates to be complete *against* — but it must not say so by pretending
    # the total is zero. "Von 0 Beiträgen tragen nur 2 ein lesbares Datum" is
    # what that printed on `zecplus.de`, into a letter-ready reason and into the
    # queue note a person acts on (M1.40).
    if listed is None:
        return abstains(
            f"Nicht bewertbar: Die Übersichtsseite lässt sich nicht auszählen; "
            f"{len(dates)} Beiträge tragen ein lesbares Datum – wie oft der Shop "
            "veröffentlicht, lässt sich daraus nicht bestimmen.",
            review_reason="blog_cadence_unmeasurable",
        )
    return abstains(
        f"Nicht bewertbar: Von {int(listed)} Beiträgen auf der "
        f"Übersichtsseite tragen nur {len(dates)} ein lesbares Datum – wie oft "
        "der Shop veröffentlicht, lässt sich daraus nicht bestimmen.",
        # Ratified 2026-08-16, migration 008. A7a rather than A7b: the dates are
        # missing from the index itself, so running again reads the same index.
        review_reason="blog_cadence_unmeasurable",
    )


# ── the ruleset ─────────────────────────────────────────────────────────

RULES: tuple[Rule, ...] = (
    # §6.1
    Rule(
        "qual.ecommerce_platform", 15, "6.1", ("platform",), False, _ecommerce_platform
    ),
    Rule(
        "qual.owner_operated",
        15,
        "6.1",
        (
            "legal_form",
            "gf_count",
            "owner_named",
            "impressum_extracted",
            "homepage_extracted",
        ),
        True,
        _owner_operated,
        _owner_operated_settled,
    ),
    Rule(
        "qual.product_depth", 10, "6.1", ("product_url_count",), False, _product_depth
    ),
    # `reads=()` and `assert_declared`'s named exemption for it are BOTH gone
    # (M1.76): the exemption existed only because A2's key did not.
    Rule(
        "qual.own_brand",
        10,
        "6.1",
        ("own_brand", "homepage_extracted"),
        True,
        _own_brand,
        _homepage_read,
    ),
    Rule(
        "qual.own_domain_shop",
        5,
        "6.1",
        ("product_url_count",),
        False,
        _own_domain_shop,
    ),
    Rule(
        "qual.product_strength",
        10,
        "6.1",
        ("trusted_shops", "review_count"),
        False,
        _product_strength,
    ),
    # §6.2 — the ladder. Declaration order *is* evaluation order.
    Rule(
        "opp.no_blog",
        25,
        "6.2",
        ("blog_exists", "blog_search_exhaustive", "blog_search_limit"),
        False,
        _no_blog,
        chain="blog",
    ),
    Rule(
        "opp.blog_stale",
        20,
        "6.2",
        ("blog_last_post", "blog_last_post_basis"),
        False,
        _blog_stale,
        chain="blog",
    ),
    Rule(
        "opp.thin_blog",
        12,
        "6.2",
        ("blog_post_count",),
        False,
        _thin_blog,
        chain="blog",
    ),
    Rule(
        "opp.blog_slowing",
        10,
        "6.2",
        ("blog_last_post", "blog_last_post_basis"),
        False,
        _blog_slowing,
        chain="blog",
    ),
    # §6.2 — independent
    Rule(
        "opp.no_article_schema",
        8,
        "6.2",
        ("blog_exists", "article_schema"),
        False,
        _no_article_schema,
    ),
    Rule(
        "opp.no_product_schema",
        10,
        "6.2",
        ("product_schema",),
        False,
        _no_product_schema,
    ),
    Rule(
        "opp.ai_invisible",
        15,
        "6.2",
        ("ai_queries_checked", "ai_brand_mentions"),
        True,
        _ai_invisible,
        _never_settled,
    ),
    Rule(
        "opp.slow_site",
        10,
        "6.2",
        ("lighthouse_perf",),
        True,
        _slow_site,
        _slow_site_settled,
    ),
    Rule("opp.de_only", 5, "6.2", ("hreflang_count",), False, _de_only),
    # §6.3
    Rule(
        "neg.has_agency",
        -20,
        "6.3",
        ("agency_credit",),
        True,
        _has_agency,
        _has_agency_settled,
    ),
    Rule(
        "neg.active_content",
        -25,
        "6.3",
        ("blog_exists", "blog_post_dates", "blog_post_count"),
        False,
        _active_content,
    ),
)


class RulesetError(RuntimeError):
    """The declared ruleset is malformed. Raised at import, never at score time."""


def assert_declared(rules: tuple[Rule, ...] = RULES) -> None:
    """§5.4's startup assertion. Fails loudly rather than scoring on a guess.

    The Phase-2 check is the one the spec asks for by name, and the others are
    here for the same reason B6 asked for a table at all: a rule that reads as
    implemented and cannot fire is a defect class this project has now hit twice
    (B7, and `neg.active_content`), and every instance was invisible because
    nothing ever looked at the rule *as data*.
    """
    seen: set[str] = set()
    for rule in rules:
        if rule.id in seen:
            raise RulesetError(f"duplicate rule id: {rule.id}")
        seen.add(rule.id)
        if not isinstance(rule.phase2_reachable, bool):
            raise RulesetError(f"{rule.id} does not declare phase2_reachable")
        if rule.points == 0:
            raise RulesetError(f"{rule.id} is worth nothing and cannot matter")
        if not callable(rule.evaluate):
            raise RulesetError(f"{rule.id} has no predicate")
        if not rule.reads:
            # The named exemption for `qual.own_brand` is GONE (M1.76). It
            # existed only because A2's mapping had never been transcribed into
            # the spec, so the rule had no key to read — the one check built to
            # catch a rule that cannot fire had the instance written into it as
            # a special case. `brand.own_brand` exists now (migration 011), so
            # the rule reads, and the exemption goes with the gap.
            raise RulesetError(f"{rule.id} reads no signal and cannot fire")
        settled = rule.phase2_input_settled
        if rule.phase2_reachable and not callable(settled):
            raise RulesetError(
                f"{rule.id} is phase2_reachable and does not declare "
                f"phase2_input_settled — §5.4's bound would keep offering its "
                f"points for a question Phase 2 has already answered (M1.82)"
            )
        if not rule.phase2_reachable and settled is not None:
            raise RulesetError(
                f"{rule.id} is not phase2_reachable and declares "
                f"phase2_input_settled — a Phase-1 rule cannot have an input "
                f"Phase 2 settles, and asserting it hides the mistake"
            )
        if rule.section not in ("6.1", "6.2", "6.3"):
            raise RulesetError(f"{rule.id} names no section")


assert_declared()
