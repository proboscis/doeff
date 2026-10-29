"""
doeff-core-effects — reference implementation of effects and handlers.

This package provides:
- Effects: Ask, Get, Put, Tell, ReadWriterLog
- Handlers: reader, state, writer
- Scheduler: Spawn, Wait, Gather, Race, Cancel, Discard, Promise, ExternalPromise, Semaphore
"""

import importlib as _importlib
from typing import TYPE_CHECKING

# doeff imports this package's effects and scheduler while it loads, and they import the VM's types
# from doeff (doeff is the one package that imports doeff_vm). Loading doeff first binds those names
# before any of this package's modules asks for them, whichever package a program imports first.
import doeff as _doeff  # noqa: F401 - loaded for its import order, not for a name
from doeff_core_effects import _hy_submodules  # noqa: F401 - installs the lazy Hy finder first

# `X as X` makes each name public to type checkers: this package ships py.typed, where a plain import is not a
# re-export (pyright: reportPrivateImportUsage, mypy --strict: attr-defined). `__all__` is not used (DOEFF021).
from doeff_core_effects._effects import Absent as Absent
from doeff_core_effects._effects import Ask as Ask
from doeff_core_effects._effects import Await as Await
from doeff_core_effects._effects import Get as Get
from doeff_core_effects._effects import Listen as Listen
from doeff_core_effects._effects import Local as Local
from doeff_core_effects._effects import MissingEnvKeyError as MissingEnvKeyError
from doeff_core_effects._effects import Put as Put
from doeff_core_effects._effects import Raise as Raise
from doeff_core_effects._effects import ReadWriterLog as ReadWriterLog
from doeff_core_effects._effects import Resumption as Resumption
from doeff_core_effects._effects import Slog as Slog
from doeff_core_effects._effects import SlogEffect as SlogEffect
from doeff_core_effects._effects import Tell as Tell
from doeff_core_effects._effects import Try as Try
from doeff_core_effects._effects import WriterTellEffect as WriterTellEffect
from doeff_core_effects._effects import slog as slog
from doeff_core_effects._handlers import await_handler as await_handler
from doeff_core_effects._handlers import env_var_ask as env_var_ask
from doeff_core_effects._handlers import lazy_ask as lazy_ask
from doeff_core_effects._handlers import listen_handler as listen_handler
from doeff_core_effects._handlers import local_handler as local_handler
from doeff_core_effects._handlers import reader as reader
from doeff_core_effects._handlers import slog_discard_handler as slog_discard_handler
from doeff_core_effects._handlers import slog_handler as slog_handler
from doeff_core_effects._handlers import state as state
from doeff_core_effects._handlers import try_handler as try_handler
from doeff_core_effects._handlers import vm_probe_handler as vm_probe_handler
from doeff_core_effects._handlers import writer as writer

# absence and failure (ADR-DOE-CORE-EFFECTS-003)
from doeff_core_effects._outcomes import RaiseCase as RaiseCase
from doeff_core_effects._outcomes import absent_as as absent_as
from doeff_core_effects._outcomes import maybe as maybe
from doeff_core_effects._outcomes import on_raise as on_raise
from doeff_core_effects._outcomes import result as result
from doeff_core_effects._scheduler import PRIORITY_HIGH as PRIORITY_HIGH
from doeff_core_effects._scheduler import PRIORITY_IDLE as PRIORITY_IDLE
from doeff_core_effects._scheduler import PRIORITY_NORMAL as PRIORITY_NORMAL
from doeff_core_effects._scheduler import AcquireSemaphore as AcquireSemaphore
from doeff_core_effects._scheduler import Cancel as Cancel
from doeff_core_effects._scheduler import CompletePromise as CompletePromise
from doeff_core_effects._scheduler import CreateExternalPromise as CreateExternalPromise
from doeff_core_effects._scheduler import CreatePromise as CreatePromise
from doeff_core_effects._scheduler import CreateSemaphore as CreateSemaphore
from doeff_core_effects._scheduler import Discard as Discard
from doeff_core_effects._scheduler import ExternalPromise as ExternalPromise
from doeff_core_effects._scheduler import (
    ExternalPromiseCancelCallbackError as ExternalPromiseCancelCallbackError,
)
from doeff_core_effects._scheduler import FailPromise as FailPromise
from doeff_core_effects._scheduler import Future as Future
from doeff_core_effects._scheduler import Gather as Gather
from doeff_core_effects._scheduler import Promise as Promise
from doeff_core_effects._scheduler import Race as Race
from doeff_core_effects._scheduler import ReleaseSemaphore as ReleaseSemaphore
from doeff_core_effects._scheduler import SchedulerDeadlockError as SchedulerDeadlockError
from doeff_core_effects._scheduler import Semaphore as Semaphore
from doeff_core_effects._scheduler import Spawn as Spawn
from doeff_core_effects._scheduler import Task as Task
from doeff_core_effects._scheduler import TaskCancelledError as TaskCancelledError
from doeff_core_effects._scheduler import Wait as Wait
from doeff_core_effects._scheduler import scheduled as scheduled

# the doeff-vm's counts and switches, answered by vm_probe_handler (agora card ki-27e8ea225b62)
from doeff_core_effects.vm_probe_effects import ReadInvariantChecks as ReadInvariantChecks
from doeff_core_effects.vm_probe_effects import ReadVmLiveCounts as ReadVmLiveCounts
from doeff_core_effects.vm_probe_effects import ReadVmScanCounts as ReadVmScanCounts
from doeff_core_effects.vm_probe_effects import ReadVmWorkCounts as ReadVmWorkCounts
from doeff_core_effects.vm_probe_effects import SetInvariantChecks as SetInvariantChecks
from doeff_core_effects.vm_probe_effects import (
    SetSchedulerStepBudgetArmed as SetSchedulerStepBudgetArmed,
)
from doeff_core_effects.vm_probe_effects import (
    SetSchedulerTraceInstalled as SetSchedulerTraceInstalled,
)

# isort: split
# Last: the hint table reads the scheduler, which `import doeff` must finish loading first.
from doeff_core_effects import _unhandled_hints  # noqa: F401 - registers the hint lines with the VM

# The names below load on first use through `__getattr__` (ADR-DOE-API-001 R5: names come from this entry, the
# modules under it start with `_`), so `import doeff` - which loads this package - does not load their modules.
# Type checkers see them as `X as X` in the `if TYPE_CHECKING:` block. HTTP effects and handlers live in Hy and pull
# in httpx; they are left out of the block (that would read the Hy modules), so type checkers see them as `object`.
if TYPE_CHECKING:
    from doeff_core_effects._outcomes import AbsentAsToken as AbsentAsToken
    from doeff_core_effects._outcomes import BoundAbsent as BoundAbsent
    from doeff_core_effects._outcomes import absent_raises as absent_raises
    from doeff_core_effects._scheduler import ProcessStepBudget as ProcessStepBudget
    from doeff_core_effects._scheduler import SchedulerImplementation as SchedulerImplementation
    from doeff_core_effects._scheduler import SchedulerIndexDriftError as SchedulerIndexDriftError
    from doeff_core_effects._scheduler import SchedulerIntrospection as SchedulerIntrospection
    from doeff_core_effects._scheduler import SpentStepBudget as SpentStepBudget
    from doeff_core_effects._scheduler import StepBudget as StepBudget
    from doeff_core_effects._scheduler import StepBudgetExceeded as StepBudgetExceeded
    from doeff_core_effects._scheduler import TaskCompleted as TaskCompleted
    from doeff_core_effects._scheduler import TraceSink as TraceSink
    from doeff_core_effects._scheduler import VmWork as VmWork
    from doeff_core_effects._scheduler_channel import Channel as Channel
    from doeff_core_effects._scheduler_channel import CreateChannel as CreateChannel
    from doeff_core_effects._scheduler_channel import PutChannel as PutChannel
    from doeff_core_effects._scheduler_channel import TakeChannel as TakeChannel
    from doeff_core_effects._scheduler_channel import (
        scheduler_channel_handler as scheduler_channel_handler,
    )
    from doeff_core_effects._scheduler_step_tally import CloseStepTally as CloseStepTally
    from doeff_core_effects._scheduler_step_tally import CloseTaskTally as CloseTaskTally
    from doeff_core_effects._scheduler_step_tally import OpenStepTally as OpenStepTally
    from doeff_core_effects._scheduler_step_tally import OpenTaskTally as OpenTaskTally
    from doeff_core_effects._scheduler_step_tally import ReadTaskTally as ReadTaskTally
    from doeff_core_effects._scheduler_step_tally import SiteTally as SiteTally
    from doeff_core_effects._scheduler_step_tally import StepTally as StepTally
    from doeff_core_effects._scheduler_step_tally import TaskTally as TaskTally
    from doeff_core_effects._scheduler_step_tally import step_tally_handler as step_tally_handler
    from doeff_core_effects._storage import DurableStorage as DurableStorage
    from doeff_core_effects._storage.memory import InMemoryStorage as InMemoryStorage
    from doeff_core_effects._storage.sqlite import SQLiteStorage as SQLiteStorage

_LAZY_EXPORTS = {
    "HttpError": "doeff_core_effects._effects",
    "HttpRequest": "doeff_core_effects._effects",
    "HttpResponse": "doeff_core_effects._effects",
    "http_fixture_handler": "doeff_core_effects.http_handlers",
    "http_production_handler": "doeff_core_effects.http_handlers",
    "ProcessStepBudget": "doeff_core_effects._scheduler",
    "SchedulerImplementation": "doeff_core_effects._scheduler",
    "SchedulerIndexDriftError": "doeff_core_effects._scheduler",
    "SchedulerIntrospection": "doeff_core_effects._scheduler",
    "SpentStepBudget": "doeff_core_effects._scheduler",
    "StepBudget": "doeff_core_effects._scheduler",
    "StepBudgetExceeded": "doeff_core_effects._scheduler",
    "TaskCompleted": "doeff_core_effects._scheduler",
    "TraceSink": "doeff_core_effects._scheduler",
    "VmWork": "doeff_core_effects._scheduler",
    "Channel": "doeff_core_effects._scheduler_channel",
    "CreateChannel": "doeff_core_effects._scheduler_channel",
    "PutChannel": "doeff_core_effects._scheduler_channel",
    "TakeChannel": "doeff_core_effects._scheduler_channel",
    "scheduler_channel_handler": "doeff_core_effects._scheduler_channel",
    "CloseStepTally": "doeff_core_effects._scheduler_step_tally",
    "CloseTaskTally": "doeff_core_effects._scheduler_step_tally",
    "OpenStepTally": "doeff_core_effects._scheduler_step_tally",
    "OpenTaskTally": "doeff_core_effects._scheduler_step_tally",
    "ReadTaskTally": "doeff_core_effects._scheduler_step_tally",
    "SiteTally": "doeff_core_effects._scheduler_step_tally",
    "StepTally": "doeff_core_effects._scheduler_step_tally",
    "TaskTally": "doeff_core_effects._scheduler_step_tally",
    "step_tally_handler": "doeff_core_effects._scheduler_step_tally",
    "AbsentAsToken": "doeff_core_effects._outcomes",
    "BoundAbsent": "doeff_core_effects._outcomes",
    "absent_raises": "doeff_core_effects._outcomes",
    "DurableStorage": "doeff_core_effects._storage",
    "InMemoryStorage": "doeff_core_effects._storage.memory",
    "SQLiteStorage": "doeff_core_effects._storage.sqlite",
}


def __getattr__(name: str) -> object:
    module_name = _LAZY_EXPORTS.get(name)
    if module_name is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    value = getattr(_importlib.import_module(module_name), name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted({*globals(), *_LAZY_EXPORTS})
