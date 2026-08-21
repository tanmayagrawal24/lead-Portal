"""M1.91 — every amendment number cited anywhere must resolve to a table row.

**This is a check on the instrument, not on the code.** The amendment table in
`docs/lead-portal-spec-v0.3.md` is what this project uses to measure itself: a
comment reading *"M1.86's whole finding"* is only worth anything if M1.86 can be
looked up. Twice in a row it could not be. Unit 9a coined M1.85 and cited it in
a report and a test docstring without amending the register; Unit 9b then coined
five numbers and cited them seventeen times across seven files of production
code and schema, and the register still stopped at M1.85.

**A test rather than a convention**, because every comparable rule in this
project is enforced by something that fails: `ruleset.assert_declared`,
`assert_ledger_guarded`, M1.19's exit code. A convention asks; this refuses.

Two things it deliberately does not check. A declared row need not be cited
anywhere — a finding may be recorded and never referenced again. And the
numbering's contiguity is a different rule, never violated, and not this one.
"""

from __future__ import annotations

import re
import subprocess
import unittest
from collections import defaultdict
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SPEC = REPO / "docs" / "lead-portal-spec-v0.3.md"

#: A citation anywhere: prose, comment, SQL, docstring, workflow.
CITATION = re.compile(r"M1\.(\d+)")

#: A declaration: the start of a row in one of the spec's amendment tables. The
#: anchor is what makes this different from a citation — a number is declared by
#: having a row, not by being mentioned in one.
DECLARATION = re.compile(r"^\| M1\.(\d+) \|", re.MULTILINE)


def tracked_files() -> list[Path]:
    """Every file git tracks. `git ls-files` rather than a directory walk so
    that `.mypy_cache`, untracked scratch and build output cannot influence a
    check about what the repository says."""
    done = subprocess.run(
        ["git", "-C", str(REPO), "ls-files", "-z"],
        capture_output=True,
        check=True,
    )
    return [REPO / n for n in done.stdout.decode().split("\0") if n]


def declared_numbers() -> set[int]:
    return {int(m) for m in DECLARATION.findall(SPEC.read_text(encoding="utf-8"))}


def citations() -> dict[int, set[str]]:
    """Cited number → the repo-relative paths that cite it."""
    found: dict[int, set[str]] = defaultdict(set)
    for path in tracked_files():
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue  # a binary blob cites nothing
        for number in CITATION.findall(text):
            found[int(number)].add(str(path.relative_to(REPO)))
    return found


class EveryCitedAmendmentIsDeclared(unittest.TestCase):
    def test_no_citation_points_at_a_row_that_does_not_exist(self) -> None:
        declared = declared_numbers()
        cited = citations()
        undeclared = sorted(n for n in cited if n not in declared)
        if undeclared:
            lines = [
                f"  M1.{n} — cited in {', '.join(sorted(cited[n]))}" for n in undeclared
            ]
            self.fail(
                f"{len(undeclared)} amendment number(s) are cited in the tree "
                f"and have no row in the amendment table of "
                f"{SPEC.relative_to(REPO)}:\n" + "\n".join(lines) + "\n\n"
                "A citation that cannot be looked up is worse than no citation: "
                "it reads as an authority. Write the row, or drop the number "
                "(M1.91)."
            )

    def test_the_register_is_not_empty_and_the_parse_still_works(self) -> None:
        """A guard on the guard. If the table's row format ever changes, the
        check above would pass by declaring nothing cited and everything
        undeclared — or, worse, by finding no citations at all."""
        declared = declared_numbers()
        self.assertGreater(
            len(declared), 80, "the declaration parse found almost nothing"
        )
        self.assertIn(1, declared, "M1.1 should be the first declared row")
        self.assertGreater(
            len(citations()), 80, "the citation scan found almost nothing"
        )
