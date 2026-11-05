"""Check that the docs describe Get on a missing key the same way as the implementation (initiative doeff-publish).

In 0.5.0, `state()` returns None for Get on a missing key (test_get_missing_returns_none in tests/test_core_effects.py). The session val / var of writer and
the Hy defhandler read this None as "not placed yet" and place the initial value. Chapter 09, section "State Effects in
Concurrent Programs", says KeyError, yet its fence uses `(current or 0)` assuming None, and no check ever ran the fence.

What is verified: the fences in that section of chapter 09 run under the same convention as the README (tools/readme_examples.py) and print as the print annotations say.

Chapter 09 cannot yet run as a whole page (as of 2026-10-17, 13 of 16 fences have no annotation), so
it is not listed in CHECKED_PAGES of tests/test_docs_page_examples.py, the page-running check, and only this section is run. When the page is fixed so that it runs as a whole, move chapter 09 to
CHECKED_PAGES and remove the check for this section here (do not run the same examples twice).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.test_readme_examples import ROOT, Example, _run, examples

ADVANCED_PAGE = "docs/09-advanced-effects.md"
# The State section of chapter 09, inside concurrent processing (it explains Get on a missing key and has examples).
STATE_SECTION = "State Effects in Concurrent Programs"


def _state_fences() -> list[Example]:
    """The fences in the State section of chapter 09 that are run and compared against their annotations."""
    fences = examples.fences((ROOT / ADVANCED_PAGE).read_text(encoding="utf-8"), ADVANCED_PAGE)
    return [example for example in fences if example.section == STATE_SECTION and example.runs()]


def test_the_state_section_has_examples_that_run() -> None:
    # If the section is renamed or all fences become excerpts, the checks below go green without running a single one.
    assert _state_fences(), f"section {STATE_SECTION!r} of {ADVANCED_PAGE} has no fence to run"


@pytest.mark.parametrize("example", _state_fences(), ids=lambda example: f"09:{example.line}")
def test_state_section_example_runs_and_prints_what_it_states(
    example: Example, tmp_path: Path, deadline_scale_factor: float
) -> None:
    assert example.problems() == []
    mismatch = examples.mismatch(example, _run(example, tmp_path, deadline_scale_factor))
    assert mismatch is None, mismatch
