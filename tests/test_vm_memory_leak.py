from __future__ import annotations

import gc
import resource
import weakref
from dataclasses import dataclass

import doeff_vm

from doeff import Pass, default_handlers, do, run
from doeff.effects.base import EffectBase
from doeff.rust_vm import WithHandler


@dataclass(frozen=True)
class BigDataEffect(EffectBase):
    iteration: int


@dataclass(frozen=True)
class TinyEffect(EffectBase):
    iteration: int


class CountAlivePayloads(EffectBase):
    pass


class WeakPayload:
    __slots__ = ("__weakref__", "payload")

    def __init__(self, payload: list[int]) -> None:
        self.payload = payload


@do
def big_data_handler(effect: EffectBase, k):
    if isinstance(effect, BigDataEffect):
        big_payload = list(range(100_000))
        yield doeff_vm.Transfer(k, big_payload)
    if isinstance(effect, TinyEffect):
        yield doeff_vm.Transfer(k, effect.iteration)
    yield Pass()


@do
def _sequential_yield_discard(n: int):
    for i in range(n):
        _unused = yield BigDataEffect(iteration=i)
        del _unused
    return n


@do
def _sequential_yield_keep_last(n: int):
    last = None
    for i in range(n):
        last = yield BigDataEffect(iteration=i)
    return last


@do
def _tiny_loop(n: int):
    for i in range(n):
        _ = yield TinyEffect(iteration=i)
        del _
    return n


def _rss_mb() -> float:
    usage = resource.getrusage(resource.RUSAGE_SELF)
    return usage.ru_maxrss / 1024


def _run_program(program):
    return run(WithHandler(big_data_handler, program), handlers=default_handlers())


N_ITERATIONS = 200
MAX_ALLOWED_GROWTH_MB = 50


def test_tail_transfer_releases_large_payloads_during_run() -> None:
    # Transfer is the explicit tail-position protocol: the handler is abandoned
    # immediately instead of staying suspended on the remainder continuation.
    payload_refs: list[weakref.ReferenceType[WeakPayload]] = []

    @do
    def handler(effect: EffectBase, k):
        if isinstance(effect, BigDataEffect):
            payload = WeakPayload(list(range(100_000)))
            payload_refs.append(weakref.ref(payload))
            yield doeff_vm.Transfer(k, payload)
        if isinstance(effect, CountAlivePayloads):
            alive = sum(ref() is not None for ref in payload_refs)
            yield doeff_vm.Transfer(k, alive)
        yield Pass()

    @do
    def program():
        samples: list[int] = []
        for i in range(40):
            payload = yield BigDataEffect(iteration=i)
            del payload
            if (i + 1) % 10 == 0:
                samples.append((yield CountAlivePayloads()))
        return samples

    result = run(WithHandler(handler, program()), handlers=default_handlers())

    assert result.is_ok(), f"Program failed: {result.error}"
    assert result.value == [1, 1, 1, 1]
    assert max(result.value) <= 1


def test_sequential_discard_bounded_memory() -> None:
    gc.collect()
    rss_before = _rss_mb()

    result = _run_program(_sequential_yield_discard(N_ITERATIONS))

    gc.collect()
    rss_after = _rss_mb()
    delta = rss_after - rss_before

    assert result.is_ok(), f"Program failed: {result.error}"
    assert result.value == N_ITERATIONS
    assert delta < MAX_ALLOWED_GROWTH_MB, (
        f"Memory leak detected! RSS grew by {delta:.0f} MB "
        f"for {N_ITERATIONS} iterations yielding ~800KB each "
        f"(expected <{MAX_ALLOWED_GROWTH_MB} MB)."
    )


def test_sequential_keep_last_bounded_memory() -> None:
    gc.collect()
    rss_before = _rss_mb()

    result = _run_program(_sequential_yield_keep_last(N_ITERATIONS))

    gc.collect()
    rss_after = _rss_mb()
    delta = rss_after - rss_before

    assert result.is_ok(), f"Program failed: {result.error}"
    assert isinstance(result.value, list)
    assert len(result.value) == 100_000
    assert delta < MAX_ALLOWED_GROWTH_MB, (
        f"Memory leak detected! RSS grew by {delta:.0f} MB "
        f"for {N_ITERATIONS} iterations yielding ~800KB each "
        f"(expected <{MAX_ALLOWED_GROWTH_MB} MB)."
    )


def test_control_small_effects_low_memory() -> None:
    gc.collect()
    rss_before = _rss_mb()

    result = _run_program(_tiny_loop(N_ITERATIONS))

    gc.collect()
    rss_after = _rss_mb()
    delta = rss_after - rss_before

    assert result.is_ok(), f"Program failed: {result.error}"
    assert result.value == N_ITERATIONS
    assert delta < 20, f"Even tiny effects leaked {delta:.0f} MB."
