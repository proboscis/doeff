import asyncio
import time

import pytest

from doeff import Await, Gather, ProgramInterpreter, do, EffectGenerator

TimelineEntry = tuple[str, str, float]


def _starts_before_first_end(timeline: list[TimelineEntry]) -> int:
    for index, (_, phase, _) in enumerate(timeline):
        if phase == "end":
            return sum(1 for _, prior, _ in timeline[:index] if prior == "start")
    raise AssertionError("expected at least one 'end' event in timeline")


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
