# doeff_hy.static_stub が作った型の宣言 — 手で直さない(元 = main.hy・作り直し = python -m doeff_hy.static_stub --write <この .pyi の隣の .hy>)

from doeff import Program as _Program
from pathlib import Path as Path
from dataclasses import replace as replace
from doeff import run as run
from doeff import with_handlers as with_handlers
from doeff_time import sync_time_handler as sync_time_handler
from doeff_cluster.shared.core.clock import now_epoch_ms as now_epoch_ms
from doeff_core_effects.effects import slog as slog
from doeff_core_effects.file_effects import FileFailed as FileFailed
from doeff_core_effects.file_effects import ListDirectory as ListDirectory
from doeff_core_effects.file_effects import PathKind as PathKind
from doeff_core_effects.file_effects import ReadText as ReadText
from doeff_core_effects.file_effects import StatPath as StatPath
from doeff_core_effects.file_effects import file_done as file_done
from doeff_core_effects.handlers import slog_handler as slog_handler
from doeff_core_effects.os_file import os_file_handler as os_file_handler
from doeff_core_effects.os_process import subprocess_handler as subprocess_handler
from doeff_core_effects.process_effects import EnvEntry as EnvEntry
from doeff_core_effects.process_effects import ReadEnvironment as ReadEnvironment
from doeff_cluster.shared.core.launch_rules import coordinator_commit_env_name as coordinator_commit_env_name
from doeff_core_effects.scheduler import scheduled as scheduled
from doeff_cluster.shared.intent.protocol import ClusterTiming as ClusterTiming
from doeff_cluster.coordinator.intent.cluster_model import ClusterState as ClusterState
from doeff_cluster.coordinator.intent.cluster_model import ACCEPTED_FORMATS as ACCEPTED_FORMATS
from doeff_cluster.coordinator.protocol.cluster_json import naming_from_json as naming_from_json
from doeff_cluster.coordinator.core.cluster_policy import fresh_task_prefix as fresh_task_prefix
from doeff_cluster.coordinator.protocol.state_json import state_from_json as state_from_json
from doeff_cluster.coordinator.protocol.durable_kv import full_kv as full_kv
from doeff_cluster.coordinator.protocol.durable_kv import state_from_kv as state_from_kv
from doeff_cluster.coordinator.protocol.durable_kv import legacy_key_moves as legacy_key_moves
from doeff_cluster.coordinator.protocol.durable_kv import resume_writes as resume_writes
from doeff_cluster.foundation.wal_store import WalStore as WalStore
from doeff_cluster.coordinator.protocol.store import DurableStore as DurableStore
from doeff_cluster.coordinator.protocol.store import durable_exists as durable_exists
from doeff_cluster.coordinator.protocol.store import durable_load as durable_load
from doeff_cluster.coordinator.protocol.store import durable_persist as durable_persist
from doeff_cluster.coordinator.protocol.store import durable_checkpoint as durable_checkpoint
from doeff_cluster.coordinator.core.api_policy import resume_after_downtime as resume_after_downtime
from doeff_cluster.coordinator.core.resource_policy import adopt_legacy as adopt_legacy
from doeff_cluster.coordinator.protocol.kube import ObjectWatches as ObjectWatches
from doeff_cluster.coordinator.protocol.kube import kube_api as kube_api
from doeff_cluster.coordinator.protocol.kube import kube_unavailable as kube_unavailable
from doeff_cluster.foundation.kube_client import KubeClient as KubeClient
from doeff_cluster.coordinator.intent.kube_model import KubeUnavailable as KubeUnavailable
from doeff_cluster.foundation.coordinator_inbox import RequestInbox as RequestInbox
from doeff_cluster.foundation.coordinator_inbox import StopState as StopState
from doeff_cluster.foundation.coordinator_inbox import stop_on_signals as stop_on_signals
from doeff_cluster.coordinator.entry.handler_sets import memory_notices as memory_notices
from doeff_cluster.coordinator.entry.handler_sets import production_handlers as production_handlers
from doeff_cluster.coordinator.entry.handler_sets import redis_notices as redis_notices
from doeff_events import MemoryBroker as MemoryBroker
from doeff_cluster.coordinator.core.program import run_coordinator as run_coordinator

def board_file_rows(board_dir: str) -> _Program[tuple, object]:
    ...

def legacy_state(state_file: str, now: int) -> _Program[ClusterState | None, object]:
    ...

def load_state(state_file: str, store: DurableStore, now: int) -> _Program[ClusterState, object]:
    ...

def running_commit() -> _Program[str | None, object]:
    ...

def with_running_commit(state: ClusterState) -> _Program[ClusterState, object]:
    ...

def state_on_start(state_file: str, store: DurableStore) -> _Program[ClusterState, object]:
    ...

def main() -> None:
    ...
