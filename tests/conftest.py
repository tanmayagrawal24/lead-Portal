"""M1.95 — the suite may not borrow the machine's corpus, and this is the check.

**This is the guard that makes moving `data/` aside unnecessary.** Unit 7's first
push failed both pytest jobs while the suite was green locally: `llm-prices
--reserve` opened the database, a pre-existing test used the default path, and
the developer's machine had `data/portal.db` from 17 August while a CI runner
had none. `aaa41bb` fixed it at source — `PricesCommand` builds its own database
— and the mitigation that had been issued alongside it (*"before every push,
move `data/` aside"*) outlived the defect by six units and eventually destroyed
the corpus it was written to protect.

**A test rather than an instruction**, for the reason M1.91 already gives: a
convention asks, and this refuses. `db.connect` calls
`path.parent.mkdir(parents=True, exist_ok=True)`, so anything that opens
`config.DEFAULT_DB_PATH` creates `data/` as a side effect. If the directory was
not there when the session began and is there when it ends, something in the
suite reached for the machine's corpus.

**What it cannot catch, stated rather than left to be discovered.** On a machine
that already has `data/`, the before-state and the after-state are both *present*
and the guard is silent — it cannot distinguish a test that opened the corpus
from one that did not. That is not a hole to be closed here; it is M1.64 and
M1.19's standing lesson about where authority lives. CI runners never have
`data/`, so the run that gates the merge is the run where this check has teeth,
and the local run is the one that cannot be trusted to speak for it. Closing the
gap properly would mean intercepting `sqlite3.connect`, which buys a guard
against a defect that CI already fails on.
"""

from __future__ import annotations

import pytest

from portal import config

#: Resolved once, at import, so that a test which changes the working directory
#: cannot move the thing being watched.
CORPUS_DIR = config.DEFAULT_DB_PATH.parent


@pytest.fixture(scope="session", autouse=True)
def the_suite_does_not_touch_the_real_corpus() -> object:
    """Fail the session if the suite created `data/` where there was none."""
    existed_before = CORPUS_DIR.exists()
    yield
    if not existed_before and CORPUS_DIR.exists():
        pytest.fail(
            f"the test suite created {CORPUS_DIR} — something opened "
            f"config.DEFAULT_DB_PATH instead of a temporary database. "
            f"`db.connect` mkdirs the parent, so an empty file at the default "
            f"path is enough to do this. Point the code under test at a "
            f"tmp_path database or have it build its own (aaa41bb's fix); do "
            f"not move the corpus out of the way to make this pass (M1.95).",
            pytrace=False,
        )
