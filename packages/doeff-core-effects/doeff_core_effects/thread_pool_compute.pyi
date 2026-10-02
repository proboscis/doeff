# doeff_hy.static_stub が作った型の宣言 — 手で直さない(元 = thread_pool_compute.hy・作り直し = python -m doeff_hy.static_stub --write <この .pyi の隣の .hy>)

from _typeshed import Incomplete
from doeff_hy.static_types import Handler as _Handler
from concurrent.futures import Executor as Executor
from concurrent.futures import Future as Future
from doeff import Program as Program
from doeff_vm import PyVM as PyVM
from doeff_core_effects.scheduler import CreateExternalPromise as CreateExternalPromise
from doeff_core_effects.scheduler import ExternalPromise as ExternalPromise
from doeff_core_effects.scheduler import Wait as Wait
from doeff_core_effects.compute_effects import Compute as Compute
from doeff_core_effects.inline_compute import computed as computed
from doeff import Pass as Pass
from doeff_vm import WithHandler as WithHandler

def settle(promise: ExternalPromise, future: Future) -> None:
    ...

def run_computed(program: Program) -> Incomplete:
    ...

def thread_pool_compute_handler(pool: Executor) -> _Handler:
    ...
