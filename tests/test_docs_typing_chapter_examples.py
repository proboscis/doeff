"""Checks that the examples in the typing chapters (docs/23 and docs/24), as a reader copies them, produce no errors under pyright strict and mypy --strict
(initiative doeff-publish).

Typing report (gap 9 of 2026-10-17-doeff-user-typecheck.html): the 4 examples in the typing chapters are excerpts missing imports and definitions; copying them into a type check
left 16 names unresolved. The test that runs examples (tests/test_docs_page_examples.py) checks that examples run and print as annotated,
but does not look at type checks. The typing chapters say "copy it and neither pyright strict nor mypy --strict reports errors", and this test verifies that.

The fences of a page are written to files in a per-page dir with the same convention as the example test (tools/readme_examples.py): a continues fence is the script
appended to the fence above it, and a file= fence is the file of that name. Every shape a reader gets by copying from the top and stopping midway passes the type check. Excerpt fences
(snippets of a few program lines, and examples that deliberately show a type error) are not written. Readers cannot see the excerpt marker, so a page says "an example with import lines
makes one file", and this test also verifies that snippets have no imports. The pages to check are read from the rows of the contract table in pyproject.toml
that select this test (same as tests/test_docs_page_examples.py — so a commit adding a page does not conflict in this file's list).

One typed-example section each from the README and the introductory chapters is checked by tests/test_docs_typed_examples.py (one example of the section, and the result type stated in the annotation).
This test checks all the examples of the pages of the typing chapters.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

from tests.test_docs_entry_path_examples import _fences
from tests.test_docs_page_examples import (
    DOCS_PREFIX,
    prefixes_selecting,
    prefixes_that_are_not_pages,
)
from tests.test_readme_examples import ROOT, examples
from tests.test_static_typing import _pyright_python

# This test's file (path from the repo root — the way the contract table in pyproject.toml writes it).
THIS_CHECK = Path(__file__).resolve().relative_to(ROOT).as_posix()
# Pages to type check (names from docs/) — the rows of the contract table that select this test are the single definition.
TYPED_PAGES = tuple(prefix.removeprefix(DOCS_PREFIX) for prefix in prefixes_selecting(THIS_CHECK))
# The fence language of the fences to type check.
PYTHON = "python"
# Import lines (which snippet fences do not have).
_IMPORT_LINE = re.compile(r"^(?:import|from)\s", re.MULTILINE)


def _write_page(page: str, directory: Path) -> list[Path]:
    """Write the Python fences of a page that go into files (script and file=) into a directory, and return the list of written files."""
    directory.mkdir()
    return [
        examples.write(example, directory)
        for example in _fences(page)
        if example.language == PYTHON and example.written_as() is not None
    ]


def _pyright_findings(directory: Path, files: list[Path], tmp_path: Path) -> list[str]:
    """Run strict pyright on files with the directory as root, and return errors and warnings as a list of `file:line: rule: message`."""
    original = json.loads((ROOT / "pyrightconfig.json").read_text())
    config = directory / "pyrightconfig.json"
    config.write_text(
        json.dumps(
            {
                "extends": str(ROOT / "pyrightconfig.json"),
                "typeCheckingMode": "strict",
                "extraPaths": [str(ROOT), *(str(ROOT / path) for path in original["extraPaths"])],
            }
        )
    )
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pyright",
            "--project",
            str(config),
            "--pythonpath",
            str(_pyright_python(tmp_path)),
            "--outputjson",
            *map(str, files),
        ],
        cwd=directory,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    report = json.loads(result.stdout)
    return [
        f"{Path(item['file']).name}:{item['range']['start']['line'] + 1}: {item.get('rule', '')}: {item['message']}"
        for item in report["generalDiagnostics"]
        if item["severity"] in {"error", "warning"}
    ]


@pytest.mark.parametrize("page", TYPED_PAGES)
def test_page_examples_are_clean_under_strict_pyright(page: str, tmp_path: Path) -> None:
    directory = tmp_path / Path(page).stem
    files = _write_page(page, directory)
    # A page with no fences to write has verified nothing
    assert files, f"docs/{page} has no Python fences that go into files"
    assert _pyright_findings(directory, files, tmp_path) == []


@pytest.mark.parametrize("page", TYPED_PAGES)
def test_page_examples_are_clean_under_strict_mypy(page: str, tmp_path: Path) -> None:
    # mypy is a tool of the dev group (installed by make sync). The venv of the Python Compatibility CI installs only maturin and pyright from the dev group,
    # so there we give a reason and skip (same as tests/test_handler_installer_typing.py)
    pytest.importorskip(
        "mypy",
        reason="venv without mypy (the Python Compatibility CI installs only maturin and pyright from the dev group)",
    )
    directory = tmp_path / Path(page).stem
    files = _write_page(page, directory)
    assert files, f"docs/{page} has no Python fences that go into files"
    # Make the cwd the example dir — run from the repo root, mypy picks up the tree's doeff as a target to check rather than as a dependency to read
    result = subprocess.run(
        [
            sys.executable,
            "-B",
            "-m",
            "mypy",
            "--strict",
            "--cache-dir",
            str(tmp_path / "mypy-cache"),
            "--show-error-codes",
            "--hide-error-context",
            "--no-error-summary",
            "--no-color-output",
            "--no-pretty",
            *(path.name for path in files),
        ],
        cwd=directory,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    # Empty stderr and output with exit code 0 = mypy ran and found nothing (when it cannot run, it writes to stderr and exits with 2)
    assert result.stderr == ""
    assert result.stdout.splitlines() == []
    assert result.returncode == 0


@pytest.mark.parametrize("page", TYPED_PAGES)
def test_page_excerpts_have_no_imports(page: str) -> None:
    # If an example with import lines were made an excerpt, an example the page says "makes one file" would silently drop out of the type check
    excerpts_with_imports = [
        example.name
        for example in _fences(page)
        if example.language == PYTHON
        and examples.EXCERPT in example.words
        and _IMPORT_LINE.search(example.code)
    ]
    assert excerpts_with_imports == []


def test_rows_selecting_this_check_name_docs_pages() -> None:
    # A row of the table makes the `make test-changed` of a commit that changed a page select this test, and the same row decides the pages to check. If the row's prefix is not a page,
    # that page's examples are not covered by any type check.
    prefixes = prefixes_selecting(THIS_CHECK)
    assert prefixes, (
        f"[[tool.doeff.contract-tests]] in pyproject.toml has no row that selects {THIS_CHECK}"
    )
    assert prefixes_that_are_not_pages(prefixes) == []
