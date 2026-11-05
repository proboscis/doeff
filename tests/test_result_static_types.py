"""Check at runtime, with pyright, and with mypy that Some, Maybe, and the error of Err in doeff.result carry types.

Failure cases (measured with pyright 1.1.414 and mypy 2.4.0; gap 5 of the doeff-publish initiative's investigation):
``doeff/result.py`` had no annotations, so ``Some(1).value`` was Unknown in pyright and Any in mypy. ``Maybe = Some | _NothingType``
took no type arguments, so ``Maybe[int]`` was an error in both tools. ``Err`` was not Generic and ``Err(...).error`` was Any, and the contents of the ``Err`` in a ``Try`` answer
were also Any. The declarations of ``Ok`` and ``Err`` had no ``__match_args__``, so the docstring's ``case Ok(value):`` was also an
error in both tools. At runtime too, ``Maybe[int]``, ``Ok[int]``, and ``Err[ValueError]`` raised TypeError and could not be written in evaluated annotations.
"""

import pickle
import re
import subprocess
import sys
from pathlib import Path
from typing import get_args, get_origin, get_type_hints

import pytest

from doeff import Err, Maybe, Nothing, Ok, Some
from doeff.result import _NothingType
from tests.test_static_typing import _pyright


def _annotated(found: Maybe[int], some: Some[str], result: Ok[int] | Err[ValueError]) -> Maybe[str]:
    return Nothing


def test_result_types_can_be_written_in_evaluated_annotations() -> None:
    hints = get_type_hints(_annotated)
    assert hints["found"] == Some[int] | _NothingType
    assert hints["return"] == Some[str] | _NothingType
    assert (get_origin(hints["some"]), get_args(hints["some"])) == (Some, (str,))
    ok, err = get_args(hints["result"])
    assert (get_origin(ok), get_args(ok)) == (Ok, (int,))
    assert (get_origin(err), get_args(err)) == (Err, (ValueError,))


def test_typed_some_and_nothing_keep_their_runtime_behaviour() -> None:
    some = Some[int](1)
    assert some == Some(1)
    assert hash(some) == hash(Some(1))
    assert repr(some) == "Some(1)"
    assert pickle.loads(pickle.dumps(some)) == some
    assert pickle.loads(pickle.dumps(Nothing)) is Nothing
    assert (bool(some), bool(Nothing)) == (True, False)
    match some:
        case Some(value):
            assert value == 1
    assert not hasattr(
        some, "__dict__"
    )  # Generic adds no __dict__: the slots stay the only storage


# The type checker sees the same facts: strict pyright and mypy --strict are clean on the part
# above the marker and flag each line below it.
_SAMPLE = """\
# pyright: strict
from collections.abc import Generator
from typing import Any, Literal, assert_type

from doeff import Err, Maybe, Nothing, Ok, Pure, Some, do
from doeff_core_effects._effects import Try


def first(items: list[int]) -> Maybe[int]:
    return Some(items[0]) if items else Nothing


def describe(found: Maybe[int]) -> int:
    match found:
        case Some(value):
            assert_type(value, int)
            return value
        case _:
            assert_type(found.is_nothing(), Literal[True])
            return 0


def when_present(found: Maybe[int]) -> int:
    if found:
        assert_type(found, Some[int])
        return found.value
    return 0


def recover(result: Ok[int] | Err[ValueError]) -> int:
    match result:
        case Ok(value):
            assert_type(value, int)
            return value
        case Err(error):
            assert_type(error, ValueError)
            return len(error.args)


@do
def attempt() -> Generator[Try[int], Any, int]:
    tried = yield from Try(Pure(1))
    match tried:
        case Ok(value):
            return value
        case Err(error):
            assert_type(error, Exception)
            return 0


assert_type(Some(1).value, int)
assert_type(Some("a"), Some[str])
assert_type(Err(KeyError("k")).error, KeyError)
widened: Maybe[float] = first([1])
caught: Err[Exception] = Err(ValueError("x"))
# Bare names mean Maybe[Any] / Some[Any] / Err[Any]: code written before they carried a type stays clean
untyped: Maybe = Nothing
some: Some = Some(1)
reason: Err = Err("business reason")

# ---- mistakes (each line below must be an error) ----
wrong_value: Maybe[int] = Some("a")
wrong_error: Err[ValueError] = Err(KeyError("k"))
count: int = Some("a").value
"""

_LINES = _SAMPLE.splitlines()
_FIRST_MISTAKE = next(i for i, line in enumerate(_LINES) if line.startswith("# ---- mistakes"))
_MISTAKES = (
    'wrong_value: Maybe[int] = Some("a")',
    'wrong_error: Err[ValueError] = Err(KeyError("k"))',
    'count: int = Some("a").value',
)


def test_pyright_reads_the_value_and_error_types(tmp_path: Path) -> None:
    report = _pyright(tmp_path, _SAMPLE)
    problems = [d for d in report["generalDiagnostics"] if d["severity"] in {"error", "warning"}]
    assert [d for d in problems if d["range"]["start"]["line"] < _FIRST_MISTAKE] == [], problems
    flagged = [(_LINES[d["range"]["start"]["line"]], d["rule"]) for d in problems]
    assert flagged == [(mistake, "reportAssignmentType") for mistake in _MISTAKES], problems


_MYPY_ERROR = re.compile(r"^[^:]+:(?P<line>\d+): error: (?P<message>.*)  \[(?P<code>[a-z-]+)\]$")


def test_mypy_reads_the_value_and_error_types(tmp_path: Path) -> None:
    # mypy is a tool in the dev group (installed by make sync). The Python Compatibility CI venv lacks it, so skip with a reason
    # (same as tests/test_handler_installer_typing.py)
    pytest.importorskip(
        "mypy",
        reason="venv without mypy (the Python Compatibility CI installs only maturin and pyright from the dev group)",
    )
    sample = tmp_path / "result_types.py"
    sample.write_text(_SAMPLE)
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
            str(sample),
        ],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    errors = [
        found.groupdict() for found in map(_MYPY_ERROR.match, result.stdout.splitlines()) if found
    ]
    # The exit code is 1 even when it cannot start, so an empty stderr distinguishes "ran and reported red" from "could not run"
    assert result.stderr == "", result.stderr
    assert result.returncode == 1, result.stdout
    assert len(errors) == len(result.stdout.splitlines()), result.stdout
    flagged = [(_LINES[int(error["line"]) - 1], error["code"]) for error in errors]
    assert flagged == [
        (_MISTAKES[0], "arg-type"),
        (_MISTAKES[1], "arg-type"),
        (_MISTAKES[2], "assignment"),
    ], result.stdout
