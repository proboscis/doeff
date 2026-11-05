"""Verify at runtime, with pyright, and with mypy that tasks with differing result types can be passed to Gather and Race."""

from __future__ import annotations

import re
import subprocess
import sys
from collections.abc import Generator
from pathlib import Path
from typing import Any

import pytest
from doeff_core_effects import Gather, Race, Spawn, scheduled

from doeff import do, run
from tests.test_handler_installer_typing import _pyright_errors


@do
def _count() -> Generator[Any, Any, int]:
    return 1
    yield


@do
def _name() -> Generator[Any, Any, str]:
    return "a"
    yield


def test_gather_and_race_run_tasks_of_different_result_types() -> None:
    @do
    def main() -> Generator[Any, Any, tuple[list[int | str], int | str]]:
        number = yield from Spawn(_count())
        text = yield from Spawn(_name())
        both = yield from Gather(number, text)
        first = yield from Race(number, text)
        return both, first

    both, first = run(scheduled(main()))
    assert both == [1, "a"]
    assert first in (1, "a")


# Failure case (measured with pyright 1.1.414 and mypy 2.4.0; gap 4 in the doeff-publish investigation):
# When the type arguments of Task and Future were invariant, `Gather(Task[int], Task[str])` gave pyright an error that Task[str] cannot be passed to
# Task[int] | Future[int], and gave mypy a "cannot infer _T" error (execution still worked).
_SAMPLE = """\
from collections.abc import Generator
from typing import Any, assert_type

from doeff import do
from doeff_core_effects._scheduler import CreatePromise, Future, Gather, Race, Spawn, Task


@do
def count() -> Generator[Any, Any, int]:
    return 1
    yield


@do
def name() -> Generator[Any, Any, str]:
    return "a"
    yield


@do
def mixed() -> Generator[Any, Any, list[int | str]]:
    number = yield from Spawn(count())
    text = yield from Spawn(name())
    promise = yield from CreatePromise[bytes]()
    both = yield from Gather(number, text)
    assert_type(both, list[int | str])
    assert_type((yield from Gather(number, text, promise.future)), list[int | str | bytes])
    assert_type((yield from Gather(number, text, number, text, number, text)), list[int | str])
    assert_type((yield from Gather(number, number)), list[int])
    assert_type((yield from Gather(*[number, number])), list[int])
    assert_type((yield from Race(number, text)), int | str)
    assert_type((yield from Race(number, promise.future, priority=None)), int | bytes)
    assert_type((yield from Race(*[number, number], priority=None)), int)
    return both


def widened(number: Task[int], read: Future[bytes]) -> tuple[Task[object], Future[object]]:
    return number, read


# ---- mistakes (each line below must be an error) ----
def narrowed(text: Task[str]) -> Task[int]:
    return text


def not_a_handle(number: Task[int]) -> None:
    Gather(number, 42)
    Race(number, 42)
"""

_LINES = _SAMPLE.splitlines()
_FIRST_MISTAKE = next(i for i, line in enumerate(_LINES) if line.startswith("# ---- mistakes"))
_MISTAKES = ["return text", "Gather(number, 42)", "Race(number, 42)"]


def test_gather_and_race_answer_the_union_of_the_result_types(tmp_path: Path) -> None:
    errors = _pyright_errors(tmp_path, _SAMPLE)
    assert [e for e in errors if e["range"]["start"]["line"] < _FIRST_MISTAKE] == [], errors
    # For the overload that does not match, pyright reports two errors: "no matching overload" and an argument error on the last overload
    flagged = sorted({_LINES[e["range"]["start"]["line"]].strip() for e in errors})
    assert flagged == sorted(_MISTAKES), errors


_MYPY_ERROR = re.compile(r"^[^:]+:(?P<line>\d+): error: (?P<message>.*)  \[(?P<code>[a-z-]+)\]$")


def test_gather_and_race_answer_the_union_of_the_result_types_under_mypy(tmp_path: Path) -> None:
    # mypy widens the solution of type arguments by join (int and str become object), so what answers the union is the per-argument-count
    # overloads of Gather and Race — the same sample turns red only at the same places under mypy's default settings
    pytest.importorskip("mypy", reason="venv without mypy (the Python Compatibility CI installs only maturin and pyright of the dev group)")
    sample = tmp_path / "mixed_handles.py"
    sample.write_text(_SAMPLE)
    result = subprocess.run(
        [sys.executable, "-B", "-m", "mypy", "--cache-dir", str(tmp_path / "mypy-cache"),
         "--show-error-codes", "--hide-error-context", "--no-error-summary", "--no-color-output",
         "--no-pretty", str(sample)],
        cwd=tmp_path, capture_output=True, text=True, timeout=120, check=False,
    )
    # The exit code is 1 even when mypy cannot start, so an empty stderr distinguishes "ran and reported red" from "could not run"
    assert result.stderr == "", result.stderr
    assert result.returncode == 1, result.stdout
    errors = [found.groupdict() for found in map(_MYPY_ERROR.match, result.stdout.splitlines()) if found]
    flagged = [(_LINES[int(error["line"]) - 1].strip(), error["code"]) for error in errors]
    assert flagged == [
        ("return text", "return-value"),
        ("Gather(number, 42)", "call-overload"),
        ("Race(number, 42)", "call-overload"),
    ], result.stdout
