"""doeff の effect と Program が型付きになっていることを、実行時と pyright の両方で確かめる。

実測(agora-controllers の実験 2026-09-23): doeff には py.typed が無く、EffectBase は Generic で
なく、@do は戻り値の型を Expand に変えて消していた。利用者は 300 行の型の層(Eff[T] の __iter__・
Prog[E, T]・program・reply)を手で書いていた。その層の中身を doeff 本体へ入れた:

- ``class ReadClock(EffectBase[int])`` → ``now = yield from ReadClock()`` の now は int
- ``@do`` は ``Callable[P, Expand[T, E]]`` を返す(引数・結果・出す effect の和が残る)
- 生成器の注記 ``Generator[E, Any, T]`` の E が「出してよい effect」の宣言(外の effect は赤)
- ``typed_resume(effect, k, value)`` は handler の答えの型を effect の T と突き合わせる
- 実行時の読み口(``effect_result_type`` / ``program_signature``)が同じ注記を読む(Hy の検査の土台)
"""

import json
import subprocess
import sys
from collections.abc import Generator
from dataclasses import dataclass
from pathlib import Path
from typing import Any, TypeVar

import pytest
from doeff_core_effects.effects import Put
from doeff_core_effects.handlers import state

from doeff import (
    EffectBase,
    EffectGenerator,
    Expand,
    ProgramSignature,
    Pure,
    do,
    effect_result_type,
    program_signature,
    run,
    typed_resume,
    with_handlers,
)

T = TypeVar("T")


@dataclass(frozen=True)
class ReadClock(EffectBase[int]):
    pass


class WriteEffect(EffectBase[T]):
    pass


@dataclass(frozen=True)
class WriteShared(WriteEffect[bool]):
    key: str


@do
def board(effect: ReadClock | WriteShared, k):
    if isinstance(effect, ReadClock):
        return (yield typed_resume(effect, k, 1000))
    return (yield typed_resume(effect, k, effect.key != "deny"))


@do
def elapsed(since: int) -> Generator[ReadClock, Any, int]:
    now = yield from ReadClock()
    return now - since


@do
def job() -> Generator[ReadClock | WriteShared, Any, tuple[int, bool, int, str]]:
    now = yield from ReadClock()
    ok = yield from WriteShared("row")
    spent = yield from elapsed(400)
    tag = yield from Pure("done")
    return now, ok, spent, tag


def test_yield_from_effects_and_programs_returns_the_answer() -> None:
    assert run(with_handlers([board], job())) == (1000, True, 600, "done")


def test_yield_and_yield_from_are_interchangeable() -> None:
    @do
    def mixed():
        a = yield ReadClock()
        b = yield from ReadClock()
        return a + b

    assert run(with_handlers([board], mixed())) == 2000


def test_handler_error_raises_at_the_yield_from_site() -> None:
    @do
    def refuse(effect: WriteShared, k):
        raise PermissionError(effect.key)
        yield

    @do
    def body():
        try:
            yield from WriteShared("x")
        except PermissionError as error:
            return f"caught {error}"
        return "not raised"

    assert run(with_handlers([refuse], body())) == "caught x"


def test_core_effects_declare_their_answer_types() -> None:
    @do
    def body():
        yield from Put("k", 1)
        return "ok"

    assert run(state()(body())) == "ok"
    assert effect_result_type(Put) is None  # EffectBase[None]


def test_scheduler_effects_run_with_yield_from() -> None:
    from doeff_core_effects.scheduler import (
        CompletePromise,
        CreatePromise,
        Gather,
        Spawn,
        Wait,
        scheduled,
    )

    @do
    def parent():
        task = yield from Spawn(elapsed(400))
        value = yield from Wait(task)
        values = yield from Gather(task, task)
        promise = yield from CreatePromise[str]()
        yield from CompletePromise(promise, "done")
        text = yield from Wait(promise.future)
        return value, values, text

    assert run(scheduled(with_handlers([board], parent()))) == (600, [600, 600], "done")


def test_generic_effect_classes_are_subscriptable_at_runtime() -> None:
    assert EffectBase[int].__origin__ is EffectBase
    assert Expand[int, ReadClock].__args__ == (int, ReadClock)
    assert ReadClock.__orig_bases__ == (EffectBase[int],)


def test_effect_result_type_follows_generic_parents() -> None:
    assert effect_result_type(ReadClock) is int
    assert effect_result_type(WriteShared("k")) is bool
    assert effect_result_type(WriteEffect) is T


def test_program_signature_reads_the_do_annotation() -> None:
    assert program_signature(job) == ProgramSignature(
        (ReadClock, WriteShared), tuple[int, bool, int, str]
    )
    assert program_signature(elapsed) == ProgramSignature((ReadClock,), int)

    @do
    def open_effects() -> EffectGenerator[str]:
        yield ReadClock()
        return "x"

    assert program_signature(open_effects) == ProgramSignature(None, str)


def test_typed_resume_counts_as_a_tail_resume() -> None:
    from doeff.do import _analyze_resume_yields

    def handler(effect, k):
        return (yield typed_resume(effect, k, 1))

    assert _analyze_resume_yields(handler, non_tail=False)


# --- pyright: correct code is clean, each deliberate mistake is caught ---------------------------

_SAMPLE = """\
from collections.abc import Callable, Generator
from dataclasses import dataclass
from typing import Any, TypeVar, assert_type

from doeff import EffectBase, Expand, Program, K, Pure, do, typed_resume
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


@do
def elapsed(since: int) -> Generator[ReadClock, Any, int]:
    now = yield from ReadClock()
    assert_type(now, int)
    return now - since


@do
def job() -> Generator[ReadClock | WriteShared | Ask | Get | Put | Try[int], Any, str]:
    ok = yield from WriteShared("row")
    assert_type(ok, bool)
    spent = yield from elapsed(1)
    assert_type(spent, int)
    tag = yield from Pure("x")
    assert_type(tag, str)
    anything = yield from Ask("worker")
    assert_type(anything, Any)
    stored = yield from Put("k", 1)
    assert_type(stored, None)
    tried = yield from Try(elapsed(1))
    assert_type(tried, Ok[int] | Err)
    return tag


elapsed_type: Callable[[int], Expand[int, ReadClock]] = elapsed
assert_type(job(), Expand[str, ReadClock | WriteShared | Ask | Get | Put | Try[int]])
program: Program[str] = job()


@do
def parent() -> Generator[Spawn[Any, Any] | ReadClock | Wait[int] | Gather[int] | CreatePromise[str], Any, int]:
    task = yield from Spawn(elapsed(1))
    assert_type(task, Task[int])
    value = yield from Wait(task)
    assert_type(value, int)
    values = yield from Gather(task, task)
    assert_type(values, list[int])
    promise = yield from CreatePromise[str]()
    assert_type(promise, Promise[str])
    return value


def clock(effect: ReadClock, k: K) -> Generator[Any, Any, None]:
    yield typed_resume(effect, k, 1000)


# ---- mistakes (each line below must be an error) ----
@do
def wrong_answer_type() -> Generator[ReadClock, Any, None]:
    now: str = yield from ReadClock()


@do
def undeclared_effect() -> Generator[ReadClock, Any, None]:
    yield from Delete("row")


@do
def undeclared_effect_of_a_called_program() -> Generator[WriteShared, Any, None]:
    yield from elapsed(1)


@do
def wrong_program_result() -> Generator[ReadClock, Any, None]:
    label: str = yield from elapsed(1)


def wrong_handler_answer(effect: ReadClock, k: K) -> Generator[Any, Any, None]:
    yield typed_resume(effect, k, "now")


@do
def spawn_without_child_effects() -> Generator[Spawn[Any, Any], Any, None]:
    yield from Spawn(elapsed(1))


@do
def wrong_wait_result(task: Task[int]) -> Generator[Wait[int], Any, None]:
    text: str = yield from Wait(task)


elapsed("1")
"""

_MISTAKE_MARKER = "# ---- mistakes"


def test_pyright_accepts_typed_code_and_catches_each_mistake(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[1]
    original = json.loads((root / "pyrightconfig.json").read_text())
    config = tmp_path / "pyrightconfig.json"
    config.write_text(json.dumps({
        "extends": str(root / "pyrightconfig.json"),
        "extraPaths": [str(root), *(str(root / path) for path in original["extraPaths"])],
    }))
    sample = tmp_path / "typed_sample.py"
    sample.write_text(_SAMPLE)
    result = subprocess.run(
        [sys.executable, "-m", "pyright", "--project", str(config),
         "--pythonpath", sys.executable, "--outputjson", str(sample)],
        cwd=root, capture_output=True, text=True, timeout=120, check=False,
    )
    report = json.loads(result.stdout)
    errors = [item for item in report["generalDiagnostics"] if item["severity"] == "error"]
    lines = _SAMPLE.splitlines()
    first_mistake = next(i for i, line in enumerate(lines) if line.startswith(_MISTAKE_MARKER))
    clean_part = [e for e in errors if e["range"]["start"]["line"] < first_mistake]
    assert clean_part == [], clean_part
    flagged = {lines[e["range"]["start"]["line"]].strip() for e in errors}
    expected = {
        "now: str = yield from ReadClock()",
        'yield from Delete("row")',
        "yield from elapsed(1)",
        "label: str = yield from elapsed(1)",
        'yield typed_resume(effect, k, "now")',
        'elapsed("1")',
        "yield from Spawn(elapsed(1))",
        "text: str = yield from Wait(task)",
    }
    assert flagged == expected, (flagged ^ expected, errors)


@pytest.mark.parametrize("module", ["doeff", "doeff_vm", "doeff_core_effects"])
def test_packages_ship_py_typed(module: str) -> None:
    package = __import__(module)
    assert (Path(package.__file__).parent / "py.typed").is_file()
