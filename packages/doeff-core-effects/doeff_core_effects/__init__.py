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
from doeff_core_effects._vm_probe_effects import ReadInvariantChecks as ReadInvariantChecks
from doeff_core_effects._vm_probe_effects import ReadVmLiveCounts as ReadVmLiveCounts
from doeff_core_effects._vm_probe_effects import ReadVmScanCounts as ReadVmScanCounts
from doeff_core_effects._vm_probe_effects import ReadVmWorkCounts as ReadVmWorkCounts
from doeff_core_effects._vm_probe_effects import SetInvariantChecks as SetInvariantChecks
from doeff_core_effects._vm_probe_effects import (
    SetSchedulerStepBudgetArmed as SetSchedulerStepBudgetArmed,
)
from doeff_core_effects._vm_probe_effects import (
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
    from doeff_core_effects._apart_call import ApartClosed as ApartClosed
    from doeff_core_effects._apart_call import run_apart as run_apart
    from doeff_core_effects._cache_effects import CacheDelete as CacheDelete
    from doeff_core_effects._cache_effects import CacheDeleteEffect as CacheDeleteEffect
    from doeff_core_effects._cache_effects import CacheExists as CacheExists
    from doeff_core_effects._cache_effects import CacheExistsEffect as CacheExistsEffect
    from doeff_core_effects._cache_effects import CacheGet as CacheGet
    from doeff_core_effects._cache_effects import CacheGetEffect as CacheGetEffect
    from doeff_core_effects._cache_effects import CachePut as CachePut
    from doeff_core_effects._cache_effects import CachePutEffect as CachePutEffect
    from doeff_core_effects._cache_effects import cache_delete as cache_delete
    from doeff_core_effects._cache_effects import cache_exists as cache_exists
    from doeff_core_effects._cache_effects import cache_get as cache_get
    from doeff_core_effects._cache_effects import cache_put as cache_put
    from doeff_core_effects._cache_handlers import cache_handler as cache_handler
    from doeff_core_effects._cache_handlers import (
        in_memory_cache_handler as in_memory_cache_handler,
    )
    from doeff_core_effects._cache_handlers import sqlite_cache_handler as sqlite_cache_handler
    from doeff_core_effects._cache_policy import CacheLifecycle as CacheLifecycle
    from doeff_core_effects._cache_policy import CachePolicy as CachePolicy
    from doeff_core_effects._cache_policy import CacheStorage as CacheStorage
    from doeff_core_effects._compute_effects import Compute as Compute
    from doeff_core_effects._compute_effects import Computed as Computed
    from doeff_core_effects._compute_effects import ComputeFailed as ComputeFailed
    from doeff_core_effects._compute_effects import ComputeLoad as ComputeLoad
    from doeff_core_effects._compute_effects import ReadComputeLoad as ReadComputeLoad
    from doeff_core_effects._faulthandler_stack_dump import (
        faulthandler_stack_dump_handler as faulthandler_stack_dump_handler,
    )
    from doeff_core_effects._file_effects import AcquireLock as AcquireLock
    from doeff_core_effects._file_effects import AppendBytes as AppendBytes
    from doeff_core_effects._file_effects import Appended as Appended
    from doeff_core_effects._file_effects import AppendText as AppendText
    from doeff_core_effects._file_effects import CompilePythonSources as CompilePythonSources
    from doeff_core_effects._file_effects import CopyFile as CopyFile
    from doeff_core_effects._file_effects import CopyTree as CopyTree
    from doeff_core_effects._file_effects import DirEntry as DirEntry
    from doeff_core_effects._file_effects import DiskUsage as DiskUsage
    from doeff_core_effects._file_effects import EntryStat as EntryStat
    from doeff_core_effects._file_effects import FileFailed as FileFailed
    from doeff_core_effects._file_effects import LinkFile as LinkFile
    from doeff_core_effects._file_effects import ListDirectory as ListDirectory
    from doeff_core_effects._file_effects import LockHeld as LockHeld
    from doeff_core_effects._file_effects import MakeDirectory as MakeDirectory
    from doeff_core_effects._file_effects import MakeSymlink as MakeSymlink
    from doeff_core_effects._file_effects import MeasureTree as MeasureTree
    from doeff_core_effects._file_effects import MemoryFile as MemoryFile
    from doeff_core_effects._file_effects import MemoryFiles as MemoryFiles
    from doeff_core_effects._file_effects import MemoryLink as MemoryLink
    from doeff_core_effects._file_effects import PathKind as PathKind
    from doeff_core_effects._file_effects import PathStat as PathStat
    from doeff_core_effects._file_effects import ReadBytes as ReadBytes
    from doeff_core_effects._file_effects import ReadDiskFree as ReadDiskFree
    from doeff_core_effects._file_effects import ReadDiskUsage as ReadDiskUsage
    from doeff_core_effects._file_effects import ReadMemoryFiles as ReadMemoryFiles
    from doeff_core_effects._file_effects import ReadText as ReadText
    from doeff_core_effects._file_effects import ReleaseLock as ReleaseLock
    from doeff_core_effects._file_effects import RemoveTree as RemoveTree
    from doeff_core_effects._file_effects import RenamePath as RenamePath
    from doeff_core_effects._file_effects import StatPath as StatPath
    from doeff_core_effects._file_effects import StatTree as StatTree
    from doeff_core_effects._file_effects import SyncDirectory as SyncDirectory
    from doeff_core_effects._file_effects import TruncateFile as TruncateFile
    from doeff_core_effects._file_effects import WalkTree as WalkTree
    from doeff_core_effects._file_effects import WriteBytes as WriteBytes
    from doeff_core_effects._file_effects import WriteText as WriteText
    from doeff_core_effects._gc_freeze import gc_freeze_handler as gc_freeze_handler
    from doeff_core_effects._heap_effects import CollectAndFreeze as CollectAndFreeze
    from doeff_core_effects._inline_compute import inline_compute_handler as inline_compute_handler
    from doeff_core_effects._latest_effects import AwaitLatest as AwaitLatest
    from doeff_core_effects._latest_effects import PublishLatest as PublishLatest
    from doeff_core_effects._latest_effects import ReadLatest as ReadLatest
    from doeff_core_effects._memo_effects import MemoDelete as MemoDelete
    from doeff_core_effects._memo_effects import MemoDeleteEffect as MemoDeleteEffect
    from doeff_core_effects._memo_effects import MemoExists as MemoExists
    from doeff_core_effects._memo_effects import MemoExistsEffect as MemoExistsEffect
    from doeff_core_effects._memo_effects import MemoGet as MemoGet
    from doeff_core_effects._memo_effects import MemoGetEffect as MemoGetEffect
    from doeff_core_effects._memo_effects import MemoPut as MemoPut
    from doeff_core_effects._memo_effects import MemoPutEffect as MemoPutEffect
    from doeff_core_effects._memo_handlers import MemoKeyFn as MemoKeyFn
    from doeff_core_effects._memo_handlers import in_memory_memo_handler as in_memory_memo_handler
    from doeff_core_effects._memo_handlers import make_memo_rewriter as make_memo_rewriter
    from doeff_core_effects._memo_handlers import memo_handler as memo_handler
    from doeff_core_effects._memo_handlers import memo_rewriters as memo_rewriters
    from doeff_core_effects._memo_handlers import sqlite_memo_handler as sqlite_memo_handler
    from doeff_core_effects._memo_policy import Lifecycle as Lifecycle
    from doeff_core_effects._memo_policy import MemoPolicy as MemoPolicy
    from doeff_core_effects._memo_policy import RecomputeCost as RecomputeCost
    from doeff_core_effects._memory_file import memory_file_handler as memory_file_handler
    from doeff_core_effects._memory_latest import MemoryLatestStore as MemoryLatestStore
    from doeff_core_effects._memory_latest import memory_latest_handler as memory_latest_handler
    from doeff_core_effects._memory_meter import memory_meter_handler as memory_meter_handler
    from doeff_core_effects._memory_stack_dump import (
        memory_stack_dump_handler as memory_stack_dump_handler,
    )
    from doeff_core_effects._meter_effects import CountMetric as CountMetric
    from doeff_core_effects._meter_effects import MeterBucket as MeterBucket
    from doeff_core_effects._meter_effects import MeterSettings as MeterSettings
    from doeff_core_effects._meter_effects import MeterSnapshot as MeterSnapshot
    from doeff_core_effects._meter_effects import ObserveSeconds as ObserveSeconds
    from doeff_core_effects._meter_effects import ObserveSecondsBatch as ObserveSecondsBatch
    from doeff_core_effects._meter_effects import ReadMeter as ReadMeter
    from doeff_core_effects._meter_effects import SecondsObservation as SecondsObservation
    from doeff_core_effects._meter_effects import SecondsTotal as SecondsTotal
    from doeff_core_effects._meter_effects import SetGauge as SetGauge
    from doeff_core_effects._os_file import offloaded_file_handler as offloaded_file_handler
    from doeff_core_effects._os_file import offloaded_lock_handler as offloaded_lock_handler
    from doeff_core_effects._os_file import offloaded_tree_handler as offloaded_tree_handler
    from doeff_core_effects._os_file import os_file_handler as os_file_handler
    from doeff_core_effects._os_random import os_random_handler as os_random_handler
    from doeff_core_effects._outcomes import AbsentAsToken as AbsentAsToken
    from doeff_core_effects._outcomes import BoundAbsent as BoundAbsent
    from doeff_core_effects._outcomes import absent_raises as absent_raises
    from doeff_core_effects._python_bytecode import SourceNotCompiled as SourceNotCompiled
    from doeff_core_effects._random_effects import RandomBytes as RandomBytes
    from doeff_core_effects._rooted_file import rooted_file_handler as rooted_file_handler
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
    from doeff_core_effects._clickhouse_mock_sql import (
        clickhouse_mock_sql_handler as clickhouse_mock_sql_handler,
    )
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
    from doeff_core_effects._scripted_freeze import (
        scripted_freeze_handler as scripted_freeze_handler,
    )
    from doeff_core_effects._seeded_random import seeded_random_handler as seeded_random_handler
    from doeff_core_effects._stack_dump_effects import ArmStackDump as ArmStackDump
    from doeff_core_effects._stack_dump_effects import DisarmStackDump as DisarmStackDump
    from doeff_core_effects._stack_dump_effects import ReadStackDumps as ReadStackDumps
    from doeff_core_effects._stack_dump_effects import StackDumpLedger as StackDumpLedger
    from doeff_core_effects._storage import DurableStorage as DurableStorage
    from doeff_core_effects._storage.memory import InMemoryStorage as InMemoryStorage
    from doeff_core_effects._storage.sqlite import SQLiteStorage as SQLiteStorage
    from doeff_core_effects._thread_pool_compute import (
        thread_pool_compute_handler as thread_pool_compute_handler,
    )

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
    "clickhouse_mock_sql_handler": "doeff_core_effects._clickhouse_mock_sql",
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
    "SourceNotCompiled": "doeff_core_effects._python_bytecode",
    "ArmStackDump": "doeff_core_effects._stack_dump_effects",
    "DisarmStackDump": "doeff_core_effects._stack_dump_effects",
    "ReadStackDumps": "doeff_core_effects._stack_dump_effects",
    "StackDumpLedger": "doeff_core_effects._stack_dump_effects",
    "memory_stack_dump_handler": "doeff_core_effects._memory_stack_dump",
    "faulthandler_stack_dump_handler": "doeff_core_effects._faulthandler_stack_dump",
    "gc_freeze_handler": "doeff_core_effects._gc_freeze",
    "scripted_freeze_handler": "doeff_core_effects._scripted_freeze",
    "CollectAndFreeze": "doeff_core_effects._heap_effects",
    "ApartClosed": "doeff_core_effects._apart_call",
    "run_apart": "doeff_core_effects._apart_call",
    "inline_compute_handler": "doeff_core_effects._inline_compute",
    "Compute": "doeff_core_effects._compute_effects",
    "ComputeFailed": "doeff_core_effects._compute_effects",
    "ComputeLoad": "doeff_core_effects._compute_effects",
    "Computed": "doeff_core_effects._compute_effects",
    "ReadComputeLoad": "doeff_core_effects._compute_effects",
    "thread_pool_compute_handler": "doeff_core_effects._thread_pool_compute",
    "RandomBytes": "doeff_core_effects._random_effects",
    "os_random_handler": "doeff_core_effects._os_random",
    "seeded_random_handler": "doeff_core_effects._seeded_random",
    "AwaitLatest": "doeff_core_effects._latest_effects",
    "PublishLatest": "doeff_core_effects._latest_effects",
    "ReadLatest": "doeff_core_effects._latest_effects",
    "MemoryLatestStore": "doeff_core_effects._memory_latest",
    "memory_latest_handler": "doeff_core_effects._memory_latest",
    "CountMetric": "doeff_core_effects._meter_effects",
    "MeterBucket": "doeff_core_effects._meter_effects",
    "MeterSettings": "doeff_core_effects._meter_effects",
    "MeterSnapshot": "doeff_core_effects._meter_effects",
    "ObserveSeconds": "doeff_core_effects._meter_effects",
    "ObserveSecondsBatch": "doeff_core_effects._meter_effects",
    "ReadMeter": "doeff_core_effects._meter_effects",
    "SecondsObservation": "doeff_core_effects._meter_effects",
    "SecondsTotal": "doeff_core_effects._meter_effects",
    "SetGauge": "doeff_core_effects._meter_effects",
    "memory_meter_handler": "doeff_core_effects._memory_meter",
    "AcquireLock": "doeff_core_effects._file_effects",
    "AppendBytes": "doeff_core_effects._file_effects",
    "AppendText": "doeff_core_effects._file_effects",
    "Appended": "doeff_core_effects._file_effects",
    "CompilePythonSources": "doeff_core_effects._file_effects",
    "CopyFile": "doeff_core_effects._file_effects",
    "CopyTree": "doeff_core_effects._file_effects",
    "DirEntry": "doeff_core_effects._file_effects",
    "DiskUsage": "doeff_core_effects._file_effects",
    "EntryStat": "doeff_core_effects._file_effects",
    "FileFailed": "doeff_core_effects._file_effects",
    "LinkFile": "doeff_core_effects._file_effects",
    "ListDirectory": "doeff_core_effects._file_effects",
    "LockHeld": "doeff_core_effects._file_effects",
    "MakeDirectory": "doeff_core_effects._file_effects",
    "MakeSymlink": "doeff_core_effects._file_effects",
    "MeasureTree": "doeff_core_effects._file_effects",
    "MemoryFile": "doeff_core_effects._file_effects",
    "MemoryFiles": "doeff_core_effects._file_effects",
    "MemoryLink": "doeff_core_effects._file_effects",
    "PathKind": "doeff_core_effects._file_effects",
    "PathStat": "doeff_core_effects._file_effects",
    "ReadBytes": "doeff_core_effects._file_effects",
    "ReadDiskFree": "doeff_core_effects._file_effects",
    "ReadDiskUsage": "doeff_core_effects._file_effects",
    "ReadMemoryFiles": "doeff_core_effects._file_effects",
    "ReadText": "doeff_core_effects._file_effects",
    "ReleaseLock": "doeff_core_effects._file_effects",
    "RemoveTree": "doeff_core_effects._file_effects",
    "RenamePath": "doeff_core_effects._file_effects",
    "StatPath": "doeff_core_effects._file_effects",
    "StatTree": "doeff_core_effects._file_effects",
    "SyncDirectory": "doeff_core_effects._file_effects",
    "TruncateFile": "doeff_core_effects._file_effects",
    "WalkTree": "doeff_core_effects._file_effects",
    "WriteBytes": "doeff_core_effects._file_effects",
    "WriteText": "doeff_core_effects._file_effects",
    "offloaded_file_handler": "doeff_core_effects._os_file",
    "offloaded_lock_handler": "doeff_core_effects._os_file",
    "offloaded_tree_handler": "doeff_core_effects._os_file",
    "os_file_handler": "doeff_core_effects._os_file",
    "memory_file_handler": "doeff_core_effects._memory_file",
    "rooted_file_handler": "doeff_core_effects._rooted_file",
    "MemoDelete": "doeff_core_effects._memo_effects",
    "MemoDeleteEffect": "doeff_core_effects._memo_effects",
    "MemoExists": "doeff_core_effects._memo_effects",
    "MemoExistsEffect": "doeff_core_effects._memo_effects",
    "MemoGet": "doeff_core_effects._memo_effects",
    "MemoGetEffect": "doeff_core_effects._memo_effects",
    "MemoPut": "doeff_core_effects._memo_effects",
    "MemoPutEffect": "doeff_core_effects._memo_effects",
    "MemoKeyFn": "doeff_core_effects._memo_handlers",
    "in_memory_memo_handler": "doeff_core_effects._memo_handlers",
    "make_memo_rewriter": "doeff_core_effects._memo_handlers",
    "memo_handler": "doeff_core_effects._memo_handlers",
    "memo_rewriters": "doeff_core_effects._memo_handlers",
    "sqlite_memo_handler": "doeff_core_effects._memo_handlers",
    "Lifecycle": "doeff_core_effects._memo_policy",
    "MemoPolicy": "doeff_core_effects._memo_policy",
    "RecomputeCost": "doeff_core_effects._memo_policy",
    "CacheDelete": "doeff_core_effects._cache_effects",
    "CacheDeleteEffect": "doeff_core_effects._cache_effects",
    "CacheExists": "doeff_core_effects._cache_effects",
    "CacheExistsEffect": "doeff_core_effects._cache_effects",
    "CacheGet": "doeff_core_effects._cache_effects",
    "CacheGetEffect": "doeff_core_effects._cache_effects",
    "CachePut": "doeff_core_effects._cache_effects",
    "CachePutEffect": "doeff_core_effects._cache_effects",
    "cache_delete": "doeff_core_effects._cache_effects",
    "cache_exists": "doeff_core_effects._cache_effects",
    "cache_get": "doeff_core_effects._cache_effects",
    "cache_put": "doeff_core_effects._cache_effects",
    "cache_handler": "doeff_core_effects._cache_handlers",
    "in_memory_cache_handler": "doeff_core_effects._cache_handlers",
    "sqlite_cache_handler": "doeff_core_effects._cache_handlers",
    "CacheLifecycle": "doeff_core_effects._cache_policy",
    "CachePolicy": "doeff_core_effects._cache_policy",
    "CacheStorage": "doeff_core_effects._cache_policy",
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
