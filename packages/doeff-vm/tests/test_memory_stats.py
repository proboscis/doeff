"""Liveness diagnostics for the current doeff-vm bridge."""

import ast
from dataclasses import dataclass
from pathlib import Path

import doeff_vm
from doeff_core_effects import scheduled, vm_probe_handler

from doeff import Gather, ReadVmLiveCounts, ReadVmWorkCounts, Spawn, do, run


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


@dataclass(frozen=True)
class _LiveCounts:
    """The VM's live segments, continuations and IR streams."""

    segments: int
    continuations: int
    ir_streams: int


def _live() -> _LiveCounts:
    """Read the VM live counts via effect ReadVmLiveCounts (the reading run is compared after it ends, so every read includes the same amount)."""
    return _LiveCounts(*run(vm_probe_handler(ReadVmLiveCounts())))


def test_the_live_counts_effect_answers_three_ints() -> None:
    live = _live()

    assert isinstance(live.segments, int)
    assert isinstance(live.continuations, int)
    assert isinstance(live.ir_streams, int)


def test_vm_live_counts_return_to_baseline_after_pyvm_run() -> None:
    before = _live()
    vm = doeff_vm.PyVM()

    assert vm.run(doeff_vm.Pure(7)) == 7

    assert _live() == before
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
    before = _live()

    assert run(program) == [
        [f"0:{task_index}" for task_index in range(10)],
        [f"1:{task_index}" for task_index in range(10)],
    ]

    assert _live() == before


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

    before = _live()

    assert run(scenario()) == "aborted"

    assert _live() == before
    assert len(head_indices) == n_aborts
    assert max(head_indices) <= 8, (
        f"arena slots stranded: max head fiber index {max(head_indices)} "
        f"after {n_aborts} aborted dispatches (expected bounded slot reuse)"
    )


# -- Accumulated amount of work (#2670) --------------------------------
# Counts for judging a test's time budget by a number that does not fluctuate with machine load. Read via effect ReadVmWorkCounts (the extension's function is not among the public names).


@dataclass(frozen=True)
class Work:
    """One read of the accumulated counts (or the difference of two reads): steps and handler invocations."""

    steps: int
    handler_calls: int

    def since(self, earlier: "Work") -> "Work":
        return Work(self.steps - earlier.steps, self.handler_calls - earlier.handler_calls)


def _work() -> Work:
    """Read the accumulated counts via an effect (the reading run's own steps are included in every read, so in the difference of two reads they add a fixed number on top of the program's share)."""
    steps, handler_calls = run(vm_probe_handler(ReadVmWorkCounts()))
    return Work(steps, handler_calls)


def _handled_scenario():
    @do
    def scenario():
        values = ()
        for index in range(5):
            values = (*values, (yield SyntheticQuery(key=str(index))))
        return list(values)

    return doeff_vm.WithHandler(_synthetic_query_handler(), scenario())


def _work_of_one_run() -> Work:
    """Increase in the accumulated counts (steps, handler invocations) while running _handled_scenario once."""
    before = _work()
    assert run(_handled_scenario()) == ["0", "1", "2", "3", "4"]
    return _work().since(before)


def test_vm_work_counts_is_cumulative_and_not_a_public_name() -> None:
    """The accumulated counts never decrease; a program through a handler increases both the step count and the handler count; and they appear neither at the package top nor among the extension's public names."""
    from doeff_vm import doeff_vm as ext

    grown = _work_of_one_run()
    assert grown.steps > 0
    assert grown.handler_calls >= 5
    assert not hasattr(doeff_vm, "vm_work_counts")
    assert not hasattr(ext, "vm_work_counts")
    assert "_vm_work_counts" not in ext.__all__


def test_vm_work_counts_grow_by_the_same_amount_for_the_same_program() -> None:
    """The same program increases the counts by the same amount both times - a fixed number independent of machine load (unlike CPU seconds)."""
    first = _work_of_one_run()
    second = _work_of_one_run()
    assert first == second, (first, second)


def test_the_extension_stub_declares_only_functions_the_extension_has() -> None:
    """Every function named by the compiled module's type declaration (doeff_vm/doeff_vm.pyi) exists in the extension, those starting with _ are absent from the extension's __all__,
    and _vm_work_counts returns two ints."""
    from doeff_vm import doeff_vm as ext

    stub = ast.parse(Path(doeff_vm.__file__).with_name("doeff_vm.pyi").read_text(encoding="utf-8"))
    declared = {
        node.name: ast.unparse(node.returns)
        for node in stub.body
        if isinstance(node, ast.FunctionDef) and node.name != "__getattr__"
    }
    assert declared["_vm_work_counts"] == "tuple[int, int]"
    assert [name for name in declared if not hasattr(ext, name)] == []
    assert [name for name in declared if name.startswith("_") and name in ext.__all__] == []
    steps, calls = ext._vm_work_counts()
    assert (type(steps), type(calls)) == (int, int)
