"""結果の型の違う task を Gather と Race に渡せる事を、実行時と pyright と mypy で確かめる。"""

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


# 失敗ケース(pyright 1.1.414・mypy 2.4.0 の実測・取り組み doeff-publish の調べ card ki-02e317783af0 の抜け 4):
# Task と Future の型の引数が不変だった時、`Gather(Task[int], Task[str])` は pyright で Task[str] を
# Task[int] | Future[int] に渡せない error、mypy で「_T を推せない」error になった(実行は通る)。
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
    # 当たらない overload は、pyright が「合う overload が無い」と最後の overload の引数の誤りの 2 つを出す
    flagged = sorted({_LINES[e["range"]["start"]["line"]].strip() for e in errors})
    assert flagged == sorted(_MISTAKES), errors


_MYPY_ERROR = re.compile(r"^[^:]+:(?P<line>\d+): error: (?P<message>.*)  \[(?P<code>[a-z-]+)\]$")


def test_gather_and_race_answer_the_union_of_the_result_types_under_mypy(tmp_path: Path) -> None:
    # mypy は型の引数の解を join で広げる(int と str は object)ので、union を答えるのは Gather と Race の
    # 引数の数ごとの overload — 同じ見本が mypy の既定の設定でも同じ所だけ赤になる
    pytest.importorskip("mypy", reason="mypy が無い venv(Python Compatibility の CI は dev の group の maturin と pyright だけを入れる)")
    sample = tmp_path / "mixed_handles.py"
    sample.write_text(_SAMPLE)
    result = subprocess.run(
        [sys.executable, "-B", "-m", "mypy", "--cache-dir", str(tmp_path / "mypy-cache"),
         "--show-error-codes", "--hide-error-context", "--no-error-summary", "--no-color-output",
         "--no-pretty", str(sample)],
        cwd=tmp_path, capture_output=True, text=True, timeout=120, check=False,
    )
    # 起動できない時も終了コードは 1 なので、stderr が空である事で「走って赤を出した」と「走れなかった」を分ける
    assert result.stderr == "", result.stderr
    assert result.returncode == 1, result.stdout
    errors = [found.groupdict() for found in map(_MYPY_ERROR.match, result.stdout.splitlines()) if found]
    flagged = [(_LINES[int(error["line"]) - 1].strip(), error["code"]) for error in errors]
    assert flagged == [
        ("return text", "return-value"),
        ("Gather(number, 42)", "call-overload"),
        ("Race(number, 42)", "call-overload"),
    ], result.stdout
