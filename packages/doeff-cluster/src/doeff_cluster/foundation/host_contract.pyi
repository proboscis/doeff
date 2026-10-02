# doeff_hy.static_stub が作った型の宣言 — 手で直さない(元 = host_contract.hy・作り直し = python -m doeff_hy.static_stub --write <この .pyi の隣の .hy>)

from _typeshed import Incomplete
from doeff import Program as _Program
from doeff_hy.static_types import Handler as _Handler
from dataclasses import dataclass as dataclass
from collections.abc import Mapping as Mapping
from doeff_core_effects.effects import Ask as Ask
from doeff_core_effects.scheduler import Spawn as Spawn
from doeff_core_effects.scheduler import TaskCompleted as TaskCompleted
from doeff_core_effects.scheduler import Gather as Gather
from doeff_core_effects.scheduler import Wait as Wait
from doeff_core_effects.scheduler import Race as Race
from doeff_core_effects.scheduler import Cancel as Cancel
from doeff_core_effects.scheduler import CreatePromise as CreatePromise
from doeff_core_effects.scheduler import CompletePromise as CompletePromise
from doeff_core_effects.scheduler import FailPromise as FailPromise
from doeff_core_effects.scheduler import CreateExternalPromise as CreateExternalPromise
from doeff_core_effects.scheduler import CreateSemaphore as CreateSemaphore
from doeff_core_effects.scheduler import AcquireSemaphore as AcquireSemaphore
from doeff_core_effects.scheduler import ReleaseSemaphore as ReleaseSemaphore
from doeff_time import DelayEffect as DelayEffect
from doeff_time import GetTimeEffect as GetTimeEffect
from doeff_time import GetMonotonicEffect as GetMonotonicEffect
from doeff_time import WaitUntilEffect as WaitUntilEffect
from doeff_time import WaitWithinEffect as WaitWithinEffect
from doeff import Pass as Pass
from doeff_vm import WithHandler as WithHandler

@dataclass(frozen=True, kw_only=True)
class HostContract:
    run_context_key: str
    program_key: str
    versions_key: str
    program_env: str
HOST_CONTRACT: HostContract
SIM_PASSABLE: Incomplete

def this_program_path() -> _Program[str, object]:
    ...

def environ_reader(environ: Mapping[str, str]=...) -> _Handler:
    ...
