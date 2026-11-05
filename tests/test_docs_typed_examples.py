"""Test that the typed examples in the README and the introductory chapters stay at 0 errors under the strictest type check (initiative doeff-publish).

Gaps 10 and 11 of the type report (2026-10-17-doeff-user-typecheck.html): the README and introductory examples were written with `yield` and no annotations, so under pyright
strict 75 of the 76 introductory examples were errors, and mypy reads an unannotated @do function as `def (*Any, **Any) -> Any` and does not look inside.
How to write types existed only in the types chapter (docs/23). The README section Type checking and the introductory section Adding Types each got one example written with `yield from` and
a `Generator[effects, Any, result]` annotation, plus a note that mypy needs the annotation.

Running the examples and comparing the printed output with annotations is done by tests/test_readme_examples.py and tests/test_docs_entry_path_examples.py. This test guards that the same fences
have 0 errors under pyright strict and mypy --strict, and that both tools answer the result type the example states in its annotation -- if a type declaration is
changed and the example turns red, the user's editor that copied it turns red too. Without checking the result type, even if doeff's types fell to Any, strict would stay green
without reporting an error (Any is not an error even under strict).
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

import pytest

from tests.test_readme_examples import ROOT, Example, examples
from tests.test_static_typing import _pyright_python


@dataclass(frozen=True)
class TypedExample:
    """One section that holds a typed example."""

    # Path from the repo root
    page: str
    # Heading of the section that holds the example (after `## `)
    section: str
    # Name of the value the example builds last, and the type its line's annotation states (`summary = run(...)  # summary: str`)
    result: str
    result_type: str


TYPED_EXAMPLES = (
    TypedExample("README.md", "Type checking", "summary", "str"),
    TypedExample("docs/01-getting-started.md", "Adding Types", "result", "str"),
)
# What the section says (the card's guard -- mypy needs the annotation).
MYPY_NEEDS_ANNOTATIONS = "mypy needs the annotations"
# Time limit per child (seconds). Both pyright and mypy take about 5-15 seconds for 2 files.
CHILD_DEADLINE_SECONDS = 180


def _section_text(typed: TypedExample) -> str:
    """The section body (from the line after the heading to before the next `## ` heading)."""
    text = (ROOT / typed.page).read_text(encoding="utf-8")
    heading = re.search(rf"^## {re.escape(typed.section)}[ \t]*$", text, re.MULTILINE)
    assert heading is not None, f"{typed.page} has no section {typed.section}"
    following = re.search(r"^## ", text[heading.end() :], re.MULTILINE)
    return (
        text[heading.end() :]
        if following is None
        else text[heading.end() : heading.end() + following.start()]
    )


def _typed_fence(typed: TypedExample) -> Example:
    """The section's python fence to run (exactly one)."""
    found = [
        example
        for example in examples.fences((ROOT / typed.page).read_text(encoding="utf-8"), typed.page)
        if example.section == typed.section and example.language == "python" and example.runs()
    ]
    assert len(found) == 1, (
        f"{typed.page} section {typed.section} has {len(found)} python fences to run"
    )
    return found[0]


def _write_checked(tmp_path: Path, number: int, typed: TypedExample) -> Path:
    """Write a file that appends assert_type of the result type stated in the annotation after the example -- if the type falls to Any, both tools turn red."""
    source = tmp_path / f"typed_example_{number}.py"
    source.write_text(
        f"{_typed_fence(typed).code}\nfrom typing import assert_type\n\n"
        f"assert_type({typed.result}, {typed.result_type})\n",
        encoding="utf-8",
    )
    return source


def _checked_sources(tmp_path: Path) -> list[Path]:
    """Write all of TYPED_EXAMPLES into a file to put through the type check."""
    return [_write_checked(tmp_path, number, typed) for number, typed in enumerate(TYPED_EXAMPLES)]


@pytest.mark.parametrize("typed", TYPED_EXAMPLES, ids=lambda typed: typed.page)
def test_typed_example_uses_yield_from_and_the_generator_annotation(typed: TypedExample) -> None:
    code = _typed_fence(typed).code
    assert "yield from " in code, (
        f"{typed.page}'s typed example does not use yield from -- the answer of yield is Any"
    )
    assert re.search(r"\) -> Generator\[", code), (
        f"{typed.page}'s typed example does not write a Generator annotation on the @do function"
    )
    # The assert_type added by the two tests below checks the result type the example states -- if the annotation and the table drift apart, it is named here
    assert f"# {typed.result}: {typed.result_type}" in code, (
        f"{typed.page}'s typed example does not say `# {typed.result}: {typed.result_type}` -- fit the TYPED_EXAMPLES table to the example"
    )
    assert MYPY_NEEDS_ANNOTATIONS in _section_text(typed), (
        f"{typed.page}'s section does not say that mypy needs the annotation"
    )


def test_typed_examples_have_no_error_under_pyright_strict(tmp_path: Path) -> None:
    sources = _checked_sources(tmp_path)
    # Do not inherit the repo's settings -- if an inherited setting disables rules, it does not return to strict
    config = tmp_path / "pyrightconfig.json"
    config.write_text(
        json.dumps({"typeCheckingMode": "strict", "pythonVersion": "3.11"}), encoding="utf-8"
    )
    result = subprocess.run(
        [
            sys.executable,
            "-B",
            "-m",
            "pyright",
            "--project",
            str(config),
            "--pythonpath",
            str(_pyright_python(tmp_path)),
            "--outputjson",
            *map(str, sources),
        ],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=CHILD_DEADLINE_SECONDS,
        check=False,
    )
    report = json.loads(result.stdout)
    assert report["summary"]["filesAnalyzed"] == len(sources), report["summary"]
    errors = [
        f"{Path(item['file']).name}:{item['range']['start']['line'] + 1}: {item['message']}"
        for item in report["generalDiagnostics"]
        if item["severity"] == "error"
    ]
    assert errors == [], errors


def test_typed_examples_have_no_error_under_mypy_strict(tmp_path: Path) -> None:
    pytest.importorskip(
        "mypy",
        reason="venv without mypy (the Python Compatibility CI installs only dev group's maturin and pyright)",
    )
    sources = _checked_sources(tmp_path)
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
            "--no-color-output",
            "--no-pretty",
            *map(str, sources),
        ],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=CHILD_DEADLINE_SECONDS,
        check=False,
    )
    # Exit code is 1 even when launch fails, so distinguish "ran and went red" from "could not run" by whether stderr is empty
    assert result.stderr == "", result.stderr
    assert result.returncode == 0, result.stdout
    assert f"no issues found in {len(sources)} source files" in result.stdout, result.stdout
