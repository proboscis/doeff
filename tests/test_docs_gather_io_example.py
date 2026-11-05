"""Test that the chapter 09 example arranging I/O with Gather runs with I/O placed in a handler and prints as documented
(doeff-publish initiative).

The example in the chapter 09 subsection "Using Gather with Async Operations" called HTTP directly inside the program with `yield Await(http_get(url))`.
In the doeff style, the program yields an `HttpGet(url)` effect,
and the I/O is held by a handler marked `@handler`. This test reads that the example runs on current doeff and prints as the print annotations say.

Chapter 09 cannot yet be run as a whole page (as of 2026-10-26, 14 of its 16 fences do not run as annotated), so it is not listed in CHECKED_PAGES of
tests/test_docs_page_examples.py, which runs whole pages; only this subsection is run. Once the whole page is fixed to run,
move chapter 09 to CHECKED_PAGES and remove this test (do not run the same example twice).
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from tests.test_readme_examples import ROOT, Example, _run, examples

ADVANCED_PAGE = "docs/09-advanced-effects.md"
# The chapter 09 subsection that arranges I/O with Spawn and Gather.
GATHER_IO_SUBSECTION = "Using Gather with Async Operations"
_HEADING = re.compile(r"^#{2,3} (?P<title>.+?)[ \t]*$", re.MULTILINE)


def _subsection_lines(text: str, title: str) -> range:
    """The line range of the subsection with heading title (### or ##) in text (1-based, up to just before the next heading)."""
    headings = [(text.count("\n", 0, found.start()) + 1, found["title"]) for found in _HEADING.finditer(text)]
    for index, (line, found_title) in enumerate(headings):
        if found_title == title:
            end = headings[index + 1][0] if index + 1 < len(headings) else text.count("\n") + 2
            return range(line, end)
    return range(0)


def _gather_io_fences() -> list[Example]:
    """The fences of the chapter 09 Gather I/O subsection that are run and compared against their annotations."""
    text = (ROOT / ADVANCED_PAGE).read_text(encoding="utf-8")
    lines = _subsection_lines(text, GATHER_IO_SUBSECTION)
    return [example for example in examples.fences(text, ADVANCED_PAGE) if example.line in lines and example.runs()]


def test_the_gather_io_subsection_has_an_example_that_runs() -> None:
    # If the subsection is renamed or the fences are turned into an excerpt, the tests below would run none and stay green.
    assert _gather_io_fences(), f"{ADVANCED_PAGE} subsection {GATHER_IO_SUBSECTION!r} has no fence to run"


@pytest.mark.parametrize("example", _gather_io_fences(), ids=lambda example: f"09:{example.line}")
def test_gather_io_example_runs_and_prints_what_it_states(
    example: Example, tmp_path: Path, deadline_scale_factor: float
) -> None:
    assert example.problems() == []
    mismatch = examples.mismatch(example, _run(example, tmp_path, deadline_scale_factor))
    assert mismatch is None, mismatch


@pytest.mark.parametrize("example", _gather_io_fences(), ids=lambda example: f"09:{example.line}")
def test_gather_io_example_keeps_io_out_of_the_program(example: Example) -> None:
    # The form that bakes I/O directly into a program (yielding `Await(http_get(...))` inside @do) must not come back.
    # Await is used only inside handlers.
    assert "yield HttpGet(" in example.code
    assert "@handler" in example.code
    programs = re.split(r"^@", example.code, flags=re.MULTILINE)
    awaits_in_programs = [part for part in programs if part.startswith("do\n") and "Await(" in part]
    assert awaits_in_programs == [], "There is a @do program that yields Await — put I/O in a handler"
