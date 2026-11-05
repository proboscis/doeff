"""Verify the public type of handler and the contract for reapplying an existing installer."""

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
    # The type ProgramHandler does not declare a function name, so confirm the installer is a function before reading the name
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
        # A composed handler can return a Program other than WithHandler.
        return _relay(body)


def test_existing_installer_can_return_a_different_program_node() -> None:
    install = _ExistingInstaller()
    assert handler(install) is install
    assert run(handler(install)(Pure(42))) == 42


def _pyright_errors(tmp_path: Path, source: str) -> list[dict[str, Any]]:
    """Type-check source with the repo's pyright config and return error diagnostics (a test of the type contract)."""
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
    # handler has two overloads, installer and plain handler, so 42, which fits neither, goes red
    # on two counts: "no matching overload" and an argument error for the last overload
    assert [(item["range"]["start"]["line"], item["rule"]) for item in errors] == [
        (7, "reportCallIssue"), (8, "reportCallIssue"),
        (9, "reportCallIssue"), (9, "reportArgumentType"),
    ], errors


# Failure case (measured with pyright 1.1.414 and mypy 2.4.0; gap 2 found in the doeff-publish initiative's investigation):
# When ProgramHandler was Callable[[object], Program[Any]], `reader(env)(prog())`, `state()(p)`, `writer(p)`,
# `handler(h)(p)`, and `stacked_handlers(...)(p)` were all Program[Any, Any] and `run(...)` was Any -- a mismatch in the result type
# passed silently. `writer(prog)` (forgetting to call) and `state()(42)` also passed (at run time, TypeError).
_RESULT_TYPE_SAMPLE = """\
from collections.abc import Callable, Generator
from typing import Any, assert_type

from doeff import Ask, Program, do, handler, run, stacked_handlers, with_handlers
from doeff.program import ProgramHandler
from doeff_core_effects._handlers import reader, state, writer
from doeff_hy._static_types import Handler


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


# The type of defhandler's handler and the spelling of the factory that takes the body as object (Callable[[object], Program] in doeff-records) are also installer
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
    """Type-check source with mypy's default config and return error findings (line, message, code) (a test of the type contract)."""
    # mypy is a tool of the dev group (installed by make sync -- daily and local). The CI venv for Python Compatibility installs only
    # maturin and pyright from the dev group, so there we give a reason and skip (2026-10-17 -- it ran without installing and failed on every version)
    pytest.importorskip("mypy", reason="venv without mypy (the Python Compatibility CI installs only maturin and pyright from the dev group)")
    sample = tmp_path / "installer_contract.py"
    sample.write_text(source)
    result = subprocess.run(
        [sys.executable, "-B", "-m", "mypy", "--cache-dir", str(tmp_path / "mypy-cache"),
         "--show-error-codes", "--hide-error-context", "--no-error-summary", "--no-color-output",
         "--no-pretty", str(sample)],
        cwd=tmp_path, capture_output=True, text=True, timeout=120, check=False,
    )
    errors = [found.groupdict() for found in map(_MYPY_ERROR.match, result.stdout.splitlines()) if found]
    # mypy writes findings to stdout. When it cannot start (no mypy in the venv -- the Python Compatibility CI of 2026-10-17), the
    # exit code is also 1, so an empty stderr distinguishes "mypy ran and reported red" from "could not run"
    assert result.stderr == "", result.stderr
    assert result.returncode == 1, result.stdout
    assert len(errors) == len(result.stdout.splitlines()), result.stdout
    return errors


def test_installers_keep_the_result_type_and_refuse_a_non_program_under_mypy(tmp_path: Path) -> None:
    # Users check with either pyright or mypy -- the same sample goes red at the same places under mypy's default config
    errors = _mypy_errors(tmp_path, _RESULT_TYPE_SAMPLE)
    flagged = [(_RESULT_TYPE_LINES[int(error["line"]) - 1], error["code"]) for error in errors]
    assert flagged == [
        (_MISTAKES[0], "assignment"),
        *((mistake, "arg-type") for mistake in _MISTAKES[1:]),
    ], errors


# Failure case (measured with pyright 1.1.414 and mypy 2.4.0; gap 7 found in the doeff-publish initiative's investigation):
# When the argument of handler() was Callable[..., object], a function whose effect annotation is not matched by the effect (`effect: str`), and
# a function that does not accept (effect, k), both passed the type check -- running it gives TypeError at handler() (check_handler_annotation).
# Narrow the accepted functions to "functions that take an EffectBase effect and K". A function with no effect annotation, Any, or object accepts all effects.
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
