"""Test that the examples in the docs getting-started path pages run as-is on current doeff and print as documented (doeff-publish initiative).

A first-time user goes from the README to the docs table of contents (docs/index.md) and reads the introductory chapters in order. No test ran the examples of the pages on that path,
so nobody noticed when the way to use handlers changed: as of 0.5.0, of the 324 fences reachable from the table of contents,
158 failed; 5 of 13 in the introductory chapter and 10 of 12 in the error-handling chapter used outdated usage (writer needs a state handler;
Try needs try_handler — STALE_EXAMPLES below are copies from that time; docs report 2026-10-17-doeff-docs-examples-run.html).

Fences are read, run, and compared with annotations under the same convention as the README (the single definition point is tools/readme_examples.py — loading and running
are borrowed from tests/test_readme_examples.py). For fragments that are not run, put the word `excerpt` after the language (```` ```python excerpt ```` —
the same marker as in chapter 13). Each page is handled in its own dir, and `file=` fences are placed before later fences.

The getting-started path = the pages of the table of contents, the For Beginners pages of its Learning Path, and chapters 01 to 05 named by the card. Chapter 14 (CLI auto-discovery) is
not included: its examples need doeff-indexer (outside the 5 core components) and a package with dirs (`myapp/features/report.py`), and the convention's `file=` can only
name files without dirs.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import tomllib

from tests.test_readme_examples import ROOT, Example, _run, examples

DOCS = ROOT / "docs"
# This test's file (a path from the repo root — as written in the contract table of pyproject.toml).
THIS_CHECK = Path(__file__).resolve().relative_to(ROOT).as_posix()
# Pages of the getting-started path (names relative to docs/, in reading order). When adding a page to For Beginners in the table of contents, add it here too (the test below names it).
ENTRY_PATH = (
    "index.md",
    "01-getting-started.md",
    "02-core-concepts.md",
    "03-basic-effects.md",
    "04-async-effects.md",
    "05-error-handling.md",
)
# The heading of the section in the table of contents that lists pages in the order a first-time user reads them (all pages linked in this section are in ENTRY_PATH).
BEGINNER_SECTION = "### For Beginners"
# Links to docs/ pages inside a table-of-contents section (`[name](01-getting-started.md)`, also with `#section`).
_PAGE_LINK = re.compile(r"\]\((?P<page>[^)#/:]+\.md)(?:#[^)]*)?\)")


def _fences(page: str) -> list[Example]:
    """Fences of a page (a page given as this test's name is a path from the repo root)."""
    return examples.fences((DOCS / page).read_text(encoding="utf-8"), f"docs/{page}")


def _runnable() -> list[tuple[str, Example]]:
    """Fences of the getting-started path that are run and compared with annotations (paired with the page)."""
    return [(page, example) for page in ENTRY_PATH for example in _fences(page) if example.runs()]


def _example_id(case: tuple[str, Example]) -> str:
    """parametrize id — by page and line, so that a failed test shows which fence it was."""
    page, example = case
    return f"{Path(page).stem}:{example.line}"


@pytest.mark.parametrize("case", _runnable(), ids=_example_id)
def test_entry_path_example_runs_and_prints_what_it_states(
    case: tuple[str, Example], tmp_path: Path, deadline_scale_factor: float
) -> None:
    page, example = case
    assert example.problems() == []
    # Place the files written by earlier fences of the same page (`file=` modules etc.) first, as a reader would.
    for earlier in _fences(page):
        if earlier.line < example.line and earlier.written_as() is not None:
            examples.write(earlier, tmp_path)
    mismatch = examples.mismatch(example, _run(example, tmp_path, deadline_scale_factor))
    assert mismatch is None, mismatch


@pytest.mark.parametrize("page", ENTRY_PATH)
def test_entry_path_fences_keep_the_rules(page: str) -> None:
    problems = [problem for example in _fences(page) for problem in example.problems()]
    assert problems == [], "\n".join(problems)


@pytest.mark.parametrize("page", ENTRY_PATH)
def test_entry_path_page_has_an_example_that_runs(page: str) -> None:
    # A page where everything is an excerpt verifies nothing (it would go green just by adding the marker to exclude it from the test).
    assert any(example.runs() for example in _fences(page)), f"docs/{page} has no example to run"


def _beginner_pages(index: str) -> list[str]:
    """Pages linked by the For Beginners section of the table of contents (names relative to docs/)."""
    _, found, rest = index.partition(f"\n{BEGINNER_SECTION}\n")
    assert found, (
        f"docs/index.md has no section {BEGINNER_SECTION!r} — if the section is renamed, fix BEGINNER_SECTION too"
    )
    section = re.split(r"^#{1,3} ", rest, maxsplit=1, flags=re.MULTILINE)[0]
    return [link["page"] for link in _PAGE_LINK.finditer(section)]


def test_entry_path_covers_the_beginner_pages_of_the_index() -> None:
    pages = _beginner_pages((DOCS / "index.md").read_text(encoding="utf-8"))
    assert pages, f"{BEGINNER_SECTION} in docs/index.md has no link to a page"
    assert [page for page in pages if page not in ENTRY_PATH] == [], (
        f"a page linked by {BEGINNER_SECTION} in docs/index.md is not in ENTRY_PATH — add it so its examples run"
    )


def test_contract_table_selects_this_check_for_each_entry_page() -> None:
    # `make test-changed` for a change that rewrites a page selects this test (examples are written to files and run, so reverse import dependencies would not select it).
    rows = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["tool"]["doeff"][
        "contract-tests"
    ]
    selected = {row["prefix"] for row in rows if THIS_CHECK in row.get("tests", [])}
    assert [page for page in ENTRY_PATH if f"docs/{page}" not in selected] == [], (
        f"[[tool.doeff.contract-tests]] in pyproject.toml has no row selecting {THIS_CHECK} for some getting-started path page"
    )


def test_beginner_pages_are_read_from_the_section_only() -> None:
    index = """## Learning Path

### For Beginners

1. Start with **[Getting Started](01-getting-started.md)** for basics
2. Then **[Errors](05-error-handling.md#try-effect)**, see [GitHub](https://github.com/x/y.md)

### For Intermediate Users

1. **[Async Effects](04-async-effects.md)**
"""
    assert _beginner_pages(index) == ["01-getting-started.md", "05-error-handling.md"]


# Copies of two pre-fix docs examples (failure cases that confirm this test names them) — the chapter 03 Ask example (no state outside writer,
# pre-fix) and the chapter 05 Try example (no try_handler, pre-fix).
STALE_EXAMPLES = {
    "Get": Example(
        "docs/03-basic-effects.md",
        "Reader Effects",
        "python",
        """from doeff import do, run
from doeff_core_effects import Ask, Tell
from doeff_core_effects._handlers import reader, writer
from doeff_core_effects._scheduler import scheduled

@do
def connect_to_database():
    db_url = yield Ask("database_url")
    timeout = yield Ask("timeout")
    yield Tell(f"Connecting to {db_url} with timeout {timeout}")
    return f"Connected to {db_url}"

# Run with environment
def main():
    prog = connect_to_database()
    prog = writer(prog)
    prog = reader(env={
        "database_url": "postgresql://localhost/mydb",
        "timeout": 30
    })(prog)
    result = run(scheduled(prog))
    print(result)  # "Connected to postgresql://localhost/mydb"

main()
""",
        21,
    ),
    "Try": Example(
        "docs/05-error-handling.md",
        "Try Effect",
        "python",
        """from doeff import Err, Ok, Try, Tell, do, run
from doeff_core_effects._handlers import writer

@do
def parse_count(raw: str):
    return int(raw)

@do
def app(raw: str):
    parsed = yield Try(parse_count(raw))

    match parsed:
        case Ok(value=count):
            yield Tell(f"parsed count={count}")
            return count
        case Err(error=exc):
            yield Tell(f"parse failed: {exc}")
            return 0

prog = app("not-a-number")
prog = writer(prog)
result = run(prog)
print(result)  # 0
""",
        110,
    ),
}


@pytest.mark.parametrize("effect", sorted(STALE_EXAMPLES))
def test_check_names_the_stale_examples(
    effect: str, tmp_path: Path, deadline_scale_factor: float
) -> None:
    example = STALE_EXAMPLES[effect]
    mismatch = examples.mismatch(example, _run(example, tmp_path, deadline_scale_factor))
    assert mismatch is not None
    assert "failed" in mismatch
    assert f"UnhandledEffect: no handler found for effect: {effect}" in mismatch
