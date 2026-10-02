"""Liveness diagnostics for the current doeff-vm bridge."""

import ast
from dataclasses import dataclass
from pathlib import Path

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


# ── 積み上げの仕事の量(agora-redesign #2851・#2670)────────────────────────────────
# 検の時間の予算を、機体の負荷で揺れない数で判じるための口。compiled の module から読む(__init__ へは足さない)。


@dataclass(frozen=True)
class Work:
    """積み上げの数の 1 回の読み(か 2 つの読みの差): 歩数と handler を呼んだ回数。"""

    steps: int
    handler_calls: int

    def since(self, earlier: "Work") -> "Work":
        return Work(self.steps - earlier.steps, self.handler_calls - earlier.handler_calls)


def _work() -> Work:
    from doeff_vm import doeff_vm as ext

    steps, handler_calls = ext.vm_work_counts()
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
    """_handled_scenario を 1 回走らせた間の、積み上げの数の増え(歩数・handler の回数)。"""
    before = _work()
    assert run(_handled_scenario()) == ["0", "1", "2", "3", "4"]
    return _work().since(before)


def test_vm_work_counts_is_cumulative_and_not_reexported() -> None:
    """積み上げの数は減らない・handler を通る program で歩数も handler の回数も増える・package の頭へは出さない。"""
    grown = _work_of_one_run()
    assert grown.steps > 0
    assert grown.handler_calls >= 5
    assert not hasattr(doeff_vm, "vm_work_counts")


def test_vm_work_counts_grow_by_the_same_amount_for_the_same_program() -> None:
    """同じ program は 2 回とも同じだけ数を増やす — 機体の負荷に依らない決まった数であること(CPU 秒と違う)。"""
    first = _work_of_one_run()
    second = _work_of_one_run()
    assert first == second, (first, second)


def test_the_extension_stub_declares_only_functions_the_extension_has() -> None:
    """compiled の module の型の宣言(doeff_vm/doeff_vm.pyi)が名乗る関数は、拡張に在り、vm_work_counts は int 2 つを返す。"""
    from doeff_vm import doeff_vm as ext

    stub = ast.parse(Path(doeff_vm.__file__).with_name("doeff_vm.pyi").read_text(encoding="utf-8"))
    declared = {
        node.name: ast.unparse(node.returns)
        for node in stub.body
        if isinstance(node, ast.FunctionDef) and node.name != "__getattr__"
    }
    assert declared["vm_work_counts"] == "tuple[int, int]"
    assert [name for name in declared if not hasattr(ext, name)] == []
    steps, calls = ext.vm_work_counts()
    assert (type(steps), type(calls)) == (int, int)
