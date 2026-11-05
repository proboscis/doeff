"""Check that the docs say, with running examples, how a handler that does not Resume and a handler that does not Pass end the program
(initiative doeff-publish).

When a hand-written handler returns a value without Resume, the rest of the program under the handler does not run, and the program's answer becomes
the handler's value. A handler with neither annotation nor Pass falls through to the end on an effect it does not handle (such as Ask) and returns None, ending the program with
None. Neither raises an error or warning -- the VM accepts "return a value without resuming" as a legitimate way to finish, and has no mark to tell whether it was stopped on purpose
or Resume was forgotten (ADR-DOE-CORE-EFFECTS-003 R15, HP-04 of SPEC-TYPES-001).
On 0.5.0 and main 4685dc801d62, `got https://example.com` and `None` came out instead of the `body=got ...` the writer expected
(items 16 and the appendix of the error table 2026-10-17-doeff-first-errors-20.html).

Two things are verified:
- The fences in the Control Effects section of chapter 13 (which has the Resume and Pass items) run under the same convention as the README (tools/readme_examples.py) and
  print as the print annotations say.
- The Handler Contract section of chapter 02 has runnable fences (they are run by the entry-path check
  tests/test_docs_entry_path_examples.py -- not run here; the same examples are not run twice).

Chapter 13 cannot yet run as a whole page (as of 2026-10-17, many of the fences in the language to run have no annotation), so it is not listed in CHECKED_PAGES of the page-running check
tests/test_docs_page_examples.py, and only this section is run. When the page is fixed so that it runs as a whole,
move chapter 13 to CHECKED_PAGES and remove the check for the chapter 13 section here.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.test_readme_examples import ROOT, Example, _run, examples

API_PAGE = "docs/13-api-reference.md"
# The section of chapter 13 that has the Resume and Pass items.
CONTROL_SECTION = "Control Effects"
CORE_PAGE = "docs/02-core-concepts.md"
# The section of chapter 02 that has the subsections on return without Resume and a handler that does not Pass.
HANDLER_SECTION = "Handler Contract"


def _section_fences(page: str, section: str) -> list[Example]:
    """The fences of a page section that are run and compared against their annotations."""
    fences = examples.fences((ROOT / page).read_text(encoding="utf-8"), page)
    return [example for example in fences if example.section == section and example.runs()]


def test_the_control_section_has_examples_that_run() -> None:
    # If the section is renamed or all fences become excerpts, the checks below go green without running a single one.
    assert len(_section_fences(API_PAGE, CONTROL_SECTION)) >= 2, (
        f"section {CONTROL_SECTION!r} of {API_PAGE} has no runnable fence for the Resume and Pass items"
    )


def test_the_handler_contract_section_has_examples_that_run() -> None:
    # The entry-path check runs them. Here we only verify that renaming a section or excerpts do not drop the examples of the two subsections from the checks.
    assert len(_section_fences(CORE_PAGE, HANDLER_SECTION)) >= 2, (
        f"section {HANDLER_SECTION!r} of {CORE_PAGE} has no runnable fence for return without Resume and a handler that does not Pass"
    )


@pytest.mark.parametrize(
    "example",
    _section_fences(API_PAGE, CONTROL_SECTION),
    ids=lambda example: f"13:{example.line}",
)
def test_control_section_example_runs_and_prints_what_it_states(
    example: Example, tmp_path: Path, deadline_scale_factor: float
) -> None:
    assert example.problems() == []
    mismatch = examples.mismatch(example, _run(example, tmp_path, deadline_scale_factor))
    assert mismatch is None, mismatch
