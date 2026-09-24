"""``@effectful`` / ``perform``: plain-Python effects, rewritten at import (docs/24-effectful-perform.md).

What is checked here:

- runtime: the rewritten function runs like the ``@do`` / ``yield`` program (Resume and
  Transfer handlers, nested programs, methods, Spawn / Wait / Gather, Cancel runs finally,
  cloudpickle of an unrun program to another process);
- tracebacks point at the original source lines;
- the rewrite refuses ``perform`` where it cannot suspend the program, with file and line;
- the bytecode cache is named by the rewrite version and rebuilt when it changes, and is not
  written while bytecode writing is off (``sys.dont_write_bytecode``);
- pyright: the answer types, and the 8 deliberate mistakes of tests/test_static_typing.py
  written in the perform form, are all caught.
"""

from __future__ import annotations

import importlib
import subprocess
import sys
import textwrap
import traceback
from pathlib import Path
from typing import Any

import cloudpickle
import pytest
from doeff_core_effects.scheduler import scheduled

import doeff._effectful_rewrite as rewrite
from doeff import Effects, Pass, Resume, Transfer, do, effectful, run, with_handlers
from doeff.cli import effectful_check
from tests.effectful_cases import programs
from tests.test_static_typing import _pyright

ROOT = Path(__file__).resolve().parents[1]


@do
def board(effect: object, k: Any):
    if isinstance(effect, programs.ReadClock):
        return (yield Resume(k, 1000))
    if isinstance(effect, programs.WriteShared):
        if effect.key == "deny":
            raise PermissionError(effect.key)
        return (yield Transfer(k, True))
    yield Pass(effect, k)  # not ours: pass it outward


def _run(program: object) -> object:
    return run(scheduled(with_handlers([board], program)))


# --- runtime ---------------------------------------------------------------------------


def test_perform_answers_like_yield() -> None:
    assert _run(programs.job()) == (1000, True, 600, "done")
    assert _run(programs.loop(3)) == 3000


def test_function_without_perform_calls_is_a_program_of_its_value() -> None:
    assert _run(programs.no_effects(3)) == 6


def test_nested_effectful_and_methods() -> None:
    assert _run(programs.outer_with_inner()) == 2002
    assert _run(programs.Service(5).shifted(1)) == 1006


def test_the_perform_parameter_is_removed_from_the_signature() -> None:
    import inspect

    assert list(inspect.signature(programs.elapsed).parameters) == ["since"]
    assert list(inspect.signature(programs.Service.shifted).parameters) == ["self", "by"]
    raw = programs.elapsed.__doeff_generator_function__
    assert inspect.isgeneratorfunction(raw)


def test_handler_error_raises_at_the_perform_site() -> None:
    assert _run(programs.caught_refusal()) == "caught deny"


def test_spawn_wait_gather() -> None:
    assert _run(programs.parallel()) == (999, [999, 998])


def test_cancel_runs_finally_of_an_effectful_task() -> None:
    notes: list[str] = []

    @do
    def keep_notes(effect: programs.Note, k: Any):
        notes.append(effect.text)
        return (yield Resume(k, None))

    program = with_handlers([keep_notes], programs.cancel_parked())
    assert run(scheduled(program)) == "cancelled"
    assert notes == ["start", "except", "finally"]


def test_unrun_program_travels_by_cloudpickle_to_another_process() -> None:
    payload = cloudpickle.dumps(programs.job())
    receiver = textwrap.dedent(
        """
        import sys, cloudpickle
        from doeff import do, run, with_handlers, Resume, Transfer
        program = cloudpickle.loads(sys.stdin.buffer.read())

        @do
        def board(effect, k):
            if type(effect).__name__ == "ReadClock":
                return (yield Resume(k, 7))
            return (yield Transfer(k, False))

        print(tuple(run(with_handlers([board], program))))
        """
    )
    result = subprocess.run(
        [sys.executable, "-c", receiver],
        input=payload,
        capture_output=True,
        cwd=ROOT,
        timeout=60,
        check=False,
    )
    assert result.returncode == 0, result.stderr.decode()
    assert result.stdout.decode().strip() == "(7, False, -393, 'done')"


# --- tracebacks ------------------------------------------------------------------------


def _line_of(tag: str) -> int:
    source = Path(programs.__file__).read_text().splitlines()
    return next(i for i, line in enumerate(source, 1) if f"# LINE: {tag}" in line)


def _frames_in_programs(error: BaseException) -> list[int]:
    return [
        frame.lineno or 0
        for frame in traceback.extract_tb(error.__traceback__)
        if frame.filename == programs.__file__
    ]


def test_traceback_points_at_the_raise_line() -> None:
    with pytest.raises(ValueError, match="boom 1000") as caught:
        _run(programs.raises_after_effect())
    assert _line_of("raise-after-effect") in _frames_in_programs(caught.value)


def test_traceback_of_a_handler_error_points_at_the_perform_line() -> None:
    with pytest.raises(PermissionError) as caught:
        _run(programs.refused_write())
    assert _frames_in_programs(caught.value) == [_line_of("refused-write")]


# --- what the rewrite refuses ----------------------------------------------------------

_HEADER = "from doeff import Effects, effectful\nfrom somewhere import ReadClock, g\n"

_REFUSED = {
    "outside @effectful": (
        "def plain(perform: Effects[ReadClock]) -> int:\n    return perform(ReadClock())\n",
        3,
        "is not @effectful",
    ),
    "lambda": (
        "@effectful\n"
        "def f(perform: Effects[ReadClock]) -> int:\n"
        "    read = lambda: perform(ReadClock())\n"
        "    return read()\n",
        5,
        "a lambda cannot suspend",
    ),
    "list comprehension": (
        "@effectful\n"
        "def f(perform: Effects[ReadClock]) -> list[int]:\n"
        "    return [perform(ReadClock()) for _ in range(3)]\n",
        5,
        "a comprehension cannot suspend",
    ),
    "generator expression": (
        "@effectful\n"
        "def f(perform: Effects[ReadClock]) -> int:\n"
        "    return sum(perform(ReadClock()) for _ in range(3))\n",
        5,
        "a comprehension cannot suspend",
    ),
    "nested plain function": (
        "@effectful\n"
        "def f(perform: Effects[ReadClock]) -> int:\n"
        "    def inner() -> int:\n"
        "        return perform(ReadClock())\n"
        "    return inner()\n",
        6,
        "nested function inner() cannot suspend",
    ),
    "class body": (
        "@effectful\n"
        "def f(perform: Effects[ReadClock]) -> int:\n"
        "    class Inner:\n"
        "        now = perform(ReadClock())\n"
        "    return Inner.now\n",
        6,
        "a class body cannot suspend",
    ),
    "passed as a value": (
        "@effectful\ndef f(perform: Effects[ReadClock]) -> int:\n    return g(perform)\n",
        5,
        "cannot be passed, stored or returned",
    ),
    "yield in the body": (
        "@effectful\n"
        "def f(perform: Effects[ReadClock]) -> int:\n"
        "    now = yield ReadClock()\n"
        "    return now\n",
        5,
        "not yield / await",
    ),
    "no perform parameter": (
        "@effectful\ndef f(since: int) -> int:\n    return since\n",
        4,
        "must take 'perform: Effects[...]'",
    ),
    "perform without Effects annotation": (
        "@effectful\ndef f(perform, since: int) -> int:\n    return since\n",
        4,
        "annotate the parameter",
    ),
    "two arguments": (
        "@effectful\n"
        "def f(perform: Effects[ReadClock]) -> int:\n"
        "    return perform(ReadClock(), 1)\n",
        5,
        "exactly one effect",
    ),
    "async def": (
        "@effectful\nasync def f(perform: Effects[ReadClock]) -> int:\n    return 1\n",
        4,
        "is async",
    ),
}


@pytest.mark.parametrize("case", sorted(_REFUSED))
def test_rewrite_refuses_with_file_and_line(case: str) -> None:
    body, line, message = _REFUSED[case]
    with pytest.raises(SyntaxError) as caught:
        rewrite.rewrite_source((_HEADER + body).encode(), "case.py")
    assert caught.value.filename == "case.py"
    assert caught.value.lineno == line, caught.value
    assert message in caught.value.msg


def test_decorator_refuses_a_module_that_was_not_rewritten() -> None:
    def elapsed(perform: Effects[programs.ReadClock], since: int) -> int:
        return perform(programs.ReadClock()) - since

    with pytest.raises(TypeError, match=r"was not rewritten at import.*install_import_hook\("):
        effectful(elapsed)


def test_static_check_command_reports_without_importing(tmp_path: Path) -> None:
    bad = tmp_path / "bad.py"
    bad.write_text(_HEADER + _REFUSED["lambda"][0])
    good = tmp_path / "good.py"
    good.write_text(Path(programs.__file__).read_text())
    assert effectful_check.main([str(good)]) == 0
    result = subprocess.run(
        [sys.executable, "-m", "doeff.cli.effectful_check", str(tmp_path)],
        capture_output=True,
        text=True,
        cwd=ROOT,
        timeout=60,
        check=False,
    )
    assert result.returncode == 1
    assert result.stdout.strip().startswith(f"{bad}:5:")
    assert "a lambda cannot suspend" in result.stdout


# --- import hook and bytecode cache ----------------------------------------------------


@pytest.fixture
def tmp_package(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    name = f"effectful_tmp_{tmp_path.name.replace('-', '_')}"
    package = tmp_path / name
    package.mkdir()
    (package / "__init__.py").write_text("")
    monkeypatch.syspath_prepend(str(tmp_path))
    rewrite.install_import_hook(name)
    yield name, package
    for module in [m for m in sys.modules if m == name or m.startswith(name + ".")]:
        del sys.modules[module]


def _import_fresh(name: str) -> Any:
    sys.modules.pop(name, None)
    importlib.invalidate_caches()
    return importlib.import_module(name)


_MODULE = (
    "from doeff import Effects, effectful\n"
    "from tests.effectful_cases.programs import ReadClock\n"
    "@effectful\n"
    "def twice(perform: Effects[ReadClock]) -> int:\n"
    "    return perform(ReadClock()) * 2\n"
)


def test_import_error_names_the_file_and_line(tmp_package: tuple[str, Path]) -> None:
    name, package = tmp_package
    (package / "bad.py").write_text(_HEADER + _REFUSED["list comprehension"][0])
    with pytest.raises(SyntaxError) as caught:
        _import_fresh(f"{name}.bad")
    assert caught.value.filename == str(package / "bad.py")
    assert caught.value.lineno == 5


def test_cache_is_named_by_the_rewrite_version_and_reused(
    tmp_package: tuple[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    # The cache is only written where bytecode may be written (land / the daily run set
    # PYTHONDONTWRITEBYTECODE=1), so this test states that precondition itself.
    monkeypatch.setattr(sys, "dont_write_bytecode", False)
    name, package = tmp_package
    source = package / "mod.py"
    source.write_text(_MODULE)
    assert _run(_import_fresh(f"{name}.mod").twice()) == 2000
    cached = rewrite.cache_path(str(source))
    # The one place that pins where the cache lives and how it is named; the other cache
    # tests ask cache_path() instead of spelling the location again.
    assert cached == (
        package
        / "__pycache__"
        / (f"mod.{sys.implementation.cache_tag}-doeff-effectful-{rewrite.REWRITE_VERSION}.pyc")
    )
    assert cached.is_file()

    def no_compile(source: bytes, filename: str) -> None:
        raise AssertionError(f"compiled {filename} although the cache is valid")

    monkeypatch.setattr(rewrite, "compile_source", no_compile)
    assert _run(_import_fresh(f"{name}.mod").twice()) == 2000


def test_a_new_rewrite_version_rebuilds_the_cache(
    tmp_package: tuple[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(sys, "dont_write_bytecode", False)
    name, package = tmp_package
    source = package / "mod.py"
    source.write_text(_MODULE)
    _import_fresh(f"{name}.mod")
    monkeypatch.setattr(rewrite, "REWRITE_VERSION", "next-version")
    module = _import_fresh(f"{name}.mod")
    assert module.__doeff_effectful__ == "next-version"
    rebuilt = rewrite.cache_path(str(source))
    assert "next-version" in rebuilt.name
    assert rebuilt.is_file()
    assert _run(module.twice()) == 2000


def test_no_cache_is_written_while_bytecode_writing_is_off(
    tmp_package: tuple[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    name, package = tmp_package
    source = package / "mod.py"
    source.write_text(_MODULE)
    cached = rewrite.cache_path(str(source))
    # Control: with writing on, this very location does get the cache — so its absence
    # below means the loader chose not to write, not that it could not.
    monkeypatch.setattr(sys, "dont_write_bytecode", False)
    _import_fresh(f"{name}.mod")
    assert cached.is_file()
    cached.unlink()

    monkeypatch.setattr(sys, "dont_write_bytecode", True)
    compiled: list[str] = []
    compile_source = rewrite.compile_source

    def counting_compile(source_bytes: bytes, filename: str) -> Any:
        compiled.append(filename)
        return compile_source(source_bytes, filename)

    monkeypatch.setattr(rewrite, "compile_source", counting_compile)
    assert _run(_import_fresh(f"{name}.mod").twice()) == 2000
    assert _run(_import_fresh(f"{name}.mod").twice()) == 2000
    assert not cached.exists()
    assert not list(cached.parent.glob(f"{cached.name}.*.tmp"))
    assert compiled.count(str(source)) == 2  # nothing was cached, so both imports compile


def test_modules_without_effectful_are_compiled_unchanged(tmp_package: tuple[str, Path]) -> None:
    name, package = tmp_package
    (package / "plain.py").write_text("VALUE = 1\n")
    module = _import_fresh(f"{name}.plain")
    assert module.VALUE == 1
    assert not hasattr(module, "__doeff_effectful__")


# --- pyright ---------------------------------------------------------------------------

_SAMPLE = """\
from collections.abc import Callable, Generator
from dataclasses import dataclass
from typing import Any, TypeVar, assert_type

from doeff import EffectBase, Effects, Expand, Program, K, Pure, effectful, typed_resume
from doeff_core_effects.effects import Ask, Get, Put, Try
from doeff_vm import Ok, Err
from doeff_core_effects.scheduler import CreatePromise, Gather, Promise, Spawn, Task, Wait

T = TypeVar("T")


@dataclass(frozen=True)
class ReadClock(EffectBase[int]):
    pass


class WriteEffect(EffectBase[T]):
    pass


@dataclass(frozen=True)
class WriteShared(WriteEffect[bool]):
    key: str


@dataclass(frozen=True)
class Delete(EffectBase[None]):
    key: str


@effectful
def elapsed(perform: Effects[ReadClock], since: int) -> int:
    now = perform(ReadClock())
    assert_type(now, int)
    return now - since


@effectful
def job(perform: Effects[ReadClock | WriteShared | Ask | Get | Put | Try[int]]) -> str:
    ok = perform(WriteShared("row"))
    assert_type(ok, bool)
    spent = perform(elapsed(1))
    assert_type(spent, int)
    tag = perform(Pure("x"))
    assert_type(tag, str)
    anything = perform(Ask("worker"))
    assert_type(anything, Any)
    stored = perform(Put("k", 1))
    assert_type(stored, None)
    tried = perform(Try(elapsed(1)))
    assert_type(tried, Ok[int] | Err)
    return tag


elapsed_type: Callable[[int], Expand[int, ReadClock]] = elapsed
assert_type(job(), Expand[str, ReadClock | WriteShared | Ask | Get | Put | Try[int]])
program: Program[str] = job()


@effectful
def parent(perform: Effects[Spawn[Any, Any] | ReadClock | Wait[int] | Gather[int] | CreatePromise[str]]) -> int:
    task = perform(Spawn(elapsed(1)))
    assert_type(task, Task[int])
    value = perform(Wait(task))
    assert_type(value, int)
    values = perform(Gather(task, task))
    assert_type(values, list[int])
    promise = perform(CreatePromise[str]())
    assert_type(promise, Promise[str])
    return value


class Service:
    @effectful
    def shifted(self, perform: Effects[ReadClock], by: int) -> int:
        return perform(ReadClock()) + by


assert_type(Service().shifted(1), Expand[int, ReadClock])


def clock(effect: ReadClock, k: K) -> Generator[Any, Any, None]:
    yield typed_resume(effect, k, 1000)


# ---- mistakes (each line below must be an error) ----
@effectful
def wrong_answer_type(perform: Effects[ReadClock]) -> None:
    now: str = perform(ReadClock())


@effectful
def undeclared_effect(perform: Effects[ReadClock]) -> None:
    perform(Delete("row"))


@effectful
def undeclared_effect_of_a_called_program(perform: Effects[WriteShared]) -> None:
    perform(elapsed(1))


@effectful
def wrong_program_result(perform: Effects[ReadClock]) -> None:
    label: str = perform(elapsed(1))


def wrong_handler_answer(effect: ReadClock, k: K) -> Generator[Any, Any, None]:
    yield typed_resume(effect, k, "now")


@effectful
def spawn_without_child_effects(perform: Effects[Spawn[Any, Any]]) -> None:
    perform(Spawn(elapsed(1)))


@effectful
def wrong_wait_result(perform: Effects[Wait[int]], task: Task[int]) -> None:
    text: str = perform(Wait(task))


elapsed("1")


def perform_outside_effectful() -> int:
    return perform(ReadClock())
"""

_MISTAKE_MARKER = "# ---- mistakes"


def test_pyright_types_perform_and_catches_each_mistake(tmp_path: Path) -> None:
    report = _pyright(tmp_path, _SAMPLE)
    errors = [item for item in report["generalDiagnostics"] if item["severity"] == "error"]
    lines = _SAMPLE.splitlines()
    first_mistake = next(i for i, line in enumerate(lines) if line.startswith(_MISTAKE_MARKER))
    clean_part = [e for e in errors if e["range"]["start"]["line"] < first_mistake]
    assert clean_part == [], clean_part
    flagged = {lines[e["range"]["start"]["line"]].strip() for e in errors}
    expected = {
        "now: str = perform(ReadClock())",
        'perform(Delete("row"))',
        "perform(elapsed(1))",
        "label: str = perform(elapsed(1))",
        'yield typed_resume(effect, k, "now")',
        "perform(Spawn(elapsed(1)))",
        "text: str = perform(Wait(task))",
        'elapsed("1")',
        "return perform(ReadClock())",  # outside @effectful: perform is not defined
    }
    assert flagged == expected, (flagged ^ expected, errors)


def test_the_case_programs_are_strict_clean(tmp_path: Path) -> None:
    report = _pyright(tmp_path, Path(programs.__file__).read_text())
    problems = [d for d in report["generalDiagnostics"] if d["severity"] in {"error", "warning"}]
    assert problems == [], problems
