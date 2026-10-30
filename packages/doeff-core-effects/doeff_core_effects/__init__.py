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
    from doeff_core_effects._file_effects import file_done as file_done
    from doeff_core_effects._inline_compute import computed as computed
    from doeff_core_effects._meter_prometheus import render_prometheus as render_prometheus
    from doeff_core_effects._offloaded_call import offloaded as offloaded
    from doeff_core_effects._python_bytecode import checked_hash_pyc as checked_hash_pyc
    from doeff_core_effects._python_bytecode import compiled_pyc as compiled_pyc
    from doeff_core_effects._seeded_random import seeded_bytes as seeded_bytes
    from doeff_core_effects._stack_dump_effects import stack_dumps_at as stack_dumps_at
    from doeff_core_effects._http_server_effects import AppendHttpScript as AppendHttpScript
    from doeff_core_effects._http_server_effects import HttpAddress as HttpAddress
    from doeff_core_effects._http_server_effects import HttpBody as HttpBody
    from doeff_core_effects._http_server_effects import HttpBodyBytes as HttpBodyBytes
    from doeff_core_effects._http_server_effects import HttpBodyFailed as HttpBodyFailed
    from doeff_core_effects._http_server_effects import HttpBodyFileRange as HttpBodyFileRange
    from doeff_core_effects._http_server_effects import HttpBodyOutcome as HttpBodyOutcome
    from doeff_core_effects._http_server_effects import HttpBodyRead as HttpBodyRead
    from doeff_core_effects._http_server_effects import HttpBodyTooLarge as HttpBodyTooLarge
    from doeff_core_effects._http_server_effects import HttpCommand as HttpCommand
    from doeff_core_effects._http_server_effects import HttpEvent as HttpEvent
    from doeff_core_effects._http_server_effects import HttpForward as HttpForward
    from doeff_core_effects._http_server_effects import HttpHeader as HttpHeader
    from doeff_core_effects._http_server_effects import HttpListen as HttpListen
    from doeff_core_effects._http_server_effects import HttpNextRequest as HttpNextRequest
    from doeff_core_effects._http_server_effects import HttpNoBody as HttpNoBody
    from doeff_core_effects._http_server_effects import HttpProbe as HttpProbe
    from doeff_core_effects._http_server_effects import HttpProbeAnswer as HttpProbeAnswer
    from doeff_core_effects._http_server_effects import HttpReadBody as HttpReadBody
    from doeff_core_effects._http_server_effects import HttpRequestArrived as HttpRequestArrived
    from doeff_core_effects._http_server_effects import HttpRespond as HttpRespond
    from doeff_core_effects._http_server_effects import HttpScript as HttpScript
    from doeff_core_effects._http_server_effects import HttpServed as HttpServed
    from doeff_core_effects._http_server_effects import HttpServerClosed as HttpServerClosed
    from doeff_core_effects._http_server_effects import HttpShutdown as HttpShutdown
    from doeff_core_effects._http_server_effects import HttpStopListening as HttpStopListening
    from doeff_core_effects._http_server_effects import HttpTakeQueued as HttpTakeQueued
    from doeff_core_effects._http_server_effects import ReadHttpServed as ReadHttpServed
    from doeff_core_effects._http_server_effects import ScriptedBody as ScriptedBody
    from doeff_core_effects._http_server_effects import ScriptedUpstream as ScriptedUpstream
    from doeff_core_effects._http_server_effects import TakeWsSendReport as TakeWsSendReport
    from doeff_core_effects._http_server_effects import WsAccept as WsAccept
    from doeff_core_effects._http_server_effects import WsBinaryArrived as WsBinaryArrived
    from doeff_core_effects._http_server_effects import WsClose as WsClose
    from doeff_core_effects._http_server_effects import WsCloseSent as WsCloseSent
    from doeff_core_effects._http_server_effects import WsClosed as WsClosed
    from doeff_core_effects._http_server_effects import WsEvent as WsEvent
    from doeff_core_effects._http_server_effects import WsForward as WsForward
    from doeff_core_effects._http_server_effects import WsOpened as WsOpened
    from doeff_core_effects._http_server_effects import WsSendReport as WsSendReport
    from doeff_core_effects._http_server_effects import WsSendText as WsSendText
    from doeff_core_effects._http_server_effects import WsSendTextEach as WsSendTextEach
    from doeff_core_effects._http_server_effects import WsShortcut as WsShortcut
    from doeff_core_effects._http_server_effects import WsShortcutAnswered as WsShortcutAnswered
    from doeff_core_effects._http_server_effects import WsShortcutFailed as WsShortcutFailed
    from doeff_core_effects._http_server_effects import WsTextArrived as WsTextArrived
    from doeff_core_effects._http_server_effects import WsTextSent as WsTextSent
    from doeff_core_effects._http_server_effects import ws_shortcut_for as ws_shortcut_for
    from doeff_core_effects._http_server_effects import ws_shortcut_outcome as ws_shortcut_outcome
    from doeff_core_effects._aiohttp_http_server import WebEdge as WebEdge
    from doeff_core_effects._aiohttp_http_server import aiohttp_http_server as aiohttp_http_server
    from doeff_core_effects._scripted_http_server import ScriptSend as ScriptSend
    from doeff_core_effects._scripted_http_server import script_sent as script_sent
    from doeff_core_effects._scripted_http_server import (
        scripted_http_server as scripted_http_server,
    )
    from doeff_core_effects._process_effects import AwaitProcessExit as AwaitProcessExit
    from doeff_core_effects._process_effects import ChildLifetime as ChildLifetime
    from doeff_core_effects._process_effects import EnvEntry as EnvEntry
    from doeff_core_effects._process_effects import EnvMode as EnvMode
    from doeff_core_effects._process_effects import ExecutableAt as ExecutableAt
    from doeff_core_effects._process_effects import InterpreterFacts as InterpreterFacts
    from doeff_core_effects._process_effects import ModuleFound as ModuleFound
    from doeff_core_effects._process_effects import ModuleNotFound as ModuleNotFound
    from doeff_core_effects._process_effects import PollProcess as PollProcess
    from doeff_core_effects._process_effects import ProcessAlive as ProcessAlive
    from doeff_core_effects._process_effects import ProcessEnded as ProcessEnded
    from doeff_core_effects._process_effects import ProcessExited as ProcessExited
    from doeff_core_effects._process_effects import ProcessInputWritten as ProcessInputWritten
    from doeff_core_effects._process_effects import ProcessNotChild as ProcessNotChild
    from doeff_core_effects._process_effects import ProcessNotStarted as ProcessNotStarted
    from doeff_core_effects._process_effects import ProcessOutcome as ProcessOutcome
    from doeff_core_effects._process_effects import ProcessRunning as ProcessRunning
    from doeff_core_effects._process_effects import ProcessSignal as ProcessSignal
    from doeff_core_effects._process_effects import ProcessSignalled as ProcessSignalled
    from doeff_core_effects._process_effects import ProcessStarted as ProcessStarted
    from doeff_core_effects._process_effects import ReadEnvironment as ReadEnvironment
    from doeff_core_effects._process_effects import ReadInterpreter as ReadInterpreter
    from doeff_core_effects._process_effects import ReadMachineName as ReadMachineName
    from doeff_core_effects._process_effects import ResolveModule as ResolveModule
    from doeff_core_effects._process_effects import RunProcess as RunProcess
    from doeff_core_effects._process_effects import SignalProcess as SignalProcess
    from doeff_core_effects._process_effects import StartProcess as StartProcess
    from doeff_core_effects._process_effects import StopProcess as StopProcess
    from doeff_core_effects._process_effects import WorkingDirectory as WorkingDirectory
    from doeff_core_effects._process_effects import WriteProcessInput as WriteProcessInput
    from doeff_core_effects._process_effects import environment_answer as environment_answer
    from doeff_core_effects._process_effects import environment_mapping as environment_mapping
    from doeff_core_effects._process_effects import executable_file_answer as executable_file_answer
    from doeff_core_effects._process_effects import not_started_outcome as not_started_outcome
    from doeff_core_effects._process_effects import start_refusal as start_refusal
    from doeff_core_effects._process_effects import timed_out_outcome as timed_out_outcome
    from doeff_core_effects._os_process import (
        metered_offloaded_subprocess_handler as metered_offloaded_subprocess_handler,
    )
    from doeff_core_effects._os_process import (
        offloaded_subprocess_handler as offloaded_subprocess_handler,
    )
    from doeff_core_effects._os_process import os_executable_at as os_executable_at
    from doeff_core_effects._os_process import os_process_alive as os_process_alive
    from doeff_core_effects._os_process import subprocess_handler as subprocess_handler
    from doeff_core_effects._scripted_process import ProcessScript as ProcessScript
    from doeff_core_effects._scripted_process import ScriptedCommand as ScriptedCommand
    from doeff_core_effects._scripted_process import run_scripted as run_scripted
    from doeff_core_effects._scripted_process import scripted_command_peer as scripted_command_peer
    from doeff_core_effects._scripted_process import (
        scripted_process_handler as scripted_process_handler,
    )
    from doeff_core_effects._process_exit import ExitEnd as ExitEnd
    from doeff_core_effects._process_exit import ExitWaitUnavailable as ExitWaitUnavailable
    from doeff_core_effects._process_exit import process_exit_handler as process_exit_handler
    from doeff_core_effects._process_latest import process_latest_handler as process_latest_handler
    from doeff_core_effects._process_meter import process_meter_handler as process_meter_handler
    from doeff_core_effects._proc_table_effects import ProcRow as ProcRow
    from doeff_core_effects._proc_table_effects import ReadProcTable as ReadProcTable
    from doeff_core_effects._proc_table_effects import SignalPid as SignalPid
    from doeff_core_effects._os_proc_table import ProcTableUnavailable as ProcTableUnavailable
    from doeff_core_effects._os_proc_table import os_proc_table_handler as os_proc_table_handler
    from doeff_core_effects._scripted_proc_table import (
        scripted_proc_table_handler as scripted_proc_table_handler,
    )
    from doeff_core_effects._warm_effects import AwaitWarmChildExit as AwaitWarmChildExit
    from doeff_core_effects._warm_effects import ForkFromWarm as ForkFromWarm
    from doeff_core_effects._warm_effects import PollWarmChild as PollWarmChild
    from doeff_core_effects._warm_effects import SignalWarmChild as SignalWarmChild
    from doeff_core_effects._warm_effects import WarmExited as WarmExited
    from doeff_core_effects._warm_effects import WarmForked as WarmForked
    from doeff_core_effects._warm_effects import WarmGone as WarmGone
    from doeff_core_effects._warm_effects import WarmInputWritten as WarmInputWritten
    from doeff_core_effects._warm_effects import WarmLost as WarmLost
    from doeff_core_effects._warm_effects import WarmRefused as WarmRefused
    from doeff_core_effects._warm_effects import WarmRunning as WarmRunning
    from doeff_core_effects._warm_effects import WarmSignaled as WarmSignaled
    from doeff_core_effects._warm_effects import WriteWarmChildInput as WriteWarmChildInput
    from doeff_core_effects._os_warm_process import HeldInput as HeldInput
    from doeff_core_effects._os_warm_process import WarmProcUnavailable as WarmProcUnavailable
    from doeff_core_effects._os_warm_process import input_written as input_written
    from doeff_core_effects._os_warm_process import (
        os_warm_process_handler as os_warm_process_handler,
    )
    from doeff_core_effects._os_warm_process import own_thread_count as own_thread_count
    from doeff_core_effects._os_warm_process import proc_stat_of as proc_stat_of
    from doeff_core_effects._os_warm_process import warm_answer_line as warm_answer_line
    from doeff_core_effects._os_warm_process import warm_request_of as warm_request_of
    from doeff_core_effects._scripted_warm_process import WarmRun as WarmRun
    from doeff_core_effects._scripted_warm_process import WarmScript as WarmScript
    from doeff_core_effects._scripted_warm_process import WarmSocket as WarmSocket
    from doeff_core_effects._scripted_warm_process import WarmSocketAnswer as WarmSocketAnswer
    from doeff_core_effects._scripted_warm_process import (
        scripted_warm_process_handler as scripted_warm_process_handler,
    )
    from doeff_core_effects._stop_signal_effects import AwaitStop as AwaitStop
    from doeff_core_effects._stop_signal_effects import RaiseStop as RaiseStop
    from doeff_core_effects._stop_signal_effects import StopBell as StopBell
    from doeff_core_effects._stop_signal_effects import StopRequested as StopRequested
    from doeff_core_effects._stop_signal_handlers import (
        os_signal_stop_handler as os_signal_stop_handler,
    )
    from doeff_core_effects._stop_signal_handlers import (
        scripted_stop_handler as scripted_stop_handler,
    )
    from doeff_core_effects._http_effects import HttpFailed as HttpFailed
    from doeff_core_effects._http_effects import HttpFailureKind as HttpFailureKind
    from doeff_core_effects._http_effects import HttpPhases as HttpPhases
    from doeff_core_effects._ws_client_effects import ReadWsSent as ReadWsSent
    from doeff_core_effects._ws_client_effects import ScriptedWsBinary as ScriptedWsBinary
    from doeff_core_effects._ws_client_effects import ScriptedWsClosed as ScriptedWsClosed
    from doeff_core_effects._ws_client_effects import ScriptedWsEndpoint as ScriptedWsEndpoint
    from doeff_core_effects._ws_client_effects import ScriptedWsFrame as ScriptedWsFrame
    from doeff_core_effects._ws_client_effects import ScriptedWsReply as ScriptedWsReply
    from doeff_core_effects._ws_client_effects import ScriptedWsText as ScriptedWsText
    from doeff_core_effects._ws_client_effects import WsBinary as WsBinary
    from doeff_core_effects._ws_client_effects import WsConnect as WsConnect
    from doeff_core_effects._ws_client_effects import WsConnectFailed as WsConnectFailed
    from doeff_core_effects._ws_client_effects import WsConnectOutcome as WsConnectOutcome
    from doeff_core_effects._ws_client_effects import WsDisconnect as WsDisconnect
    from doeff_core_effects._ws_client_effects import WsDisconnectAll as WsDisconnectAll
    from doeff_core_effects._ws_client_effects import WsFrame as WsFrame
    from doeff_core_effects._ws_client_effects import WsLink as WsLink
    from doeff_core_effects._ws_client_effects import WsLinkClosed as WsLinkClosed
    from doeff_core_effects._ws_client_effects import WsReceive as WsReceive
    from doeff_core_effects._ws_client_effects import WsScript as WsScript
    from doeff_core_effects._ws_client_effects import WsSend as WsSend
    from doeff_core_effects._ws_client_effects import WsSendOutcome as WsSendOutcome
    from doeff_core_effects._ws_client_effects import WsSent as WsSent
    from doeff_core_effects._ws_client_effects import WsSentClose as WsSentClose
    from doeff_core_effects._ws_client_effects import WsSentRecord as WsSentRecord
    from doeff_core_effects._ws_client_effects import WsSentText as WsSentText
    from doeff_core_effects._ws_client_effects import WsText as WsText
    from doeff_core_effects._ws_client_effects import closing_links as closing_links
    from doeff_core_effects._aiohttp_ws_client import (
        aiohttp_ws_link_handler as aiohttp_ws_link_handler,
    )
    from doeff_core_effects._scripted_ws_client import (
        scripted_ws_link_handler as scripted_ws_link_handler,
    )
    from doeff_core_effects._http_host_gate import HostNotAllowed as HostNotAllowed
    from doeff_core_effects._http_host_gate import http_host_gate as http_host_gate
    from doeff_core_effects._sql_effects import SetSqlOutage as SetSqlOutage
    from doeff_core_effects._sql_effects import SqlBatch as SqlBatch
    from doeff_core_effects._sql_effects import SqlColumn as SqlColumn
    from doeff_core_effects._sql_effects import SqlColumnType as SqlColumnType
    from doeff_core_effects._sql_effects import SqlDropNotice as SqlDropNotice
    from doeff_core_effects._sql_effects import SqlEnsureTables as SqlEnsureTables
    from doeff_core_effects._sql_effects import SqlFailed as SqlFailed
    from doeff_core_effects._sql_effects import SqlHangNotice as SqlHangNotice
    from doeff_core_effects._sql_effects import SqlIndex as SqlIndex
    from doeff_core_effects._sql_effects import SqlInsertRows as SqlInsertRows
    from doeff_core_effects._sql_effects import SqlLock as SqlLock
    from doeff_core_effects._sql_effects import SqlNotify as SqlNotify
    from doeff_core_effects._sql_effects import SqlParam as SqlParam
    from doeff_core_effects._sql_effects import SqlQuery as SqlQuery
    from doeff_core_effects._sql_effects import SqlRows as SqlRows
    from doeff_core_effects._sql_effects import SqlSchemaApplied as SqlSchemaApplied
    from doeff_core_effects._sql_effects import SqlTable as SqlTable
    from doeff_core_effects._sql_effects import SqlTransaction as SqlTransaction
    from doeff_core_effects._sql_effects import SqlTransactionMisuse as SqlTransactionMisuse
    from doeff_core_effects._sql_effects import SqlUnreachable as SqlUnreachable
    from doeff_core_effects._sql_effects import SqlValue as SqlValue
    from doeff_core_effects._postgres_sql import PostgresConnections as PostgresConnections
    from doeff_core_effects._postgres_sql import PostgresDatabase as PostgresDatabase
    from doeff_core_effects._postgres_sql import PostgresTimeouts as PostgresTimeouts
    from doeff_core_effects._postgres_sql import TransactionAbandoned as TransactionAbandoned
    from doeff_core_effects._postgres_sql import postgres_sql_handler as postgres_sql_handler
    from doeff_core_effects._pooled_postgres_sql import (
        pooled_postgres_sql_handler as pooled_postgres_sql_handler,
    )
    from doeff_core_effects._sqlite_sql import SqliteConnection as SqliteConnection
    from doeff_core_effects._sqlite_sql import sqlite_connection as sqlite_connection
    from doeff_core_effects._sqlite_sql import sqlite_insert as sqlite_insert
    from doeff_core_effects._sqlite_sql import sqlite_query as sqlite_query
    from doeff_core_effects._sqlite_sql import sqlite_sql_handler as sqlite_sql_handler
    from doeff_core_effects._sqlite_file_sql import SqliteFile as SqliteFile
    from doeff_core_effects._sqlite_file_sql import SqliteFiles as SqliteFiles
    from doeff_core_effects._sqlite_file_sql import close_sqlite_files as close_sqlite_files
    from doeff_core_effects._sqlite_file_sql import open_sqlite_files as open_sqlite_files
    from doeff_core_effects._sqlite_file_sql import (
        sqlite_file_sql_handler as sqlite_file_sql_handler,
    )
    from doeff_core_effects._clickhouse_http_sql import ClickHouseDatabase as ClickHouseDatabase
    from doeff_core_effects._clickhouse_http_sql import clickhouse_answer as clickhouse_answer
    from doeff_core_effects._clickhouse_http_sql import (
        clickhouse_http_sql_handler as clickhouse_http_sql_handler,
    )
    from doeff_core_effects._clickhouse_http_sql import (
        clickhouse_query_request as clickhouse_query_request,
    )
    from doeff_core_effects._clickhouse_http_sql import (
        clickhouse_schema_statements as clickhouse_schema_statements,
    )

_LAZY_EXPORTS = {
    "HttpError": "doeff_core_effects._effects",
    "HttpRequest": "doeff_core_effects._effects",
    "HttpResponse": "doeff_core_effects._effects",
    "http_fixture_handler": "doeff_core_effects._http_handlers",
    "http_production_handler": "doeff_core_effects._http_handlers",
    "ProcessStepBudget": "doeff_core_effects._scheduler",
    "SchedulerImplementation": "doeff_core_effects._scheduler",
    "SchedulerIndexDriftError": "doeff_core_effects._scheduler",
    "SchedulerIntrospection": "doeff_core_effects._scheduler",
    "SpentStepBudget": "doeff_core_effects._scheduler",
    "StepBudget": "doeff_core_effects._scheduler",
    "StepBudgetExceeded": "doeff_core_effects._scheduler",
    "TaskCompleted": "doeff_core_effects._scheduler",
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
    # 外の repo が使う program の helper(ADR-DOE-API-001 R18・R25)
    "file_done": "doeff_core_effects._file_effects",
    "computed": "doeff_core_effects._inline_compute",
    "render_prometheus": "doeff_core_effects._meter_prometheus",
    "offloaded": "doeff_core_effects._offloaded_call",
    "checked_hash_pyc": "doeff_core_effects._python_bytecode",
    "compiled_pyc": "doeff_core_effects._python_bytecode",
    "seeded_bytes": "doeff_core_effects._seeded_random",
    "stack_dumps_at": "doeff_core_effects._stack_dump_effects",
    "AppendHttpScript": "doeff_core_effects._http_server_effects",
    "HttpAddress": "doeff_core_effects._http_server_effects",
    "HttpBody": "doeff_core_effects._http_server_effects",
    "HttpBodyBytes": "doeff_core_effects._http_server_effects",
    "HttpBodyFailed": "doeff_core_effects._http_server_effects",
    "HttpBodyFileRange": "doeff_core_effects._http_server_effects",
    "HttpBodyOutcome": "doeff_core_effects._http_server_effects",
    "HttpBodyRead": "doeff_core_effects._http_server_effects",
    "HttpBodyTooLarge": "doeff_core_effects._http_server_effects",
    "HttpCommand": "doeff_core_effects._http_server_effects",
    "HttpEvent": "doeff_core_effects._http_server_effects",
    "HttpForward": "doeff_core_effects._http_server_effects",
    "HttpHeader": "doeff_core_effects._http_server_effects",
    "HttpListen": "doeff_core_effects._http_server_effects",
    "HttpNextRequest": "doeff_core_effects._http_server_effects",
    "HttpNoBody": "doeff_core_effects._http_server_effects",
    "HttpProbe": "doeff_core_effects._http_server_effects",
    "HttpProbeAnswer": "doeff_core_effects._http_server_effects",
    "HttpReadBody": "doeff_core_effects._http_server_effects",
    "HttpRequestArrived": "doeff_core_effects._http_server_effects",
    "HttpRespond": "doeff_core_effects._http_server_effects",
    "HttpScript": "doeff_core_effects._http_server_effects",
    "HttpServed": "doeff_core_effects._http_server_effects",
    "HttpServerClosed": "doeff_core_effects._http_server_effects",
    "HttpShutdown": "doeff_core_effects._http_server_effects",
    "HttpStopListening": "doeff_core_effects._http_server_effects",
    "HttpTakeQueued": "doeff_core_effects._http_server_effects",
    "ReadHttpServed": "doeff_core_effects._http_server_effects",
    "ScriptedBody": "doeff_core_effects._http_server_effects",
    "ScriptedUpstream": "doeff_core_effects._http_server_effects",
    "TakeWsSendReport": "doeff_core_effects._http_server_effects",
    "WsAccept": "doeff_core_effects._http_server_effects",
    "WsBinaryArrived": "doeff_core_effects._http_server_effects",
    "WsClose": "doeff_core_effects._http_server_effects",
    "WsCloseSent": "doeff_core_effects._http_server_effects",
    "WsClosed": "doeff_core_effects._http_server_effects",
    "WsEvent": "doeff_core_effects._http_server_effects",
    "WsForward": "doeff_core_effects._http_server_effects",
    "WsOpened": "doeff_core_effects._http_server_effects",
    "WsSendReport": "doeff_core_effects._http_server_effects",
    "WsSendText": "doeff_core_effects._http_server_effects",
    "WsSendTextEach": "doeff_core_effects._http_server_effects",
    "WsShortcut": "doeff_core_effects._http_server_effects",
    "WsShortcutAnswered": "doeff_core_effects._http_server_effects",
    "WsShortcutFailed": "doeff_core_effects._http_server_effects",
    "WsTextArrived": "doeff_core_effects._http_server_effects",
    "WsTextSent": "doeff_core_effects._http_server_effects",
    "ws_shortcut_for": "doeff_core_effects._http_server_effects",
    "ws_shortcut_outcome": "doeff_core_effects._http_server_effects",
    "WebEdge": "doeff_core_effects._aiohttp_http_server",
    "aiohttp_http_server": "doeff_core_effects._aiohttp_http_server",
    "ScriptSend": "doeff_core_effects._scripted_http_server",
    "script_sent": "doeff_core_effects._scripted_http_server",
    "scripted_http_server": "doeff_core_effects._scripted_http_server",
    "AwaitProcessExit": "doeff_core_effects._process_effects",
    "ChildLifetime": "doeff_core_effects._process_effects",
    "EnvEntry": "doeff_core_effects._process_effects",
    "EnvMode": "doeff_core_effects._process_effects",
    "ExecutableAt": "doeff_core_effects._process_effects",
    "InterpreterFacts": "doeff_core_effects._process_effects",
    "ModuleFound": "doeff_core_effects._process_effects",
    "ModuleNotFound": "doeff_core_effects._process_effects",
    "PollProcess": "doeff_core_effects._process_effects",
    "ProcessAlive": "doeff_core_effects._process_effects",
    "ProcessEnded": "doeff_core_effects._process_effects",
    "ProcessExited": "doeff_core_effects._process_effects",
    "ProcessInputWritten": "doeff_core_effects._process_effects",
    "ProcessNotChild": "doeff_core_effects._process_effects",
    "ProcessNotStarted": "doeff_core_effects._process_effects",
    "ProcessOutcome": "doeff_core_effects._process_effects",
    "ProcessRunning": "doeff_core_effects._process_effects",
    "ProcessSignal": "doeff_core_effects._process_effects",
    "ProcessSignalled": "doeff_core_effects._process_effects",
    "ProcessStarted": "doeff_core_effects._process_effects",
    "ReadEnvironment": "doeff_core_effects._process_effects",
    "ReadInterpreter": "doeff_core_effects._process_effects",
    "ReadMachineName": "doeff_core_effects._process_effects",
    "ResolveModule": "doeff_core_effects._process_effects",
    "RunProcess": "doeff_core_effects._process_effects",
    "SignalProcess": "doeff_core_effects._process_effects",
    "StartProcess": "doeff_core_effects._process_effects",
    "StopProcess": "doeff_core_effects._process_effects",
    "WorkingDirectory": "doeff_core_effects._process_effects",
    "WriteProcessInput": "doeff_core_effects._process_effects",
    "environment_answer": "doeff_core_effects._process_effects",
    "environment_mapping": "doeff_core_effects._process_effects",
    "executable_file_answer": "doeff_core_effects._process_effects",
    "not_started_outcome": "doeff_core_effects._process_effects",
    "start_refusal": "doeff_core_effects._process_effects",
    "timed_out_outcome": "doeff_core_effects._process_effects",
    "metered_offloaded_subprocess_handler": "doeff_core_effects._os_process",
    "offloaded_subprocess_handler": "doeff_core_effects._os_process",
    "os_executable_at": "doeff_core_effects._os_process",
    "os_process_alive": "doeff_core_effects._os_process",
    "subprocess_handler": "doeff_core_effects._os_process",
    "ProcessScript": "doeff_core_effects._scripted_process",
    "ScriptedCommand": "doeff_core_effects._scripted_process",
    "run_scripted": "doeff_core_effects._scripted_process",
    "scripted_command_peer": "doeff_core_effects._scripted_process",
    "scripted_process_handler": "doeff_core_effects._scripted_process",
    "ExitEnd": "doeff_core_effects._process_exit",
    "ExitWaitUnavailable": "doeff_core_effects._process_exit",
    "process_exit_handler": "doeff_core_effects._process_exit",
    "process_latest_handler": "doeff_core_effects._process_latest",
    "process_meter_handler": "doeff_core_effects._process_meter",
    "ProcRow": "doeff_core_effects._proc_table_effects",
    "ReadProcTable": "doeff_core_effects._proc_table_effects",
    "SignalPid": "doeff_core_effects._proc_table_effects",
    "ProcTableUnavailable": "doeff_core_effects._os_proc_table",
    "os_proc_table_handler": "doeff_core_effects._os_proc_table",
    "scripted_proc_table_handler": "doeff_core_effects._scripted_proc_table",
    "AwaitWarmChildExit": "doeff_core_effects._warm_effects",
    "ForkFromWarm": "doeff_core_effects._warm_effects",
    "PollWarmChild": "doeff_core_effects._warm_effects",
    "SignalWarmChild": "doeff_core_effects._warm_effects",
    "WarmExited": "doeff_core_effects._warm_effects",
    "WarmForked": "doeff_core_effects._warm_effects",
    "WarmGone": "doeff_core_effects._warm_effects",
    "WarmInputWritten": "doeff_core_effects._warm_effects",
    "WarmLost": "doeff_core_effects._warm_effects",
    "WarmRefused": "doeff_core_effects._warm_effects",
    "WarmRunning": "doeff_core_effects._warm_effects",
    "WarmSignaled": "doeff_core_effects._warm_effects",
    "WriteWarmChildInput": "doeff_core_effects._warm_effects",
    "HeldInput": "doeff_core_effects._os_warm_process",
    "WarmProcUnavailable": "doeff_core_effects._os_warm_process",
    "input_written": "doeff_core_effects._os_warm_process",
    "os_warm_process_handler": "doeff_core_effects._os_warm_process",
    "own_thread_count": "doeff_core_effects._os_warm_process",
    "proc_stat_of": "doeff_core_effects._os_warm_process",
    "warm_answer_line": "doeff_core_effects._os_warm_process",
    "warm_request_of": "doeff_core_effects._os_warm_process",
    "WarmRun": "doeff_core_effects._scripted_warm_process",
    "WarmScript": "doeff_core_effects._scripted_warm_process",
    "WarmSocket": "doeff_core_effects._scripted_warm_process",
    "WarmSocketAnswer": "doeff_core_effects._scripted_warm_process",
    "scripted_warm_process_handler": "doeff_core_effects._scripted_warm_process",
    "AwaitStop": "doeff_core_effects._stop_signal_effects",
    "RaiseStop": "doeff_core_effects._stop_signal_effects",
    "StopBell": "doeff_core_effects._stop_signal_effects",
    "StopRequested": "doeff_core_effects._stop_signal_effects",
    "os_signal_stop_handler": "doeff_core_effects._stop_signal_handlers",
    "scripted_stop_handler": "doeff_core_effects._stop_signal_handlers",
    "HttpFailed": "doeff_core_effects._http_effects",
    "HttpFailureKind": "doeff_core_effects._http_effects",
    "HttpPhases": "doeff_core_effects._http_effects",
    "ReadWsSent": "doeff_core_effects._ws_client_effects",
    "ScriptedWsBinary": "doeff_core_effects._ws_client_effects",
    "ScriptedWsClosed": "doeff_core_effects._ws_client_effects",
    "ScriptedWsEndpoint": "doeff_core_effects._ws_client_effects",
    "ScriptedWsFrame": "doeff_core_effects._ws_client_effects",
    "ScriptedWsReply": "doeff_core_effects._ws_client_effects",
    "ScriptedWsText": "doeff_core_effects._ws_client_effects",
    "WsBinary": "doeff_core_effects._ws_client_effects",
    "WsConnect": "doeff_core_effects._ws_client_effects",
    "WsConnectFailed": "doeff_core_effects._ws_client_effects",
    "WsConnectOutcome": "doeff_core_effects._ws_client_effects",
    "WsDisconnect": "doeff_core_effects._ws_client_effects",
    "WsDisconnectAll": "doeff_core_effects._ws_client_effects",
    "WsFrame": "doeff_core_effects._ws_client_effects",
    "WsLink": "doeff_core_effects._ws_client_effects",
    "WsLinkClosed": "doeff_core_effects._ws_client_effects",
    "WsReceive": "doeff_core_effects._ws_client_effects",
    "WsScript": "doeff_core_effects._ws_client_effects",
    "WsSend": "doeff_core_effects._ws_client_effects",
    "WsSendOutcome": "doeff_core_effects._ws_client_effects",
    "WsSent": "doeff_core_effects._ws_client_effects",
    "WsSentClose": "doeff_core_effects._ws_client_effects",
    "WsSentRecord": "doeff_core_effects._ws_client_effects",
    "WsSentText": "doeff_core_effects._ws_client_effects",
    "WsText": "doeff_core_effects._ws_client_effects",
    "closing_links": "doeff_core_effects._ws_client_effects",
    "aiohttp_ws_link_handler": "doeff_core_effects._aiohttp_ws_client",
    "scripted_ws_link_handler": "doeff_core_effects._scripted_ws_client",
    "HostNotAllowed": "doeff_core_effects._http_host_gate",
    "http_host_gate": "doeff_core_effects._http_host_gate",
    "SetSqlOutage": "doeff_core_effects._sql_effects",
    "SqlBatch": "doeff_core_effects._sql_effects",
    "SqlColumn": "doeff_core_effects._sql_effects",
    "SqlColumnType": "doeff_core_effects._sql_effects",
    "SqlDropNotice": "doeff_core_effects._sql_effects",
    "SqlEnsureTables": "doeff_core_effects._sql_effects",
    "SqlFailed": "doeff_core_effects._sql_effects",
    "SqlHangNotice": "doeff_core_effects._sql_effects",
    "SqlIndex": "doeff_core_effects._sql_effects",
    "SqlInsertRows": "doeff_core_effects._sql_effects",
    "SqlLock": "doeff_core_effects._sql_effects",
    "SqlNotify": "doeff_core_effects._sql_effects",
    "SqlParam": "doeff_core_effects._sql_effects",
    "SqlQuery": "doeff_core_effects._sql_effects",
    "SqlRows": "doeff_core_effects._sql_effects",
    "SqlSchemaApplied": "doeff_core_effects._sql_effects",
    "SqlTable": "doeff_core_effects._sql_effects",
    "SqlTransaction": "doeff_core_effects._sql_effects",
    "SqlTransactionMisuse": "doeff_core_effects._sql_effects",
    "SqlUnreachable": "doeff_core_effects._sql_effects",
    "SqlValue": "doeff_core_effects._sql_effects",
    "PostgresConnections": "doeff_core_effects._postgres_sql",
    "PostgresDatabase": "doeff_core_effects._postgres_sql",
    "PostgresTimeouts": "doeff_core_effects._postgres_sql",
    "TransactionAbandoned": "doeff_core_effects._postgres_sql",
    "postgres_sql_handler": "doeff_core_effects._postgres_sql",
    "pooled_postgres_sql_handler": "doeff_core_effects._pooled_postgres_sql",
    "SqliteConnection": "doeff_core_effects._sqlite_sql",
    "sqlite_connection": "doeff_core_effects._sqlite_sql",
    "sqlite_insert": "doeff_core_effects._sqlite_sql",
    "sqlite_query": "doeff_core_effects._sqlite_sql",
    "sqlite_sql_handler": "doeff_core_effects._sqlite_sql",
    "SqliteFile": "doeff_core_effects._sqlite_file_sql",
    "SqliteFiles": "doeff_core_effects._sqlite_file_sql",
    "close_sqlite_files": "doeff_core_effects._sqlite_file_sql",
    "open_sqlite_files": "doeff_core_effects._sqlite_file_sql",
    "sqlite_file_sql_handler": "doeff_core_effects._sqlite_file_sql",
    "ClickHouseDatabase": "doeff_core_effects._clickhouse_http_sql",
    "clickhouse_answer": "doeff_core_effects._clickhouse_http_sql",
    "clickhouse_http_sql_handler": "doeff_core_effects._clickhouse_http_sql",
    "clickhouse_query_request": "doeff_core_effects._clickhouse_http_sql",
    "clickhouse_schema_statements": "doeff_core_effects._clickhouse_http_sql",
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
