import asyncio
import time

import pytest

from doeff import Await, Gather, ProgramInterpreter, do, EffectGenerator
from doeff.cesk import Parallel
from doeff.cesk_adapter import CESKInterpreter

TimelineEntry = tuple[str, str, float]


def _starts_before_first_end(timeline: list[TimelineEntry]) -> int:
    for index, (_, phase, _) in enumerate(timeline):
        if phase == "end":
            return sum(1 for _, prior, _ in timeline[:index] if prior == "start")
    raise AssertionError("expected at least one 'end' event in timeline")


@pytest.mark.asyncio
async def test_parallel_effect_runs_awaitables_concurrently() -> None:
    """Test Parallel effect runs Programs concurrently.

    NOTE: Uses CESKInterpreter because Parallel is CESK-only.
    """
    engine = CESKInterpreter()
    delay = 0.01

    async def record_sleep(label: str, timeline: list[TimelineEntry]) -> str:
        loop = asyncio.get_running_loop()
        timeline.append((label, "start", loop.time()))
        await asyncio.sleep(delay)
        timeline.append((label, "end", loop.time()))
        return label

    @do
    def sequential_program(timeline: list[TimelineEntry]) -> EffectGenerator[list[str]]:
        first = yield Await(record_sleep("one", timeline))
        second = yield Await(record_sleep("two", timeline))
        return [first, second]

    @do
    def make_worker(label: str, timeline: list[TimelineEntry]) -> EffectGenerator[str]:
        result = yield Await(record_sleep(label, timeline))
        return result

    @do
    def parallel_program(timeline: list[TimelineEntry]) -> EffectGenerator[list[str]]:
        # Parallel requires Programs, not raw awaitables
        return (yield Parallel(
            make_worker("one", timeline),
            make_worker("two", timeline),
        ))

    timeline_seq: list[TimelineEntry] = []
    sequential_result = await engine.run_async(sequential_program(timeline_seq))
    assert sequential_result.is_ok
    assert sequential_result.value == ["one", "two"]
    assert _starts_before_first_end(timeline_seq) == 1

    timeline_parallel: list[TimelineEntry] = []
    parallel_result = await engine.run_async(parallel_program(timeline_parallel))
    assert parallel_result.is_ok
    assert parallel_result.value == ["one", "two"]
    assert _starts_before_first_end(timeline_parallel) == 2


@pytest.mark.asyncio
async def test_gather_runs_programs_concurrently() -> None:
    engine = ProgramInterpreter()
    delay = 0.01

    seq_timeline: list[TimelineEntry] = []
    gather_timeline: list[TimelineEntry] = []

    async def record_sleep(label: str, timeline: list[TimelineEntry]) -> str:
        loop = asyncio.get_running_loop()
        timeline.append((label, "start", loop.time()))
        await asyncio.sleep(delay)
        timeline.append((label, "end", loop.time()))
        return label

    @do
    def worker(label: str, timeline: list[TimelineEntry]) -> EffectGenerator[str]:
        return (yield Await(record_sleep(label, timeline)))

    @do
    def sequential_runner() -> EffectGenerator[list[str]]:
        programs = [worker("one", seq_timeline), worker("two", seq_timeline)]
        results: list[str] = []
        for program in programs:
            results.append((yield program))
        return results

    @do
    def gather_runner() -> EffectGenerator[list[str]]:
        programs = [worker("one", gather_timeline), worker("two", gather_timeline)]
        return (yield Gather(*programs))

    sequential_result = await engine.run_async(sequential_runner())
    assert sequential_result.is_ok
    assert sequential_result.value == ["one", "two"]
    assert _starts_before_first_end(seq_timeline) == 1

    gather_result = await engine.run_async(gather_runner())
    assert gather_result.is_ok
    assert gather_result.value == ["one", "two"]
    assert _starts_before_first_end(gather_timeline) == 2


@pytest.mark.asyncio
async def test_parallel_many_long_tasks_finishes_under_one_second() -> None:
    """Test Parallel with many Programs finishes quickly.

    NOTE: Uses CESKInterpreter because Parallel is CESK-only.
    """
    engine = CESKInterpreter()
    delay = 0.5
    task_count = 100

    async def sleep_and_return(index: int) -> int:
        await asyncio.sleep(delay)
        return index

    @do
    def make_worker(index: int) -> EffectGenerator[int]:
        result = yield Await(sleep_and_return(index))
        return result

    @do
    def run_parallel() -> EffectGenerator[list[int]]:
        programs = [make_worker(i) for i in range(task_count)]
        return (yield Parallel(*programs))

    start = time.perf_counter()
    result = await engine.run_async(run_parallel())
    duration = time.perf_counter() - start

    assert result.is_ok
    assert result.value == list(range(task_count))
    assert duration < 1.0, f"Parallel execution took {duration:.2f}s"


@pytest.mark.asyncio
async def test_gather_many_long_programs_finishes_under_one_second() -> None:
    engine = ProgramInterpreter()
    delay = 0.5
    program_count = 100

    async def sleep_and_return(index: int) -> int:
        await asyncio.sleep(delay)
        return index

    @do
    def worker(index: int) -> EffectGenerator[int]:
        return (yield Await(sleep_and_return(index)))

    @do
    def run_gather() -> EffectGenerator[list[int]]:
        programs = [worker(i) for i in range(program_count)]
        return (yield Gather(*programs))

    start = time.perf_counter()
    result = await engine.run_async(run_gather())
    duration = time.perf_counter() - start

    assert result.is_ok
    assert result.value == list(range(program_count))
    assert duration < 1.0, f"Gather execution took {duration:.2f}s"
