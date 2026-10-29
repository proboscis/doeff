"""handlerの公開型と、既存installerを再適用する契約を検証する。"""

from __future__ import annotations

import json
import re
import subprocess
import sys
import types
from collections.abc import Generator
from pathlib import Path
from typing import Any, Literal, TypeVar

import pytest
from doeff_core_effects import await_handler, scheduled, slog_discard_handler
from doeff_vm import K

from doeff import EffectBase, Pass, Program, Pure, do, handler, run
from tests.test_static_typing import _pyright_python

_T = TypeVar("_T")


@handler
def _forward(effect: EffectBase, k: K) -> Generator[Program, object, None]:
    yield Pass(effect, k)


def test_public_installers_accept_one_program_and_preserve_identity() -> None:
    install = handler(_forward)
    assert handler(install) is install
    # 型の ProgramHandler は関数の名を宣言しないので、installer が関数である事を確かめてから名を読む
    assert isinstance(install, types.FunctionType)
    assert install.__name__ == _forward.__name__
    assert run(install(Pure(42))) == 42
    assert run(slog_discard_handler(Pure(42))) == 42
    assert run(scheduled(await_handler(Pure(42)))) == 42


@do
def _relay(body: Program[_T]) -> Generator[Any, Any, _T]:
    return (yield from body)


class _ExistingInstaller:
    _doeff_is_handler_fn: Literal[True] = True

    def __call__(self, body: Program[_T]) -> Program[_T]:
        # 合成済みhandlerはWithHandler以外のProgramも返せる。
        return _relay(body)


def test_existing_installer_can_return_a_different_program_node() -> None:
    install = _ExistingInstaller()
    assert handler(install) is install
    assert run(handler(install)(Pure(42))) == 42


def _pyright_errors(tmp_path: Path, source: str) -> list[dict[str, Any]]:
    """source を repo の pyright の設定で型検査し、error の診断を返すため(型の契約の検)。"""
    root = Path(__file__).resolve().parents[1]
    original = json.loads((root / "pyrightconfig.json").read_text())
    config = tmp_path / "pyrightconfig.json"
    config.write_text(json.dumps({
        "extends": str(root / "pyrightconfig.json"),
        "extraPaths": [str(root), *(str(root / path) for path in original["extraPaths"])],
    }))
    sample = tmp_path / "installer_contract.py"
    sample.write_text(source)
    result = subprocess.run(
        [sys.executable, "-m", "pyright", "--project", str(config),
         "--pythonpath", str(_pyright_python(tmp_path)), "--outputjson", str(sample)],
        cwd=root, capture_output=True, text=True, timeout=120, check=False,
    )
    report = json.loads(result.stdout)
    return [item for item in report["generalDiagnostics"] if item["severity"] == "error"]


def test_installer_type_contract_accepts_composition_and_rejects_wrong_arity(
    tmp_path: Path,
) -> None:
    errors = _pyright_errors(tmp_path, """from typing import Any, assert_type
from doeff import Program, Pure, handler
from doeff_core_effects._handlers import await_handler, slog_discard_handler

assert_type(slog_discard_handler(Pure(42)), Program[int, Any])
assert_type(await_handler(Pure(42)), Program[int, Any])
assert_type(handler(slog_discard_handler)(Pure(42)), Program[int, Any])
slog_discard_handler()  # missing body
await_handler(Pure(42), Pure(0))  # extra argument
handler(42)  # non-callable
""")
    # handler は installer と素の handler の 2 つの overload を持つので、どちらにも合わない 42 は
    # 「合う overload が無い」と最後の overload の引数の誤りの 2 つで赤になる
    assert [(item["range"]["start"]["line"], item["rule"]) for item in errors] == [
        (7, "reportCallIssue"), (8, "reportCallIssue"),
        (9, "reportCallIssue"), (9, "reportArgumentType"),
    ], errors


# 失敗ケース(pyright 1.1.414・mypy 2.4.0 の実測・取り組み doeff-publish の調べ card ki-02e317783af0 の抜け 2):
# ProgramHandler が Callable[[object], Program[Any]] だった時、`reader(env)(prog())`・`state()(p)`・`writer(p)`・
# `handler(h)(p)`・`stacked_handlers(...)(p)` は全部 Program[Any, Any] で `run(...)` は Any — 結果の型の食い違いを
# 黙って通した。`writer(prog)`(呼び忘れ)と `state()(42)` も通った(実行時は TypeError)。
_RESULT_TYPE_SAMPLE = """\
from collections.abc import Callable, Generator
from typing import Any, assert_type

from doeff import Ask, Program, do, handler, run, stacked_handlers, with_handlers
from doeff.program import ProgramHandler
from doeff_core_effects._handlers import reader, state, writer
from doeff_hy.static_types import Handler


@do
def prog() -> Generator[Ask, Any, int]:
    return int((yield from Ask("k")))


assert_type(reader(env={"k": 1}), ProgramHandler)
assert_type(reader(env={"k": 1})(prog()), Program[int, Any])
assert_type(state()(prog()), Program[int, Any])
assert_type(writer(prog()), Program[int, Any])
assert_type(handler(writer)(prog()), Program[int, Any])
assert_type(stacked_handlers(reader(env={"k": 1}), state())(prog()), Program[int, Any])
assert_type(run(reader(env={"k": 1})(prog())), int)
assert_type(run(state()(writer(prog()))), int)
assert_type(run(with_handlers([state(), writer], prog())), int)


# defhandler の handler の型と、本文を object で受ける工場の綴り(doeff-records の Callable[[object], Program])も installer
def adopted(hy_handler: Handler, spelled: Callable[[object], Program]) -> list[ProgramHandler]:
    return [hy_handler, spelled]


# ---- mistakes (each line below must be an error) ----
answer: str = run(reader(env={"k": 1})(prog()))
writer(prog)
state()(42)
handler(writer)(42)
stacked_handlers(state())(prog)
"""


_RESULT_TYPE_LINES = _RESULT_TYPE_SAMPLE.splitlines()
_FIRST_MISTAKE = next(i for i, line in enumerate(_RESULT_TYPE_LINES) if line.startswith("# ---- mistakes"))
_MISTAKES = (
    'answer: str = run(reader(env={"k": 1})(prog()))',
    "writer(prog)",
    "state()(42)",
    "handler(writer)(42)",
    "stacked_handlers(state())(prog)",
)


def test_installers_keep_the_result_type_and_refuse_a_non_program(tmp_path: Path) -> None:
    errors = _pyright_errors(tmp_path, _RESULT_TYPE_SAMPLE)
    assert [e for e in errors if e["range"]["start"]["line"] < _FIRST_MISTAKE] == [], errors
    flagged = [(_RESULT_TYPE_LINES[e["range"]["start"]["line"]], e["rule"]) for e in errors]
    assert flagged == [
        (_MISTAKES[0], "reportAssignmentType"),
        *((mistake, "reportArgumentType") for mistake in _MISTAKES[1:]),
    ], errors


_MYPY_ERROR = re.compile(r"^[^:]+:(?P<line>\d+): error: (?P<message>.*)  \[(?P<code>[a-z-]+)\]$")


def _mypy_errors(tmp_path: Path, source: str) -> list[dict[str, str]]:
    """source を mypy の既定の設定で型検査し、error の所見(line・message・code)を返すため(型の契約の検)。"""
    # mypy は dev の group の道具(make sync で入る — 日次と手元)。Python Compatibility の CI の venv は dev の group から
    # maturin と pyright だけを入れるので、そこでは訳を出して飛ばす(2026-10-17 — 入れずに走らせて全部の版で failing になった)
    pytest.importorskip("mypy", reason="mypy が無い venv(Python Compatibility の CI は dev の group の maturin と pyright だけを入れる)")
    sample = tmp_path / "installer_contract.py"
    sample.write_text(source)
    result = subprocess.run(
        [sys.executable, "-B", "-m", "mypy", "--cache-dir", str(tmp_path / "mypy-cache"),
         "--show-error-codes", "--hide-error-context", "--no-error-summary", "--no-color-output",
         "--no-pretty", str(sample)],
        cwd=tmp_path, capture_output=True, text=True, timeout=120, check=False,
    )
    errors = [found.groupdict() for found in map(_MYPY_ERROR.match, result.stdout.splitlines()) if found]
    # mypy は所見を stdout に書く。起動できない時(venv に mypy が無い — 2026-10-17 の Python Compatibility の CI)も
    # 終了コードは 1 なので、stderr が空である事で「mypy が走って赤を出した」と「走れなかった」を分ける
    assert result.stderr == "", result.stderr
    assert result.returncode == 1, result.stdout
    assert len(errors) == len(result.stdout.splitlines()), result.stdout
    return errors


def test_installers_keep_the_result_type_and_refuse_a_non_program_under_mypy(tmp_path: Path) -> None:
    # 使う人は pyright でも mypy でも check する — 同じ見本が mypy の既定の設定でも同じ所だけ赤になる
    errors = _mypy_errors(tmp_path, _RESULT_TYPE_SAMPLE)
    flagged = [(_RESULT_TYPE_LINES[int(error["line"]) - 1], error["code"]) for error in errors]
    assert flagged == [
        (_MISTAKES[0], "assignment"),
        *((mistake, "arg-type") for mistake in _MISTAKES[1:]),
    ], errors


# 失敗ケース(pyright 1.1.414・mypy 2.4.0 の実測・取り組み doeff-publish の調べ card ki-02e317783af0 の抜け 7):
# handler() の引数が Callable[..., object] だった時、effect の注記に effect が当たらない関数(`effect: str`)も、
# (effect, k) を受けない関数も型の check を通った — 実行すると handler() の所で TypeError(check_handler_annotation)。
# 受ける関数を「EffectBase の effect と K を受ける関数」に絞る。effect の注記の無い関数・Any・object は全部の effect を受ける。
_RAW_HANDLER_SAMPLE = """\
from collections.abc import Generator
from dataclasses import dataclass
from functools import partial
from typing import Any

from doeff import EffectBase, Pass, Resume, do, handler
from doeff_core_effects._handlers import writer
from doeff_vm import K


@dataclass(frozen=True)
class Fetch(EffectBase[str]):
    url: str


@dataclass(frozen=True)
class Store(EffectBase[None]):
    key: str


class WriteEffect(EffectBase):
    \"\"\"A marker base the write effects share (derived from EffectBase).\"\"\"


@do
def fetches(effect: Fetch, k: K) -> Generator[Any, Any, None]:
    yield Pass(effect, k)


def either(effect: Fetch | Store, k: K) -> object: ...
def bare(effect, k): ...
def open_any(effect: Any, k: Any) -> object: ...
def open_object(effect: object, k: object) -> object: ...
def every(effect: EffectBase, k: K) -> object: ...
def writes(effect: WriteEffect, k: K) -> object: ...
def configured(prefix: str, effect: Fetch, k: K) -> object: ...


class Dispatcher:
    def __call__(self, effect: Fetch, k: K) -> object: ...
    def method(self, effect: Store, k: K) -> object: ...


@do
def text(effect: str, k: K) -> Generator[Any, Any, None]:
    yield Resume(k, None)


def number(effect: int, k: K) -> object: ...
def failure(effect: Exception, k: K) -> object: ...
def one_argument(effect: Fetch) -> object: ...
def not_a_continuation(effect: Fetch, k: int) -> object: ...


class TextDispatcher:
    def __call__(self, effect: str, k: K) -> object: ...


handler(fetches)
handler(either)
handler(bare)
handler(open_any)
handler(open_object)
handler(every)
handler(writes)
handler(partial(configured, "p"))
handler(Dispatcher())
handler(Dispatcher().method)
handler(lambda effect, k: None)
handler(writer)

# ---- mistakes (each line below must be an error) ----
handler(text)
handler(number)
handler(failure)
handler(TextDispatcher())
handler(one_argument)
handler(not_a_continuation)
"""


_RAW_HANDLER_LINES = _RAW_HANDLER_SAMPLE.splitlines()
_FIRST_RAW_MISTAKE = next(
    i for i, line in enumerate(_RAW_HANDLER_LINES) if line.startswith("# ---- mistakes")
)
_RAW_MISTAKES = tuple(line for line in _RAW_HANDLER_LINES[_FIRST_RAW_MISTAKE + 1:] if line)


def test_handler_refuses_a_raw_handler_no_effect_can_reach(tmp_path: Path) -> None:
    errors = _pyright_errors(tmp_path, _RAW_HANDLER_SAMPLE)
    assert [e for e in errors if e["range"]["start"]["line"] < _FIRST_RAW_MISTAKE] == [], errors
    flagged = sorted({_RAW_HANDLER_LINES[e["range"]["start"]["line"]] for e in errors})
    assert flagged == sorted(_RAW_MISTAKES), errors


def test_handler_refuses_a_raw_handler_no_effect_can_reach_under_mypy(tmp_path: Path) -> None:
    errors = _mypy_errors(tmp_path, _RAW_HANDLER_SAMPLE)
    assert [e for e in errors if int(e["line"]) - 1 < _FIRST_RAW_MISTAKE] == [], errors
    flagged = sorted({_RAW_HANDLER_LINES[int(e["line"]) - 1] for e in errors})
    assert flagged == sorted(_RAW_MISTAKES), errors
