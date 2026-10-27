"""
doeff-core-effects — reference implementation of effects and handlers.

This package provides:
- Effects: Ask, Get, Put, Tell, ReadWriterLog
- Handlers: reader, state, writer
- Scheduler: Spawn, Wait, Gather, Race, Cancel, Discard, Promise, ExternalPromise, Semaphore
"""

import importlib as _importlib

from doeff_core_effects import _hy_submodules  # noqa: F401 - installs the lazy Hy finder first

# `X as X` makes each name public to type checkers: this package ships py.typed, where a plain import is not a
# re-export (pyright: reportPrivateImportUsage, mypy --strict: attr-defined). `__all__` is not used (DOEFF021).
from doeff_core_effects.effects import Absent as Absent
from doeff_core_effects.effects import Ask as Ask
from doeff_core_effects.effects import Await as Await
from doeff_core_effects.effects import Get as Get
from doeff_core_effects.effects import Listen as Listen
from doeff_core_effects.effects import Local as Local
from doeff_core_effects.effects import MissingEnvKeyError as MissingEnvKeyError
from doeff_core_effects.effects import Put as Put
from doeff_core_effects.effects import Raise as Raise
from doeff_core_effects.effects import ReadWriterLog as ReadWriterLog
from doeff_core_effects.effects import Resumption as Resumption
from doeff_core_effects.effects import Slog as Slog
from doeff_core_effects.effects import SlogEffect as SlogEffect
from doeff_core_effects.effects import Tell as Tell
from doeff_core_effects.effects import Try as Try
from doeff_core_effects.effects import WriterTellEffect as WriterTellEffect
from doeff_core_effects.effects import slog as slog
from doeff_core_effects.handlers import await_handler as await_handler
from doeff_core_effects.handlers import env_var_ask as env_var_ask
from doeff_core_effects.handlers import lazy_ask as lazy_ask
from doeff_core_effects.handlers import listen_handler as listen_handler
from doeff_core_effects.handlers import local_handler as local_handler
from doeff_core_effects.handlers import reader as reader
from doeff_core_effects.handlers import slog_discard_handler as slog_discard_handler
from doeff_core_effects.handlers import slog_handler as slog_handler
from doeff_core_effects.handlers import state as state
from doeff_core_effects.handlers import try_handler as try_handler
from doeff_core_effects.handlers import vm_probe_handler as vm_probe_handler
from doeff_core_effects.handlers import writer as writer

# absence and failure (ADR-DOE-CORE-EFFECTS-003)
from doeff_core_effects.outcomes import RaiseCase as RaiseCase
from doeff_core_effects.outcomes import absent_as as absent_as
from doeff_core_effects.outcomes import maybe as maybe
from doeff_core_effects.outcomes import on_raise as on_raise
from doeff_core_effects.outcomes import result as result
from doeff_core_effects.scheduler import PRIORITY_HIGH as PRIORITY_HIGH
from doeff_core_effects.scheduler import PRIORITY_IDLE as PRIORITY_IDLE
from doeff_core_effects.scheduler import PRIORITY_NORMAL as PRIORITY_NORMAL
from doeff_core_effects.scheduler import AcquireSemaphore as AcquireSemaphore
from doeff_core_effects.scheduler import Cancel as Cancel
from doeff_core_effects.scheduler import CompletePromise as CompletePromise
from doeff_core_effects.scheduler import CreateExternalPromise as CreateExternalPromise
from doeff_core_effects.scheduler import CreatePromise as CreatePromise
from doeff_core_effects.scheduler import CreateSemaphore as CreateSemaphore
from doeff_core_effects.scheduler import Discard as Discard
from doeff_core_effects.scheduler import ExternalPromise as ExternalPromise
from doeff_core_effects.scheduler import (
    ExternalPromiseCancelCallbackError as ExternalPromiseCancelCallbackError,
)
from doeff_core_effects.scheduler import FailPromise as FailPromise
from doeff_core_effects.scheduler import Future as Future
from doeff_core_effects.scheduler import Gather as Gather
from doeff_core_effects.scheduler import Promise as Promise
from doeff_core_effects.scheduler import Race as Race
from doeff_core_effects.scheduler import ReleaseSemaphore as ReleaseSemaphore
from doeff_core_effects.scheduler import SchedulerDeadlockError as SchedulerDeadlockError
from doeff_core_effects.scheduler import Semaphore as Semaphore
from doeff_core_effects.scheduler import Spawn as Spawn
from doeff_core_effects.scheduler import Task as Task
from doeff_core_effects.scheduler import TaskCancelledError as TaskCancelledError
from doeff_core_effects.scheduler import Wait as Wait
from doeff_core_effects.scheduler import scheduled as scheduled

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
from doeff_core_effects import unhandled_hints  # noqa: F401 - registers the hint lines with the VM

# HTTP effects and handlers live in Hy and pull in httpx; load them on first use. They are not re-exported with
# `X as X` (that would import the Hy modules here), so type checkers see them through `__getattr__` as `object`.
_LAZY_EXPORTS = {
    "HttpError": "doeff_core_effects.effects",
    "HttpRequest": "doeff_core_effects.effects",
    "HttpResponse": "doeff_core_effects.effects",
    "http_fixture_handler": "doeff_core_effects.http_handlers",
    "http_production_handler": "doeff_core_effects.http_handlers",
}


def __getattr__(name: str) -> object:
    module_name = _LAZY_EXPORTS.get(name)
    if module_name is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    value = getattr(_importlib.import_module(module_name), name)
    globals()[name] = value
    return value
