"""doeff.result の Some・Maybe と Err の error が型を持つ事を、実行時と pyright と mypy で確かめる。

失敗ケース(pyright 1.1.414・mypy 2.4.0 の実測・取り組み doeff-publish の調べ card ki-02e317783af0 の抜け 5):
``doeff/result.py`` に注記が無く、``Some(1).value`` は pyright で Unknown・mypy で Any だった。``Maybe = Some | _NothingType``
は型の引数を持たず、``Maybe[int]`` は両方の道具で error。``Err`` は Generic でなく ``Err(...).error`` は Any で、``Try`` の答えの
``Err`` の中身も Any だった。``Ok`` と ``Err`` の宣言に ``__match_args__`` が無く、docstring の ``case Ok(value):`` も両方の道具で
error。実行時も ``Maybe[int]``・``Ok[int]``・``Err[ValueError]`` は TypeError で、評価される注記に書けなかった。
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
    # mypy は dev の group の道具(make sync で入る)。Python Compatibility の CI の venv には無いので、訳を出して飛ばす
    # (tests/test_handler_installer_typing.py と同じ)
    pytest.importorskip(
        "mypy",
        reason="mypy が無い venv(Python Compatibility の CI は dev の group の maturin と pyright だけを入れる)",
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
    # 起動できない時も終了コードは 1 なので、stderr が空である事で「走って赤を出した」と「走れなかった」を分ける
    assert result.stderr == "", result.stderr
    assert result.returncode == 1, result.stdout
    assert len(errors) == len(result.stdout.splitlines()), result.stdout
    flagged = [(_LINES[int(error["line"]) - 1], error["code"]) for error in errors]
    assert flagged == [
        (_MISTAKES[0], "arg-type"),
        (_MISTAKES[1], "arg-type"),
        (_MISTAKES[2], "assignment"),
    ], result.stdout
