"""Test that, among docs pages outside the introductory path, the examples of pages rewritten to be runnable run as-is on the current doeff and print as written
(initiative doeff-publish).

Pages on the introductory path (table of contents and chapters 01-05) are run by tests/test_docs_entry_path_examples.py. The examples of pages outside it were not run by
any test: as of 0.5.0, running the 137 fences that passed on definition alone through to run made 25 fail (report of the 137:
2026-10-17-doeff-docs-137-run.html). The chapter 07 API Response Caching example passed the return of a synchronous httpx.get to Await.

Fences are read, run, and compared with annotations under the same convention as the introductory path (one point of the convention is tools/readme_examples.py). Fragments that are not run
get the word `excerpt` after the language. When a page's examples are rewritten to be runnable, add one row to the contract table in pyproject.toml that selects this test for that page.
The list of pages to run (CHECKED_PAGES) is read from that table -- while the list was written in this file, two commits adding pages side by side
collided on the same line (landing queue L2431, L2432).
"""

from __future__ import annotations

from pathlib import Path

import pytest
import tomllib

from tests.test_docs_entry_path_examples import ENTRY_PATH, _fences
from tests.test_readme_examples import ROOT, Example, _run, examples

# This test's file (path from the repo root -- the way the contract table in pyproject.toml writes it).
THIS_CHECK = Path(__file__).resolve().relative_to(ROOT).as_posix()
# Prefix of docs pages (the way the contract table writes prefix).
DOCS_PREFIX = "docs/"


def prefixes_selecting(check: str) -> list[str]:
    """Prefixes of the rows in pyproject.toml's contract table that select check (a path from the repo root) (in table order)."""
    rows = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["tool"]["doeff"][
        "contract-tests"
    ]
    return [row["prefix"] for row in rows if check in row.get("tests", [])]


def _prefixes_selecting_this_check() -> list[str]:
    """Prefixes of the rows in pyproject.toml's contract table that select this test (in table order)."""
    return prefixes_selecting(THIS_CHECK)


# Pages outside the introductory path rewritten to be runnable (names from docs/) -- the rows of the contract table that select this test are the single definition.
CHECKED_PAGES = tuple(
    prefix.removeprefix(DOCS_PREFIX) for prefix in _prefixes_selecting_this_check()
)


def _runnable() -> list[tuple[str, Example]]:
    """The fences of CHECKED_PAGES that are run and compared with annotations (paired with the page)."""
    return [
        (page, example) for page in CHECKED_PAGES for example in _fences(page) if example.runs()
    ]


def _example_id(case: tuple[str, Example]) -> str:
    """parametrize id -- by page and line, so you can read which fence a failed test is."""
    page, example = case
    return f"{Path(page).stem}:{example.line}"


@pytest.mark.parametrize("case", _runnable(), ids=_example_id)
def test_page_example_runs_and_prints_what_it_states(
    case: tuple[str, Example], tmp_path: Path, deadline_scale_factor: float
) -> None:
    page, example = case
    assert example.problems() == []
    # Put files written by earlier fences of the same page (such as the module for `file=`) in place first, as the reader would.
    for earlier in _fences(page):
        if earlier.line < example.line and earlier.written_as() is not None:
            examples.write(earlier, tmp_path)
    mismatch = examples.mismatch(example, _run(example, tmp_path, deadline_scale_factor))
    assert mismatch is None, mismatch


@pytest.mark.parametrize("page", CHECKED_PAGES)
def test_page_fences_keep_the_rules(page: str) -> None:
    problems = [problem for example in _fences(page) for problem in example.problems()]
    assert problems == [], "\n".join(problems)


@pytest.mark.parametrize("page", CHECKED_PAGES)
def test_page_has_an_example_that_runs(page: str) -> None:
    # A page where everything is excerpt checks nothing (it would go green just by marking and removing it from the test).
    assert any(example.runs() for example in _fences(page)), f"docs/{page} has no example to run"


def test_checked_pages_are_outside_the_entry_path() -> None:
    # Pages on the introductory path are run by tests/test_docs_entry_path_examples.py (do not run the same example twice).
    assert [page for page in CHECKED_PAGES if page in ENTRY_PATH] == []


def prefixes_that_are_not_pages(prefixes: list[str]) -> list[str]:
    """Prefixes that are not an existing docs page (a docs/<name>.md file) (dirs, typos)."""
    return [
        prefix
        for prefix in prefixes
        if not (
            prefix.startswith(DOCS_PREFIX) and prefix.endswith(".md") and (ROOT / prefix).is_file()
        )
    ]


def test_rows_selecting_this_check_name_docs_pages() -> None:
    # A table row makes `make test-changed` select this test for a commit that changed a page, and decides which page that row runs. If the row's prefix is
    # not a page, no test runs that page's examples.
    prefixes = _prefixes_selecting_this_check()
    assert prefixes, f"[[tool.doeff.contract-tests]] in pyproject.toml has no row that selects {THIS_CHECK}"
    not_pages = prefixes_that_are_not_pages(prefixes)
    assert not_pages == [], (
        f"In [[tool.doeff.contract-tests]] in pyproject.toml, the prefix(es) {not_pages} of the rows that select {THIS_CHECK} are"
        f" not an existing docs page ({DOCS_PREFIX}<name>.md)"
    )


def test_the_check_names_prefixes_that_are_not_pages() -> None:
    sample = ["docs/07-cache-system.md", "docs/", "docs/no-such-page.md", "README.md"]
    assert prefixes_that_are_not_pages(sample) == ["docs/", "docs/no-such-page.md", "README.md"]
