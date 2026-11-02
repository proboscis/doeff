# doeff_hy._static_stub が作った型の宣言 — 手で直さない(元 = child_processes.hy・作り直し = python -m doeff_hy._static_stub --write <この .pyi の隣の .hy>)

from doeff import Program as _Program
from doeff_hy._static_types import Handler as _Handler
from doeff import EffectBase as _doeff_effect_base
from dataclasses import dataclass as _doeff_dataclass
from dataclasses import dataclass as dataclass
from dataclasses import replace as replace
from doeff import with_handlers as with_handlers
from doeff_core_effects import CreatePromise as CreatePromise
from doeff_core_effects import CompletePromise as CompletePromise
from doeff_core_effects import Wait as Wait
from doeff_core_effects import Spawn as Spawn
from doeff_core_effects import Task as Task
from doeff_core_effects import Future as Future
from doeff_core_effects import Promise as Promise
from doeff_core_effects import AppendText as AppendText
from doeff_core_effects import FileFailed as FileFailed
from doeff_core_effects import ScriptedCommand as ScriptedCommand
from doeff_core_effects import ProcessOutcome as ProcessOutcome
from doeff_core_effects import RunProcess as RunProcess
from doeff_core_effects import StartProcess as StartProcess
from doeff_core_effects import PollProcess as PollProcess
from doeff_core_effects import StopProcess as StopProcess
from doeff_core_effects import SignalProcess as SignalProcess
from doeff_core_effects import ProcessSignal as ProcessSignal
from doeff_core_effects import ProcessStarted as ProcessStarted
from doeff_core_effects import ProcessNotStarted as ProcessNotStarted
from doeff_core_effects import ProcessRunning as ProcessRunning
from doeff_core_effects import ProcessExited as ProcessExited
from doeff_core_effects import ProcessNotChild as ProcessNotChild
from doeff_core_effects import ProcessSignalled as ProcessSignalled
from doeff_core_effects import ChildLifetime as ChildLifetime
from doeff_core_effects import start_refusal as start_refusal
REAPED_CODE: int
TERMINATED_CODE: int
DETACHING_COMMAND: str

@dataclass(frozen=True, kw_only=True)
class SimChildren:
    commands: tuple[ScriptedCommand, ...]
    adoption: bool = True

@dataclass(frozen=True, kw_only=True)
class ChildOrigin:
    root: int
    job: str
    worker: str
    boot: str

@dataclass(frozen=True, kw_only=True)
class SimDescendant:
    pid: int
    origin: ChildOrigin
    parent: int
    argv: tuple[str, ...]
    escaped: bool
    started_ms: int
    ended_ms: int | None = None
    exit_code: int | None = None
    reaped: bool = False
    collected: bool = False

@dataclass(frozen=True, kw_only=True)
class ReapedDescendants:
    descendants: dict
    tasks: dict
    discarded: tuple[Task, ...]

@_doeff_dataclass(frozen=True)
class KeepDescendant(_doeff_effect_base[int]):
    origin: ChildOrigin
    parent: int
    argv: tuple[str, ...]
    escaped: bool
    task: Task

@_doeff_dataclass(frozen=True)
class DescendantEnded(_doeff_effect_base[None]):
    pid: int
    code: int

@_doeff_dataclass(frozen=True)
class DescendantOf(_doeff_effect_base[SimDescendant | None]):
    pid: int

@_doeff_dataclass(frozen=True)
class SignalDescendant(_doeff_effect_base[bool]):
    pid: int
    code: int

@_doeff_dataclass(frozen=True)
class CollectDescendant(_doeff_effect_base[None]):
    pid: int

@_doeff_dataclass(frozen=True)
class DescendantsOf(_doeff_effect_base[tuple]):
    name: str
CHILD_PROCESS_EFFECTS: tuple[type, ...]

@dataclass(frozen=True, kw_only=True)
class ChildArgv:
    argv: tuple[str, ...]
    detached: bool

def detached_argv(argv: tuple[str, ...]) -> _Program[ChildArgv, object]:
    ...

def hyx_descendant_reapedXquestion_markX(descendant: SimDescendant, ended_roots: frozenset, generation: tuple | None, adoption: bool) -> _Program[bool, object]:
    ...

def descendants_reaped(descendants: dict, tasks: dict, ended_roots: frozenset, generation: tuple | None, adoption: bool, now: int) -> _Program[ReapedDescendants, object]:
    ...

def descendant_ended(descendants: dict, tasks: dict, pid: int, code: int, now: int) -> _Program[ReapedDescendants, object]:
    ...

def outputs_opened(paths: tuple) -> _Program[ProcessNotStarted | None, object]:
    ...

def outputs_appended(stdout_path: str | None, stderr_path: str | None, outcome: ProcessOutcome) -> _Program[None, object]:
    ...

def child_life(origin: ChildOrigin, escaped: bool, children: SimChildren, command: ScriptedCommand, request: RunProcess, stdout_path: str | None, stderr_path: str | None, gate: Future) -> _Program[None, object]:
    ...

def child_started(origin: ChildOrigin, parent: int, escaped: bool, children: SimChildren, request: StartProcess) -> _Program[ProcessStarted | ProcessNotStarted, object]:
    ...

def own_child(parent: int, pid: int) -> _Program[SimDescendant | None, object]:
    ...

def child_processes(origin: ChildOrigin, parent: int, escaped: bool, children: SimChildren) -> _Handler:
    ...
