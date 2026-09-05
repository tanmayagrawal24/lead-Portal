"""ISO-3166-1 alpha-2 country for a company, derived rather than asked for.

**One expression of the rule, used by every writer** — `discover` (both
sources), `seeds`, and migration 021's backfill are the same three steps in the
same order (M1.42's lesson, after M1.109, M1.115, M1.121 and M1.122):

  1. the domain's TLD, where the TLD *is* a country;
  2. else the country the discovery run was aimed at, when it named one;
  3. else NULL — which is *"not established"*, never *"none"* (M1.59).

Step 1 outranks step 2 on purpose. A `.at` domain found by a run aimed at DE is
Austrian; the run's aim is what the operator was *looking for*, and the TLD is
something the company itself registered. The weaker evidence never overrides
the stronger one, and a run tag never overrides a fact.

Neither step is a measurement, and both are beaten by one. `reconcile` writes
`impressum.country` — a value a model read off the company's own Impressum
page and `verify` confirmed against it — straight into this column, and that is
correct: a derived value is a placeholder for a measured one, so nothing here
may overwrite a non-NULL.

WHY THE SET STOPS AT DACH
-------------------------
`company.country` has carried `CHECK (country IN ('DE','AT','CH'))` since
migration 001, where it records §5.1's market. `.lu` and `.li` are named in
`OUT_OF_SCOPE_TLD` rather than silently dropped, because the difference between
*"we do not handle Luxembourg"* and *"we forgot Luxembourg"* is exactly what
this file exists to make visible. Returning `'LU'` from `derive` today would
hand a `discover --submit` run a value the column refuses, turning a PAID run
into an IntegrityError partway through — the widening has to come first, and it
is not free: relaxing an inline CHECK means rebuilding `company`, which fifteen
tables reference by foreign key, with `PRAGMA foreign_keys` off. See M1.128.
"""

from __future__ import annotations

# Exactly the column's CHECK, and the reason this is a constant rather than a
# literal in five places: the schema and the code cannot drift into disagreeing
# about which countries exist.
COUNTRIES: tuple[str, ...] = ("DE", "AT", "CH")

TLD_COUNTRY: dict[str, str] = {"de": "DE", "at": "AT", "ch": "CH"}

# German-speaking TLDs the schema cannot store yet. Kept as data, not as a
# comment, so `is_out_of_scope` is answerable and a test can assert that these
# derive to NULL deliberately rather than by omission.
OUT_OF_SCOPE_TLD: dict[str, str] = {"lu": "LU", "li": "LI"}


def tld(domain: str) -> str:
    """The registrable suffix's last label, lowercased. `""` if there is none."""
    label = (domain or "").strip().lower().rstrip(".")
    _, _, last = label.rpartition(".")
    return last


def from_tld(domain: str) -> str | None:
    """Step 1. `None` for `.com`, `.shop`, `.eu`, `.berlin` — and for `.lu` and
    `.li`, which are `OUT_OF_SCOPE_TLD` rather than unknown."""
    return TLD_COUNTRY.get(tld(domain))


def is_out_of_scope(domain: str) -> bool:
    """True where the TLD names a country the column cannot yet hold. The
    caller gets NULL either way; this is how it tells the two NULLs apart."""
    return tld(domain) in OUT_OF_SCOPE_TLD


def normalise(value: str | None) -> str | None:
    """A country tag as typed, uppercased. `None` for empty.

    Raises `ValueError` for anything else, including `'LU'`: a run tag that the
    column would refuse must fail at the argument, before the run row exists
    and long before a paid call, rather than at the first INSERT.
    """
    text = (value or "").strip().upper()
    if not text:
        return None
    if text not in COUNTRIES:
        extra = ""
        if text in OUT_OF_SCOPE_TLD.values():
            extra = " — named in countries.OUT_OF_SCOPE_TLD, not yet storable (M1.128)"
        raise ValueError(
            f"country must be one of {', '.join(COUNTRIES)}; got {text!r}{extra}"
        )
    return text


def derive(domain: str, *, region: str | None = None) -> str | None:
    """The whole rule: TLD, else the run's country tag, else `None`.

    A TLD the schema cannot store STOPS the derivation rather than falling
    through. `bank.lu` found by a run tagged DE is not a German company: the
    TLD answered, and the answer is one this column cannot hold. Falling
    through would write a WRONG country where the honest outcome is a missing
    one, and M1.128's whole argument is that these NULLs are decisions.

    `region` is trusted as already-validated (`normalise` is the gate the CLI
    runs it through); an unknown value here is ignored rather than raising,
    because a derivation called per-row must not turn a bad flag into a
    half-written corpus.
    """
    if is_out_of_scope(domain):
        return None
    return from_tld(domain) or (region if region in COUNTRIES else None)
