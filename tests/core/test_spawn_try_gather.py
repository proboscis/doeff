"""
Regression: Spawn(Try(Program)) inside throttled_gather with WithHandler.

Pattern from real pipeline:
  programs = [Try(compute(key)) for key in keys]
  results = yield throttled_gather(*programs, concurrency=N)

Each program is wrapped: Spawn(wrap_sem(Try(compute(...)), sem)).
The Try produces Ok/Err results. When Spawned tasks return Ok(...),
the VM rejects it as a non-Effect yielded value.
"""
from dataclasses import dataclass
from typing import Any

from doeff import (
    Ask,
    CreateSemaphore,
    AcquireSemaphore,
    ReleaseSemaphore,
    EffectGenerator,
    Gather,
    Local,
    Pass,
    Resume,
    Spawn,
    Try,
    WithHandler,
    default_handlers,
    do,
    run,
)
from doeff.effects.base import Effect, EffectBase


@dataclass(frozen=True)
class FetchEffect(EffectBase):
    key: str


@do
def handler(effect: Effect, k):
    if not isinstance(effect, FetchEffect):
        yield Pass()
        return
    return (yield Resume(k, f"data-{effect.key}"))


@do
def fetch(key: str) -> EffectGenerator[str]:
    return (yield FetchEffect(key=key))


@do
def compute(key: str) -> EffectGenerator[str]:
    """Inner @do: yield Try(fetch) then extract value."""
    df_result = yield Try(fetch(key))
    if df_result.is_err():
        raise RuntimeError(f"failed: {df_result.error}")
    return df_result.value


def wrap_sem(p, sem):
    @do
    def _w() -> EffectGenerator[Any]:
        yield AcquireSemaphore(sem)
        r = yield p
        yield ReleaseSemaphore(sem)
        return r
    return _w()


@do
def throttled_gather(
    *programs: Any, concurrency: int
) -> EffectGenerator[list]:
    """Wrapper: CreateSemaphore + wrap each program + Spawn + Gather."""
    sem = yield CreateSemaphore(concurrency)
    wrapped = [wrap_sem(p, sem) for p in programs]
    tasks = []
    for w in wrapped:
        t = yield Spawn(w, daemon=False)
        tasks.append(t)
    return list((yield Gather(*tasks)))


# --- Tests ---


def test_spawn_try_compute_with_handler():
    """Spawn(wrap_sem(Try(compute(key)))) inside throttled_gather + WithHandler."""

    @do
    def test_program() -> EffectGenerator[list]:
        programs = [Try(compute(f"k{i}")) for i in range(3)]
        return (yield throttled_gather(*programs, concurrency=2))

    wrapped = WithHandler(handler, test_program())
    result = run(wrapped, handlers=default_handlers())
    assert result.is_ok(), f"Failed: {result.error}"
    assert len(result.value) == 3


def test_spawn_compute_without_try():
    """Same but without Try wrapping — should work."""

    @do
    def test_program() -> EffectGenerator[list]:
        programs = [compute(f"k{i}") for i in range(3)]
        return (yield throttled_gather(*programs, concurrency=2))

    wrapped = WithHandler(handler, test_program())
    result = run(wrapped, handlers=default_handlers())
    assert result.is_ok(), f"Failed: {result.error}"
    assert len(result.value) == 3


def test_sequential_try_compute_with_handler():
    """Try(compute) without Spawn — should work."""

    @do
    def test_program() -> EffectGenerator[list]:
        results = []
        for i in range(3):
            r = yield Try(compute(f"k{i}"))
            results.append(r)
        return results

    wrapped = WithHandler(handler, test_program())
    result = run(wrapped, handlers=default_handlers())
    assert result.is_ok(), f"Failed: {result.error}"
    assert len(result.value) == 3
