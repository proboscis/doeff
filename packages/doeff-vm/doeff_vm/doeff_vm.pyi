# compiled の拡張 module doeff_vm.doeff_vm(Rust・pyo3 — src/lib.rs)の型の宣言のうち、package の頭(doeff_vm/__init__.py)へ出さない口。
# 拡張だけに置く口は、古い build の .so で package の import を落とさないため、使い手が doeff_vm.doeff_vm から直に引く
# (src/lib.rs の gc_traverse_zeroed_visits の註)。拡張のほかの名はこの宣言では型を持たない(__getattr__)— 拡張に
# 宣言が無かった今までと同じ扱いで、名の全部を写すのはこの宣言の範囲の外(agora-redesign #2851)。
# 宣言した関数が拡張に在ることは packages/doeff-vm/tests/test_memory_stats.py が確かめる。
from _typeshed import Incomplete

def vm_work_counts() -> tuple[int, int]:
    """process の全部の VM の積み上げの数 (歩数, handler を呼んだ回数) — 減らない。2 つの読みの差がその間の仕事の量。"""
    ...

def set_scheduler_trace_installed(installed: bool) -> None:
    """Rust の版の scheduler の測りの口の旗(sink が据えてあるか)を切り替える — 呼び手は
    doeff_core_effects.scheduler.set_scheduler_trace だけ(src/scheduler.rs の TRACE_INSTALLED)。"""
    ...

def set_scheduler_step_budget_armed(armed: bool) -> None:
    """Rust の版の scheduler の歩数の上限の旗(上限が入っているか)を切り替える — 呼び手は
    doeff_core_effects.scheduler.arm_step_budget と disarm_step_budget だけ(src/scheduler.rs の STEP_BUDGET_ARMED)。"""
    ...

def __getattr__(name: str) -> Incomplete: ...
