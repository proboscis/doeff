"""
CESK interpreter tests for Parallel effect (ProgramParallelEffect).

NOTE: These tests are CESK-only because ProgramParallelEffect is not
supported by ProgramInterpreter. For interpreter-agnostic tests, see
test_gather.py and other parameterized test files.
"""

import asyncio
import time

import pytest

from doeff import Await, Gather, Get, Log, Put, do, EffectGenerator
from doeff.cesk_adapter import CESKInterpreter
from doeff.cesk import Parallel

TimelineEntry = tuple[str, str, float]


def _starts_before_first_end(timeline: list[TimelineEntry]) -> int:
    """Count how many tasks started before the first one ended."""
    for index, (_, phase, _) in enumerate(timeline):
        if phase == "end":
            return sum(1 for _, prior, _ in timeline[:index] if prior == "start")
    raise AssertionError("expected at least one 'end' event in timeline")


@pytest.mark.asyncio
async def test_parallel_programs_run_concurrently() -> None:
    """Test Parallel effect runs Programs concurrently."""
    engine = CESKInterpreter()
    delay = 0.01
    timeline: list[TimelineEntry] = []

    async def record_sleep(label: str) -> str:
        loop = asyncio.get_running_loop()
        timeline.append((label, "start", loop.time()))
        await asyncio.sleep(delay)
        timeline.append((label, "end", loop.time()))
        return label

    @do
    def worker(label: str) -> EffectGenerator[str]:
        result = yield Await(record_sleep(label))
        yield Log(f"Worker {label} done")
        return result

    @do
    def parallel_program() -> EffectGenerator[list[str]]:
        # Parallel runs Programs concurrently
        results = yield Parallel(worker("one"), worker("two"))
        yield Log(f"All workers done: {results}")
        return results

    result = await engine.run_async(parallel_program())

    assert result.is_ok
    assert sorted(result.value) == ["one", "two"]
    # Both should start before first one ends (concurrent execution)
    assert _starts_before_first_end(timeline) == 2


@pytest.mark.asyncio
async def test_parallel_state_merging() -> None:
    """Test Parallel merges state from all children in program order."""
    engine = CESKInterpreter()

    @do
    def worker(name: str, value: int) -> EffectGenerator[int]:
        yield Put(f"worker_{name}", value)
        yield Log(f"Worker {name} set value {value}")
        return value

    @do
    def parallel_program() -> EffectGenerator[tuple[list[int], dict]]:
        yield Put("before", "yes")
        results = yield Parallel(
            worker("a", 10),
            worker("b", 20),
            worker("c", 30),
        )
        # Check state after parallel merge
        a_val = yield Get("worker_a")
        b_val = yield Get("worker_b")
        c_val = yield Get("worker_c")
        return results, {"a": a_val, "b": b_val, "c": c_val}

    result = await engine.run_async(parallel_program())

    assert result.is_ok
    values, state_check = result.value
    assert values == [10, 20, 30]
    # All worker states should be merged
    assert state_check == {"a": 10, "b": 20, "c": 30}
    assert result.state["before"] == "yes"


@pytest.mark.asyncio
async def test_parallel_log_merging() -> None:
    """Test Parallel merges logs from all children in program order."""
    engine = CESKInterpreter()
    completion_order: list[str] = []
    b_started = asyncio.Event()

    async def record_completion(name: str) -> None:
        completion_order.append(name)

    async def signal_b_started() -> None:
        b_started.set()

    async def wait_for_b() -> None:
        # A waits for B to start, ensuring B runs first
        await b_started.wait()
        await asyncio.sleep(0.01)  # Let B complete first

    @do
    def worker_a() -> EffectGenerator[str]:
        yield Log("Start A")
        # A waits for B to ensure B completes first
        yield Await(wait_for_b())
        yield Log("End A")
        yield Await(record_completion("A"))
        return "A"

    @do
    def worker_b() -> EffectGenerator[str]:
        yield Log("Start B")
        yield Await(signal_b_started())  # Signal that B has started
        yield Log("End B")
        yield Await(record_completion("B"))
        return "B"

    @do
    def parallel_program() -> EffectGenerator[list[str]]:
        yield Log("Before parallel")
        # B completes first, but A's logs should still come first in merged output
        results = yield Parallel(worker_a(), worker_b())
        yield Log("After parallel")
        return results

    result = await engine.run_async(parallel_program())

    assert result.is_ok
    assert result.value == ["A", "B"]
    # Verify B completed before A (deterministic via event coordination)
    assert completion_order == ["B", "A"], f"Expected B to complete first: {completion_order}"
    # Logs should be: before, A logs, B logs (in program order), after
    assert result.log[0] == "Before parallel"
    assert result.log[-1] == "After parallel"
    # Verify program order: all A logs come before all B logs
    # Even though B completed first
    a_start_idx = result.log.index("Start A")
    a_end_idx = result.log.index("End A")
    b_start_idx = result.log.index("Start B")
    b_end_idx = result.log.index("End B")
    # A logs are contiguous and come before B logs (program order, not completion order)
    assert a_start_idx < a_end_idx < b_start_idx < b_end_idx


@pytest.mark.asyncio
async def test_parallel_empty() -> None:
    """Test Parallel with no programs returns empty list."""
    engine = CESKInterpreter()

    @do
    def empty_parallel() -> EffectGenerator[list]:
        results = yield Parallel()
        return results

    result = await engine.run_async(empty_parallel())

    assert result.is_ok
    assert result.value == []


@pytest.mark.asyncio
async def test_parallel_single() -> None:
    """Test Parallel with single program."""
    engine = CESKInterpreter()

    @do
    def single_worker() -> EffectGenerator[int]:
        yield Log("Single worker")
        return 42

    @do
    def single_parallel() -> EffectGenerator[list[int]]:
        results = yield Parallel(single_worker())
        return results

    result = await engine.run_async(single_parallel())

    assert result.is_ok
    assert result.value == [42]
    assert "Single worker" in result.log


@pytest.mark.asyncio
async def test_parallel_error_first_wins() -> None:
    """Test Parallel: first error (in program order) is raised."""
    engine = CESKInterpreter()
    branches_completed: set[str] = set()
    second_started = asyncio.Event()

    async def mark_completed(name: str) -> None:
        branches_completed.add(name)

    async def signal_second_started() -> None:
        second_started.set()

    async def wait_for_second() -> None:
        # First waits for second to start, ensuring second fails first
        await second_started.wait()
        await asyncio.sleep(0.01)  # Let second complete first

    @do
    def good_worker(name: str) -> EffectGenerator[str]:
        yield Log(f"Good {name}")
        yield Await(mark_completed(f"good_{name}"))
        return name

    @do
    def bad_worker_first() -> EffectGenerator[str]:
        # Coordinate to ensure this fails AFTER bad_worker_second
        yield Await(wait_for_second())
        yield Log("First bad worker")
        yield Await(mark_completed("bad_first"))  # Completed before raise
        raise ValueError("First failure")

    @do
    def bad_worker_second() -> EffectGenerator[str]:
        yield Await(signal_second_started())  # Signal that second has started
        yield Log("Second bad worker")
        yield Await(mark_completed("bad_second"))  # Completed before raise
        raise ValueError("Second failure")

    @do
    def parallel_with_errors() -> EffectGenerator[list[str]]:
        # Multiple failures - first in program order should win
        # Even though bad_worker_second completes first
        results = yield Parallel(
            good_worker("A"),
            bad_worker_first(),   # First failure (program order index 1)
            bad_worker_second(),  # Completes first but second in program order
            good_worker("D"),
        )
        return results

    result = await engine.run_async(parallel_with_errors())

    assert result.is_err
    # Verify both failing branches completed (reached Log before raise)
    assert "bad_first" in branches_completed
    assert "bad_second" in branches_completed
    # Verify non-failing branches also ran
    assert "good_A" in branches_completed
    assert "good_D" in branches_completed
    # First error in PROGRAM ORDER should be returned (not completion order)
    # bad_worker_second completes first but bad_worker_first
    # comes earlier in program order, so its error should be returned
    assert isinstance(result.error, ValueError)
    assert result.error.args[0] == "First failure"


@pytest.mark.asyncio
async def test_parallel_many_programs_performance() -> None:
    """Test Parallel with many programs finishes quickly."""
    engine = CESKInterpreter()
    program_count = 50
    delay = 0.02

    async def slow_operation(index: int) -> int:
        await asyncio.sleep(delay)
        return index

    @do
    def worker(index: int) -> EffectGenerator[int]:
        result = yield Await(slow_operation(index))
        return result

    @do
    def many_parallel() -> EffectGenerator[list[int]]:
        programs = [worker(i) for i in range(program_count)]
        results = yield Parallel(*programs)
        return results

    start = time.perf_counter()
    result = await engine.run_async(many_parallel())
    duration = time.perf_counter() - start

    assert result.is_ok
    assert len(result.value) == program_count
    assert sorted(result.value) == list(range(program_count))
    # Sequential would be 50 * 0.02 = 1.0s; parallel should be ~0.02s + overhead
    # Use generous threshold (0.8s) to avoid CI flakiness while still proving parallelism
    sequential_time = program_count * delay
    assert duration < sequential_time * 0.8, (
        f"Parallel took {duration:.2f}s, expected < {sequential_time * 0.8:.2f}s "
        f"(sequential would be {sequential_time:.2f}s)"
    )


@pytest.mark.asyncio
async def test_gather_vs_parallel_semantics() -> None:
    """Compare Gather (sequential in CESK) vs Parallel (concurrent)."""
    engine = CESKInterpreter()

    @do
    def counter_worker() -> EffectGenerator[int]:
        count = yield Get("counter")
        count = (count or 0) + 1
        yield Put("counter", count)
        return count

    @do
    def gather_program() -> EffectGenerator[list[int]]:
        yield Put("counter", 0)
        # Gather runs sequentially - each sees previous state
        results = yield Gather(counter_worker(), counter_worker(), counter_worker())
        return results

    @do
    def parallel_program() -> EffectGenerator[list[int]]:
        yield Put("counter", 0)
        # Parallel runs concurrently - each starts with same state
        results = yield Parallel(counter_worker(), counter_worker(), counter_worker())
        return results

    gather_result = await engine.run_async(gather_program())
    parallel_result = await engine.run_async(parallel_program())

    assert gather_result.is_ok
    assert parallel_result.is_ok

    # Gather: sequential, so [1, 2, 3]
    assert gather_result.value == [1, 2, 3]
    # Parallel: concurrent, all start with counter=0, so [1, 1, 1]
    assert parallel_result.value == [1, 1, 1]


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
