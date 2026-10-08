# doeff_hy.static_stub が作った型の宣言 — 手で直さない(元 = scheduler_step_tally.hy・作り直し = python -m doeff_hy.static_stub --write <この .pyi の隣の .hy>)

from doeff import Program as _Program
from doeff_hy.static_types import Handler as _Handler
from dataclasses import dataclass as dataclass
import threading as threading
from doeff_core_effects.scheduler import scheduler_trace_sink as scheduler_trace_sink
from doeff_core_effects.scheduler import set_scheduler_trace as set_scheduler_trace
from doeff_core_effects.step_tally_effects import CloseStepTally as CloseStepTally
from doeff_core_effects.step_tally_effects import EMPTY_STEP_TALLY as EMPTY_STEP_TALLY
from doeff_core_effects.step_tally_effects import OpenStepTally as OpenStepTally
from doeff_core_effects.step_tally_effects import StepTally as StepTally
from doeff_core_effects.step_tally_effects import CloseTaskTally as CloseTaskTally
from doeff_core_effects.step_tally_effects import OpenTaskTally as OpenTaskTally
from doeff_core_effects.step_tally_effects import ReadTaskTally as ReadTaskTally
from doeff_core_effects.step_tally_effects import TaskTally as TaskTally
from doeff import Pass as Pass
from doeff_vm import WithHandler as WithHandler

@dataclass(frozen=True, kw_only=True)
class StepWindow:
    thread: str
    opened_ns: int
    tally: StepTally

@dataclass(frozen=True, kw_only=True)
class TaskWindow:
    opened_ns: int
    rows: dict[tuple, TaskTally]
WINDOWS: dict[str, StepWindow]
TASK_WINDOWS: dict[str, TaskWindow]
LOCK: threading.Lock

def empty_task_row(run: int, tid: int | None, parent: int | None) -> TaskTally:
    ...

def note_spawned(event: dict) -> None:
    ...

def note_task_step(event: dict) -> None:
    ...

def task_table(window: TaskWindow) -> tuple:
    ...

def sink_claimed() -> None:
    ...

def sink_released() -> None:
    ...

def step_sink(event: dict) -> None:
    ...

def opened(key: str, thread: str, opened_ns: int) -> _Program[None, object]:
    ...

def closed(key: str) -> _Program[StepTally | None, object]:
    ...

def task_opened(key: str, opened_ns: int) -> _Program[None, object]:
    ...

def task_read(key: str) -> _Program[tuple | None, object]:
    ...

def task_closed(key: str) -> _Program[tuple | None, object]:
    ...
step_tally_handler: _Handler
