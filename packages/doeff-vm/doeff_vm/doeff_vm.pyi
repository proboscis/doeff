# compiled の拡張 module doeff_vm.doeff_vm(Rust・pyo3 — src/lib.rs)の型の宣言のうち、package の頭(doeff_vm/__init__.py)へ出さない口。
# 拡張だけに置く口は、古い build の .so で package の import を落とさないため、使い手が doeff_vm.doeff_vm から直に引く
# (src/lib.rs の gc_traverse_zeroed_visits の註)。拡張のほかの名はこの宣言では型を持たない(__getattr__)— 拡張に
# 宣言が無かった今までと同じ扱いで、名の全部を写すのはこの宣言の範囲の外(agora-redesign #2851)。
# 宣言した関数が拡張に在ることは packages/doeff-vm/tests/test_memory_stats.py が確かめる。
#
# _ で始まる関数は VM の数と切り替えで、公開の名ではない(拡張の __all__ に載らない — src/lib.rs の bind_internal)。
# 利用者は doeff の effect(ReadVmWorkCounts・SetInvariantChecks など)を出し、doeff_core_effects._handlers.vm_probe_handler が
# これらを呼んで答える。ほかに呼ぶのは doeff_core_effects の scheduler と world_phases だけ(agora card ki-27e8ea225b62)。
from _typeshed import Incomplete

def _vm_live_counts() -> tuple[int, int, int]:
    """VM の中で生きている物の数 (segment, 続き, IR の stream) — run が終われば元の数へ戻る(effect ReadVmLiveCounts)。"""
    ...

def _vm_work_counts() -> tuple[int, int]:
    """process の全部の VM の積み上げの数 (歩数, handler を呼んだ回数) — 減らない。2 つの読みの差がその間の仕事の量
    (effect ReadVmWorkCounts)。"""
    ...

def _vm_scan_counts() -> dict[str, int]:
    """Rust の版の scheduler の走査が見た entry の数を、見た物ごとに積み上げた表 — 減らない(effect ReadVmScanCounts)。"""
    ...

def _invariant_checks_enabled() -> bool:
    """VM の 1 歩ごとの不変条件の検査が、この process で入っているか(effect ReadInvariantChecks)。"""
    ...

def _set_invariant_checks(on: bool) -> None:
    """VM の 1 歩ごとの不変条件の検査を、この process で入れる・切る(effect SetInvariantChecks)。"""
    ...

def _set_scheduler_trace_installed(installed: bool) -> None:
    """Rust の版の scheduler の測りの口の旗(sink が据えてあるか)を切り替える — 呼び手は
    doeff_core_effects._scheduler.set_scheduler_trace と effect SetSchedulerTraceInstalled の handler だけ
    (src/scheduler.rs の TRACE_INSTALLED)。"""
    ...

def _set_scheduler_step_budget_armed(armed: bool) -> None:
    """Rust の版の scheduler の歩数の上限の旗(上限が入っているか)を切り替える — 呼び手は
    doeff_core_effects._scheduler.arm_step_budget と disarm_step_budget と effect SetSchedulerStepBudgetArmed の
    handler だけ(src/scheduler.rs の STEP_BUDGET_ARMED)。"""
    ...

class StateHandler:
    """state の答え手(Rust の handler — src/state_handler.rs)。Get / Put(2 つの class の instance)に store から VM の中で答える。
    作り手は doeff_core_effects._handlers.state だけ。VM が呼ぶ物で、Python から呼ぶと TypeError。"""

    def __init__(self, store: dict[object, object], get_type: type, put_type: type, /) -> None: ...
    @property
    def effect_types(self) -> tuple[type, type]: ...
    @property
    def __name__(self) -> str: ...
    @property
    def __qualname__(self) -> str: ...  # pyright: ignore[reportIncompatibleVariableOverride] — 型の __qualname__ でなく、handler の名を答える instance の口
    def __call__(self, *args: object, **kwargs: object) -> None: ...

def __getattr__(name: str) -> Incomplete: ...
