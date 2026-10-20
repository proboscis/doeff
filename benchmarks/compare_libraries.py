"""Compare doeff with plain Python and other Python effect libraries.

Every scenario does the same work in each implementation and checks the
result, so a slower implementation cannot win by skipping work:

- ``round_trip``: perform one effect and get the handler's answer back, N times.
- ``nested_calls``: a chain of sub-programs D levels deep, the bottom one
  performing one effect.
- ``handler_depth``: one effect that passes H unrelated handlers before its own.
- ``spawn_gather``: spawn N no-op tasks and gather their results.
- ``fan_out_wait``: spawn N tasks that all wait on one promise, then release it.

Usage
-----
    uv run --with effect==1.1.0 --with effectful==0.4.0 \\
        python benchmarks/compare_libraries.py --runs 30
    uv run python benchmarks/compare_libraries.py --smoke --no-output

The other libraries are not doeff dependencies: ``uv run --with`` lays them
over the project environment for one run. A library that is not installed is
listed as skipped. Each sample runs every case once in a shuffled order, so a
change of machine load during the run falls on every case alike. The primary
clock is the process CPU time (``time.process_time_ns``), which a busy shared
machine inflates far less than the wall clock; both are recorded.
"""

from __future__ import annotations

import argparse
import asyncio
import collections
import datetime as dt
import gc
import importlib.metadata
import json
import os
import platform
import random
import socket
import subprocess
import sys
import sysconfig
import time
from collections.abc import Callable, Generator, Sequence
from contextlib import ExitStack
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from doeff_core_effects import Ask, Gather, Spawn, reader, scheduled
from doeff_core_effects.scheduler import (
    CompletePromise,
    CreatePromise,
    SchedulerImplementation,
    Wait,
    resolve_implementation,
)

from doeff import EffectBase, EffectGenerator, K, Pass, Resume, do, handler, run, with_handlers

if TYPE_CHECKING:
    from doeff_core_effects.scheduler import Future, Promise, Task

RESULTS_DIR = Path("benchmarks/results")
SCHEDULER_IMPLEMENTATIONS = ("python", "rust")


@dataclass(frozen=True)
class CompareConfig:
    """Sizes of every scenario and how many samples to take of each case."""

    runs: int = 30
    warmup: int = 3
    round_trip_effects: int = 2_000
    nested_depth: int = 200
    nested_repeats: int = 10
    handler_depth: int = 20
    handler_depth_effects: int = 500
    tasks: int = 1_000
    seed: int = 0
    smoke: bool = False

    @classmethod
    def smoke_config(cls) -> CompareConfig:
        """The smallest sizes, to check in CI that every case still runs and agrees."""
        return cls(
            runs=1,
            warmup=0,
            round_trip_effects=2,
            nested_depth=2,
            nested_repeats=1,
            handler_depth=2,
            handler_depth_effects=2,
            tasks=2,
            smoke=True,
        )


@dataclass(frozen=True)
class Case:
    """One implementation of one scenario: the work to time and the answer it must give."""

    scenario: str
    implementation: str
    library: str
    unit: str
    units: int
    workload: Callable[[], object]
    expected: object


@dataclass(frozen=True)
class CaseStats:
    """The recorded samples of one case, in µs per unit of work."""

    scenario: str
    implementation: str
    library: str
    unit: str
    units: int
    cpu_us_per_unit: tuple[float, ...]
    wall_us_per_unit: tuple[float, ...]

    def summary(self) -> dict[str, float]:
        """The distribution read in the report: median and p90, CPU first."""
        return {
            "cpu_median_us": _quantile(self.cpu_us_per_unit, 0.5),
            "cpu_p90_us": _quantile(self.cpu_us_per_unit, 0.9),
            "cpu_min_us": min(self.cpu_us_per_unit),
            "wall_median_us": _quantile(self.wall_us_per_unit, 0.5),
            "wall_p90_us": _quantile(self.wall_us_per_unit, 0.9),
        }


def _quantile(values: Sequence[float], q: float) -> float:
    """Linear-interpolated quantile, so median and p90 are defined for any sample count."""
    ordered = sorted(values)
    position = q * (len(ordered) - 1)
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)


# ---------------------------------------------------------------------------
# plain Python: the floors every library is measured against
# ---------------------------------------------------------------------------


class _Get:
    """Request of the hand-written generator driver: answered with 1."""


class _Other:
    """A request no scenario makes; its handlers only stand in the way."""


_GET = _Get()


def _plain_get() -> int:
    """The work every handler does: answer 1."""
    return 1


def _plain_round_trip(n: int) -> int:
    """Floor of round_trip: the 'effect' is an ordinary function call."""
    total = 0
    for _index in range(n):
        total += _plain_get()
    return total


def _drive(
    gen: Generator[object, int, int], handlers: Sequence[tuple[type, Callable[[], int]]]
) -> int:
    """The smallest generator effect interpreter: answer each request with its first handler."""
    try:
        request = next(gen)
        while True:
            for request_type, answer in handlers:
                if isinstance(request, request_type):
                    request = gen.send(answer())
                    break
            else:
                raise LookupError(f"no handler for {request!r}")
    except StopIteration as stop:
        return stop.value


def _gen_round_trip(n: int) -> Generator[object, int, int]:
    """Floor of round_trip for generator-based effects: yield a request, get the answer."""
    total = 0
    for _index in range(n):
        total += yield _GET
    return total


def _plain_nested(depth: int) -> int:
    """Floor of nested_calls: plain recursion down to one call at the bottom."""
    if depth == 0:
        return _plain_get()
    return _plain_nested(depth - 1)


def _plain_nested_repeated(depth: int, repeats: int) -> int:
    """Run the plain chain several times so one sample is long enough to time."""
    return sum(_plain_nested(depth) for _index in range(repeats))


def _gen_nested(depth: int) -> Generator[object, int, int]:
    """Floor of nested_calls for generators: a yield-from chain with one request at the bottom."""
    if depth == 0:
        return (yield _GET)
    return (yield from _gen_nested(depth - 1))


def _gen_nested_repeated(depth: int, repeats: int) -> Generator[object, int, int]:
    """Run the generator chain several times inside one driver run."""
    total = 0
    for _index in range(repeats):
        total += yield from _gen_nested(depth)
    return total


def _gen_handler_stack(depth: int) -> list[tuple[type, Callable[[], int]]]:
    """Handlers innermost first: `depth` unrelated ones stand before the one that answers."""
    return [(_Other, _plain_get) for _index in range(depth)] + [(_Get, _plain_get)]


def _gen_noop_task() -> Generator[None, None, None]:
    """A task of the round-robin floor that finishes at its first step."""
    return None
    yield  # a generator function


def _gen_spawn_gather(n: int) -> list[None]:
    """Floor of spawn_gather: a round-robin run queue of generator tasks."""
    ready = collections.deque(enumerate(_gen_noop_task() for _index in range(n)))
    results: list[None] = [None] * n
    while ready:
        index, gen = ready.popleft()
        try:
            next(gen)
        except StopIteration as stop:
            results[index] = stop.value
        else:
            ready.append((index, gen))  # back to the run queue
    return results


async def _asyncio_noop() -> None:
    """A task of the asyncio spawn_gather that does nothing."""
    return


async def _asyncio_spawn_gather_main(n: int) -> list[None]:
    """asyncio's spawn_gather: gather wraps each coroutine in a task."""
    return list(await asyncio.gather(*(_asyncio_noop() for _index in range(n))))


def _asyncio_spawn_gather(n: int) -> list[None]:
    """One asyncio.run, as doeff's case is one run(scheduled(...))."""
    return asyncio.run(_asyncio_spawn_gather_main(n))


async def _asyncio_wait_on(future: asyncio.Future[int]) -> int:
    """A fan_out_wait task: park on the shared future."""
    return await future


async def _asyncio_release(future: asyncio.Future[int]) -> None:
    """The fan_out_wait task that wakes every parked task at once."""
    future.set_result(1)


async def _asyncio_fan_out_main(n: int) -> int:
    """asyncio's fan_out_wait: the waiters start first and park; the releaser runs after them."""
    future: asyncio.Future[int] = asyncio.get_running_loop().create_future()
    tasks = [asyncio.create_task(_asyncio_wait_on(future)) for _index in range(n)]
    releaser = asyncio.create_task(_asyncio_release(future))
    total = sum(await asyncio.gather(*tasks))
    await releaser
    return total


def _asyncio_fan_out(n: int) -> int:
    """One asyncio.run of fan_out_wait."""
    return asyncio.run(_asyncio_fan_out_main(n))


# ---------------------------------------------------------------------------
# doeff
# ---------------------------------------------------------------------------


class GetOne(EffectBase[int]):
    """The effect every doeff scenario performs."""


class Unrelated(EffectBase[int]):
    """An effect no scenario performs; its handlers only stand in the way."""


@handler
def _doeff_resume_handler(effect: GetOne, k: K) -> EffectGenerator[int]:
    """The usual handler shape: a @do generator that resumes the continuation."""
    return (yield Resume(k, 1))


@handler
def _doeff_unrelated_handler(effect: Unrelated, k: K) -> EffectGenerator[int]:
    """A handler typed for another effect: the VM skips it by its annotation."""
    return (yield Resume(k, 2))


@handler
def _doeff_pass_handler(effect: EffectBase, k: K) -> EffectGenerator[int]:
    """An untyped handler that sees every effect and forwards the ones it does not answer."""
    if isinstance(effect, Unrelated):
        return (yield Resume(k, 2))
    return (yield Pass(effect, k))


@do
def _doeff_round_trip(n: int) -> EffectGenerator[int]:
    """round_trip in doeff: perform GetOne n times."""
    total = 0
    for _index in range(n):
        total += yield GetOne()
    return total


@do
def _doeff_ask_round_trip(n: int) -> EffectGenerator[int]:
    """round_trip through the built-in reader handler instead of a user handler."""
    total = 0
    for _index in range(n):
        total += yield Ask("one")
    return total


@do
def _doeff_nested(depth: int) -> EffectGenerator[int]:
    """nested_calls in doeff: each level yields the program of the level below."""
    if depth == 0:
        return (yield GetOne())
    return (yield _doeff_nested(depth - 1))


@do
def _doeff_nested_repeated(depth: int, repeats: int) -> EffectGenerator[int]:
    """Run the doeff chain several times inside one run()."""
    total = 0
    for _index in range(repeats):
        total += yield _doeff_nested(depth)
    return total


@do
def _doeff_noop() -> EffectGenerator[None]:
    """A spawn_gather task that does nothing."""
    return None
    yield  # a generator function


@do
def _doeff_spawn_gather(n: int) -> EffectGenerator[list[None]]:
    """spawn_gather in doeff: Spawn n tasks, then Gather them."""
    # A comprehension cannot yield; filling a preallocated list keeps each step O(1).
    slots: list[Task[None] | None] = [None] * n
    for index in range(n):
        slots[index] = yield Spawn(_doeff_noop())
    return (yield Gather(*(task for task in slots if task is not None)))


@do
def _doeff_wait_on(future: Future[int]) -> EffectGenerator[int]:
    """A fan_out_wait task: park on the shared promise."""
    return (yield Wait(future))


@do
def _doeff_release(promise: Promise[int]) -> EffectGenerator[None]:
    """The fan_out_wait task that wakes every parked task at once."""
    yield CompletePromise(promise, 1)


@do
def _doeff_fan_out(n: int) -> EffectGenerator[int]:
    """fan_out_wait in doeff: the waiters start first and park; the releaser runs after them."""
    promise = yield CreatePromise()
    slots: list[Task[int] | None] = [None] * n
    for index in range(n):
        slots[index] = yield Spawn(_doeff_wait_on(promise.future))
    releaser = yield Spawn(_doeff_release(promise))
    total = sum((yield Gather(*(task for task in slots if task is not None))))
    yield Wait(releaser)
    return total


def _doeff_cases(config: CompareConfig) -> list[Case]:
    """doeff's cases: both scheduler implementations, and the default one."""
    n = config.round_trip_effects
    depth, repeats = config.nested_depth, config.nested_repeats
    h, h_effects = config.handler_depth, config.handler_depth_effects
    tasks = config.tasks
    # with_handlers lists the outermost first: the answering handler sits outside the others
    typed_stack = [_doeff_resume_handler, *([_doeff_unrelated_handler] * h)]
    pass_stack = [_doeff_resume_handler, *([_doeff_pass_handler] * h)]
    plain_cases = [
        Case(
            "round_trip",
            "doeff: @do handler + Resume",
            "doeff",
            "effect",
            n,
            lambda: run(with_handlers([_doeff_resume_handler], _doeff_round_trip(n))),
            n,
        ),
        Case(
            "round_trip",
            "doeff: built-in reader (Ask)",
            "doeff",
            "effect",
            n,
            lambda: run(reader({"one": 1})(_doeff_ask_round_trip(n))),
            n,
        ),
        Case(
            "nested_calls",
            "doeff: nested @do programs",
            "doeff",
            "level",
            depth * repeats,
            lambda: run(
                with_handlers([_doeff_resume_handler], _doeff_nested_repeated(depth, repeats))
            ),
            repeats,
        ),
        Case(
            "handler_depth",
            "doeff: typed handlers (skipped by annotation)",
            "doeff",
            "effect",
            h_effects,
            lambda: run(with_handlers(typed_stack, _doeff_round_trip(h_effects))),
            h_effects,
        ),
        Case(
            "handler_depth",
            "doeff: untyped handlers (each yields Pass)",
            "doeff",
            "effect",
            h_effects,
            lambda: run(with_handlers(pass_stack, _doeff_round_trip(h_effects))),
            h_effects,
        ),
    ]
    # None = what a plain `scheduled(body)` runs: neither the argument nor
    # $DOEFF_SCHEDULER chooses, so DEFAULT_IMPLEMENTATION decides. The label names
    # the implementation it resolved to, so a run before and after a change of the
    # default compares the same call.
    default_label = f"default = {resolve_implementation()}"
    choices: list[tuple[SchedulerImplementation | None, str]] = [
        *((implementation, implementation) for implementation in SCHEDULER_IMPLEMENTATIONS),
        (None, default_label),
    ]
    scheduler_cases = [
        case
        for implementation, label in choices
        for case in (
            Case(
                "spawn_gather",
                f"doeff: scheduled ({label} scheduler)",
                "doeff",
                "task",
                tasks,
                lambda implementation=implementation: run(
                    scheduled(_doeff_spawn_gather(tasks), implementation=implementation)
                ),
                [None] * tasks,
            ),
            Case(
                "fan_out_wait",
                f"doeff: scheduled ({label} scheduler)",
                "doeff",
                "task",
                tasks,
                lambda implementation=implementation: run(
                    scheduled(_doeff_fan_out(tasks), implementation=implementation)
                ),
                tasks,
            ),
        )
    ]
    return plain_cases + scheduler_cases


# ---------------------------------------------------------------------------
# effect (python-effect): the same generator-and-dispatcher shape as doeff
# ---------------------------------------------------------------------------


def _effect_cases(config: CompareConfig) -> list[Case]:
    """python-effect's cases; it has no scheduler, so fan_out_wait is absent."""
    from effect import (
        ComposedDispatcher,
        Effect,
        ParallelEffects,
        TypeDispatcher,
        base_dispatcher,
        parallel,
        sync_perform,
        sync_performer,
    )
    from effect.do import do as effect_do
    from effect.parallel_async import perform_parallel_async

    class EffGet:
        """python-effect intent answered with 1."""

    class EffOther:
        """python-effect intent no scenario performs (and the no-op task of spawn_gather)."""

    @sync_performer
    def perform_get(dispatcher: object, intent: EffGet) -> int:
        """Performer of EffGet."""
        return 1

    @sync_performer
    def perform_other(dispatcher: object, intent: EffOther) -> int:
        """Performer of the unrelated intent in handler_depth."""
        return 2

    @sync_performer
    def perform_noop(dispatcher: object, intent: EffOther) -> None:
        """Performer of the no-op task in spawn_gather."""
        return

    dispatcher = ComposedDispatcher([TypeDispatcher({EffGet: perform_get}), base_dispatcher])
    deep_dispatcher = ComposedDispatcher(
        [TypeDispatcher({EffOther: perform_other}) for _index in range(config.handler_depth)]
        + [TypeDispatcher({EffGet: perform_get}), base_dispatcher]
    )
    noop_dispatcher = ComposedDispatcher(
        [
            TypeDispatcher({EffOther: perform_noop, ParallelEffects: perform_parallel_async}),
            base_dispatcher,
        ]
    )

    @effect_do
    def round_trip(n: int) -> Generator[Effect, int, int]:
        """round_trip in python-effect: yield Effect(EffGet()) n times."""
        total = 0
        for _index in range(n):
            total += yield Effect(EffGet())
        return total

    @effect_do
    def nested(depth: int) -> Generator[Effect, int, int]:
        """nested_calls in python-effect: each level yields the effect of the level below."""
        if depth == 0:
            return (yield Effect(EffGet()))
        return (yield nested(depth - 1))

    @effect_do
    def nested_repeated(depth: int, repeats: int) -> Generator[Effect, int, int]:
        """Run the python-effect chain several times inside one sync_perform."""
        total = 0
        for _index in range(repeats):
            total += yield nested(depth)
        return total

    def spawn_gather(n: int) -> list[None]:
        """python-effect's nearest to spawn_gather: parallel() over n no-op effects."""
        effects = [Effect(EffOther()) for _index in range(n)]
        return sync_perform(noop_dispatcher, parallel(effects))

    n = config.round_trip_effects
    depth, repeats = config.nested_depth, config.nested_repeats
    h_effects = config.handler_depth_effects
    return [
        Case(
            "round_trip",
            "effect: @do + sync performer",
            "effect",
            "effect",
            n,
            lambda: sync_perform(dispatcher, round_trip(n)),
            n,
        ),
        Case(
            "nested_calls",
            "effect: nested @do effects",
            "effect",
            "level",
            depth * repeats,
            lambda: sync_perform(dispatcher, nested_repeated(depth, repeats)),
            repeats,
        ),
        Case(
            "handler_depth",
            "effect: TypeDispatcher chain",
            "effect",
            "effect",
            h_effects,
            lambda: sync_perform(deep_dispatcher, round_trip(h_effects)),
            h_effects,
        ),
        Case(
            "spawn_gather",
            "effect: parallel() (runs the effects one by one)",
            "effect",
            "task",
            config.tasks,
            lambda: spawn_gather(config.tasks),
            [None] * config.tasks,
        ),
    ]


# ---------------------------------------------------------------------------
# effectful (Basis): algebraic effects in direct style, no generators
# ---------------------------------------------------------------------------


def _effectful_cases(config: CompareConfig) -> list[Case]:
    """effectful's cases; it has no scheduler, so spawn_gather and fan_out_wait are absent."""
    from effectful.ops.semantics import handler
    from effectful.ops.syntax import defop
    from effectful.ops.types import NotHandled, Operation

    @defop
    def get_one() -> int:
        """effectful operation answered with 1."""
        raise NotHandled

    def unrelated_op() -> Operation[[], int]:
        """A fresh operation no scenario performs; its handler only stands in the way."""

        @defop
        def unrelated() -> int:
            """effectful operation no scenario performs."""
            raise NotHandled

        return unrelated

    unrelated_ops = [unrelated_op() for _index in range(config.handler_depth)]

    def round_trip(n: int) -> int:
        """round_trip in effectful: call the operation n times."""
        total = 0
        for _index in range(n):
            total += get_one()
        return total

    def nested(depth: int) -> int:
        """nested_calls in effectful: plain recursion, the operation at the bottom."""
        if depth == 0:
            return get_one()
        return nested(depth - 1)

    def nested_repeated(depth: int, repeats: int) -> int:
        """Run the effectful chain several times inside one handler scope."""
        return sum(nested(depth) for _index in range(repeats))

    def handled(body: Callable[[], int]) -> int:
        """Run body with get_one answered."""
        with handler({get_one: lambda: 1}):
            return body()

    def handled_under_unrelated(body: Callable[[], int]) -> int:
        """Run body with get_one answered outside, and the unrelated handlers inside."""
        with handler({get_one: lambda: 1}), ExitStack() as stack:
            for op in unrelated_ops:
                stack.enter_context(handler({op: lambda: 2}))
            return body()

    n = config.round_trip_effects
    depth, repeats = config.nested_depth, config.nested_repeats
    h_effects = config.handler_depth_effects
    return [
        Case(
            "round_trip",
            "effectful: defop + handler",
            "effectful",
            "effect",
            n,
            lambda: handled(lambda: round_trip(n)),
            n,
        ),
        Case(
            "nested_calls",
            "effectful: nested plain calls",
            "effectful",
            "level",
            depth * repeats,
            lambda: handled(lambda: nested_repeated(depth, repeats)),
            repeats,
        ),
        Case(
            "handler_depth",
            "effectful: nested handler scopes",
            "effectful",
            "effect",
            h_effects,
            lambda: handled_under_unrelated(lambda: round_trip(h_effects)),
            h_effects,
        ),
    ]


def _plain_cases(config: CompareConfig) -> list[Case]:
    """The plain Python floors of every scenario."""
    n = config.round_trip_effects
    depth, repeats = config.nested_depth, config.nested_repeats
    h_effects = config.handler_depth_effects
    one_handler = _gen_handler_stack(0)
    deep_handlers = _gen_handler_stack(config.handler_depth)
    tasks = config.tasks
    return [
        Case(
            "round_trip",
            "Python: function call",
            "python",
            "effect",
            n,
            lambda: _plain_round_trip(n),
            n,
        ),
        Case(
            "round_trip",
            "Python: generator + driver loop",
            "python",
            "effect",
            n,
            lambda: _drive(_gen_round_trip(n), one_handler),
            n,
        ),
        Case(
            "nested_calls",
            "Python: nested function calls",
            "python",
            "level",
            depth * repeats,
            lambda: _plain_nested_repeated(depth, repeats),
            repeats,
        ),
        Case(
            "nested_calls",
            "Python: nested generators (yield from)",
            "python",
            "level",
            depth * repeats,
            lambda: _drive(_gen_nested_repeated(depth, repeats), one_handler),
            repeats,
        ),
        Case(
            "handler_depth",
            "Python: generator + handler list",
            "python",
            "effect",
            h_effects,
            lambda: _drive(_gen_round_trip(h_effects), deep_handlers),
            h_effects,
        ),
        Case(
            "spawn_gather",
            "Python: asyncio.gather",
            "python",
            "task",
            tasks,
            lambda: _asyncio_spawn_gather(tasks),
            [None] * tasks,
        ),
        Case(
            "spawn_gather",
            "Python: round-robin generator tasks",
            "python",
            "task",
            tasks,
            lambda: _gen_spawn_gather(tasks),
            [None] * tasks,
        ),
        Case(
            "fan_out_wait",
            "Python: asyncio tasks awaiting one Future",
            "python",
            "task",
            tasks,
            lambda: _asyncio_fan_out(tasks),
            tasks,
        ),
    ]


LIBRARY_CASES: dict[str, Callable[[CompareConfig], list[Case]]] = {
    "python": _plain_cases,
    "doeff": _doeff_cases,
    "effect": _effect_cases,
    "effectful": _effectful_cases,
}


def _library_cases(  # noqa: DOEFF006 — benchmark の case 表の戻り
    library: str, config: CompareConfig
) -> tuple[list[Case], tuple[str, str] | None]:
    """One library's cases, or why it was skipped (the comparison libraries are optional)."""
    try:
        return LIBRARY_CASES[library](config), None
    except ImportError as error:
        return [], (library, f"not installed ({error.name})")


def build_cases(  # noqa: DOEFF006 — benchmark の case 表の戻り
    config: CompareConfig, libraries: Sequence[str] | None = None
) -> tuple[list[Case], dict[str, str]]:
    """The cases of every installed library, and why the others were skipped."""
    chosen = [name for name in LIBRARY_CASES if libraries is None or name in libraries]
    built = [_library_cases(name, config) for name in chosen]
    cases = [case for library_cases, _skip in built for case in library_cases]
    skipped = dict(skip for _cases, skip in built if skip is not None)
    return cases, skipped


def _time_case(case: Case) -> tuple[float, float]:  # noqa: DOEFF006 — benchmark の測定値の組
    """Time one run of a case (CPU, wall µs per unit) and check its answer."""
    gc.collect()
    wall_start = time.perf_counter_ns()
    cpu_start = time.process_time_ns()
    result = case.workload()
    cpu_ns = time.process_time_ns() - cpu_start
    wall_ns = time.perf_counter_ns() - wall_start
    if result != case.expected:
        raise AssertionError(
            f"{case.implementation} / {case.scenario}: expected {case.expected!r}, got {result!r}"
        )
    return cpu_ns / 1_000 / case.units, wall_ns / 1_000 / case.units


def _sample(
    cases: Sequence[Case], order: Sequence[int], label: str, progress: Callable[[str], None] | None
) -> dict[int, tuple[float, float]]:
    """One sample: every case once, in the given order, keyed by case index."""
    timings = {index: _time_case(cases[index]) for index in order}
    if progress is not None:
        progress(label)
    return timings


def measure(
    cases: Sequence[Case],
    config: CompareConfig,
    *,
    progress: Callable[[str], None] | None = None,
) -> list[CaseStats]:
    """Take warmup + runs samples, each in a fresh shuffled order, and keep the last runs."""
    rng = random.Random(config.seed)
    total = config.warmup + config.runs
    samples = [
        _sample(
            cases,
            rng.sample(range(len(cases)), len(cases)),
            f"{'warmup' if sample < config.warmup else 'sample'} {sample + 1}/{total}",
            progress,
        )
        for sample in range(total)
    ]
    recorded = samples[config.warmup :]
    return [
        CaseStats(
            case.scenario,
            case.implementation,
            case.library,
            case.unit,
            case.units,
            tuple(timings[index][0] for timings in recorded),
            tuple(timings[index][1] for timings in recorded),
        )
        for index, case in enumerate(cases)
    ]


# ---------------------------------------------------------------------------
# environment, report, CLI
# ---------------------------------------------------------------------------


def _cpu_model() -> str:
    """The CPU name, so a reader can tell which machine the numbers come from."""
    try:
        lines = Path("/proc/cpuinfo").read_text().splitlines()
    except OSError:
        lines = []
    models = [line.split(":", 1)[1].strip() for line in lines if line.startswith("model name")]
    return models[0] if models else (platform.processor() or platform.machine())


def _git_revision() -> str | None:
    """The doeff commit measured, when the script runs from a checkout."""
    try:
        completed = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            check=True,
            cwd=Path(__file__).resolve().parent,
        )
    except (OSError, subprocess.CalledProcessError):
        return None
    return completed.stdout.strip()


def _version(distribution: str) -> str | None:
    """The installed version of a distribution, None when it is absent."""
    try:
        return importlib.metadata.version(distribution)
    except importlib.metadata.PackageNotFoundError:
        return None


def environment() -> dict[str, object]:
    """What the numbers depend on: machine, load, Python build and library versions."""
    is_gil_enabled = getattr(sys, "_is_gil_enabled", None)  # absent before Python 3.13
    return {
        "hostname": socket.gethostname(),
        "platform": platform.platform(),
        "cpu": _cpu_model(),
        "cpu_count": os.cpu_count(),
        "loadavg": os.getloadavg() if hasattr(os, "getloadavg") else None,
        "python": platform.python_version(),
        "python_implementation": platform.python_implementation(),
        "free_threading_build": bool(sysconfig.get_config_var("Py_GIL_DISABLED")),
        "gil_enabled": is_gil_enabled() if is_gil_enabled is not None else True,
        "doeff_revision": _git_revision(),
        "versions": {
            name: _version(name)
            for name in ("doeff", "doeff-vm", "doeff-core-effects", "effect", "effectful")
        },
    }


def build_payload(
    config: CompareConfig,
    stats: Sequence[CaseStats],
    skipped: dict[str, str],
    env_before: dict[str, object],
    env_after: dict[str, object],
) -> dict[str, object]:
    """The JSON record of one run: environment, sizes, every sample and the summaries."""
    return {
        "schema_version": 1,
        "created_at": dt.datetime.now(dt.UTC).isoformat(),
        "environment": env_before,
        "loadavg_after": env_after["loadavg"],
        "config": asdict(config),
        "skipped": skipped,
        "results": [{**asdict(s), **s.summary()} for s in stats],
    }


def _scenario_table(rows: Sequence[CaseStats]) -> list[str]:
    """One markdown table of a scenario, fastest first, with the ratio to plain Python."""
    plain = [row.summary()["cpu_median_us"] for row in rows if row.library == "python"]
    baseline = min(plain) if plain else float("nan")
    unit = rows[0].unit
    header = [
        f"\n## {rows[0].scenario} (µs of CPU per {unit}, {rows[0].units} {unit}s per sample)",
        "| implementation | median | p90 | min | × fastest plain Python |",
        "|---|---:|---:|---:|---:|",
    ]
    ordered = sorted(rows, key=lambda row: row.summary()["cpu_median_us"])
    body = [
        f"| {row.implementation} | {s['cpu_median_us']:.3f} | {s['cpu_p90_us']:.3f} | "
        f"{s['cpu_min_us']:.3f} | {s['cpu_median_us'] / baseline:.1f} |"
        for row in ordered
        for s in (row.summary(),)
    ]
    return header + body


def format_report(stats: Sequence[CaseStats], skipped: dict[str, str]) -> str:
    """The tables printed after a run, one per scenario."""
    scenarios = list(dict.fromkeys(s.scenario for s in stats))
    tables = [
        line
        for scenario in scenarios
        for line in _scenario_table([s for s in stats if s.scenario == scenario])
    ]
    notes = [f"\nskipped {library}: {reason}" for library, reason in skipped.items()]
    return "\n".join(tables + notes)


def default_results_path() -> Path:
    """Where a run writes its JSON unless told otherwise."""
    date_label = dt.datetime.now(dt.UTC).strftime("%Y%m%d")
    host_label = socket.gethostname().split(".", maxsplit=1)[0]
    return RESULTS_DIR / f"compare-{date_label}-{host_label}.json"


def main() -> None:
    """Command line: measure every installed library and print and save the tables."""
    defaults = CompareConfig()
    parser = argparse.ArgumentParser(description="Compare doeff with plain Python and others")
    parser.add_argument("--runs", type=int, default=defaults.runs, help="Samples per case")
    parser.add_argument("--warmup", type=int, default=defaults.warmup, help="Unrecorded samples")
    parser.add_argument("--effects", type=int, default=defaults.round_trip_effects)
    parser.add_argument("--depth", type=int, default=defaults.nested_depth)
    parser.add_argument("--handlers", type=int, default=defaults.handler_depth)
    parser.add_argument("--tasks", type=int, default=defaults.tasks)
    parser.add_argument("--seed", type=int, default=defaults.seed)
    parser.add_argument(
        "--library",
        action="append",
        choices=sorted(LIBRARY_CASES),
        help="Measure only these libraries (repeatable; default: all installed)",
    )
    parser.add_argument("--smoke", action="store_true", help="One tiny sample per case")
    parser.add_argument("--output", type=Path, default=None)
    parser.add_argument("--no-output", action="store_true")
    args = parser.parse_args()

    config = (
        CompareConfig.smoke_config()
        if args.smoke
        else CompareConfig(
            runs=args.runs,
            warmup=args.warmup,
            round_trip_effects=args.effects,
            nested_depth=args.depth,
            handler_depth=args.handlers,
            tasks=args.tasks,
            seed=args.seed,
        )
    )
    cases, skipped = build_cases(config, args.library)
    env_before = environment()
    stats = measure(cases, config, progress=lambda line: print(line, file=sys.stderr))
    env_after = environment()
    payload = build_payload(config, stats, skipped, env_before, env_after)

    build = " (free-threading)" if env_before["free_threading_build"] else ""
    print(
        f"{env_before['cpu']} ×{env_before['cpu_count']} | Python {env_before['python']}{build} | "
        f"runs={config.runs} warmup={config.warmup} | "
        f"loadavg {env_before['loadavg']} -> {env_after['loadavg']}"
    )
    print(format_report(stats, skipped))

    if not args.no_output:
        output_path = args.output or default_results_path()
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
        print(f"\nwrote JSON results: {output_path}")


if __name__ == "__main__":  # pragma: no cover - CLI script
    main()
