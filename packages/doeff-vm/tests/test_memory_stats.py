"""Liveness diagnostics for the current doeff-vm bridge."""

import doeff_vm
from doeff_core_effects.scheduler import scheduled

from doeff import Gather, Spawn, do, run


class SyntheticQuery(doeff_vm.EffectBase):
    def __init__(self, key: str) -> None:
        self.key = key


def _synthetic_query_handler():
    @do
    def handler(effect, k):
        if isinstance(effect, SyntheticQuery):
            return (yield doeff_vm.Resume(k, effect.key))
        yield doeff_vm.Pass(effect, k)

    # Direct VM node test: doeff_vm.WithHandler expects the raw dispatcher.
    return handler


def test_vm_live_counts_exported_with_expected_shape() -> None:
    live_segments, live_continuations, live_ir_streams = doeff_vm.vm_live_counts()

    assert isinstance(live_segments, int)
    assert isinstance(live_continuations, int)
    assert isinstance(live_ir_streams, int)


def test_vm_live_counts_return_to_baseline_after_pyvm_run() -> None:
    before = doeff_vm.vm_live_counts()
    vm = doeff_vm.PyVM()

    assert vm.run(doeff_vm.Pure(7)) == 7

    assert doeff_vm.vm_live_counts() == before
    assert vm.arena_stats() == (0, 0, 0, 0)


def test_vm_live_counts_return_to_baseline_after_scheduled_handler_chain() -> None:
    @do
    def worker(batch_index: int, task_index: int):
        return (yield SyntheticQuery(key=f"{batch_index}:{task_index}"))

    @do
    def scenario():
        batches: list[list[str]] = []
        for batch_index in range(2):
            tasks = []
            for task_index in range(10):
                tasks.append((yield Spawn(worker(batch_index, task_index))))
            batches.append(list((yield Gather(*tasks))))
        return batches

    program = scheduled(doeff_vm.WithHandler(_synthetic_query_handler(), scenario()))
    before = doeff_vm.vm_live_counts()

    assert run(program) == [
        [f"0:{task_index}" for task_index in range(10)],
        [f"1:{task_index}" for task_index in range(10)],
    ]

    assert doeff_vm.vm_live_counts() == before


def test_arena_slots_reclaimed_when_handler_abandons_continuation() -> None:
    """#497: a handler that never resumes k (abort-style) drops the detached
    chain; the chain's arena slots must return to the free list within the
    run. Before reclamation every abort stranded ~2 vacant-reserved slots,
    so head fiber indices grew ~2 per abort (max index ~2*N); with
    reclamation the same few slots are reused and indices stay bounded.
    """
    n_aborts = 200
    head_indices: list[int] = []

    @do
    def abort_handler(effect, k):
        if isinstance(effect, SyntheticQuery):
            head_indices.append(k.to_dict()["head"])
            return "aborted"
        yield doeff_vm.Pass(effect, k)

    @do
    def body():
        yield SyntheticQuery(key="x")
        return "unreachable"

    @do
    def scenario():
        result = None
        for _ in range(n_aborts):
            result = yield doeff_vm.WithHandler(abort_handler, body())
        return result

    before = doeff_vm.vm_live_counts()

    assert run(scenario()) == "aborted"

    assert doeff_vm.vm_live_counts() == before
    assert len(head_indices) == n_aborts
    assert max(head_indices) <= 8, (
        f"arena slots stranded: max head fiber index {max(head_indices)} "
        f"after {n_aborts} aborted dispatches (expected bounded slot reuse)"
    )
