# doeff_hy._static_stub が作った型の宣言 — 手で直さない(元 = main.hy・作り直し = python -m doeff_hy._static_stub --write <この .pyi の隣の .hy>)

from doeff import Program as _Program
from pathlib import Path as Path
from dataclasses import replace as replace
from doeff import run as run
from doeff import with_handlers as with_handlers
from doeff_time import sync_time_handler as sync_time_handler
from doeff_cluster.shared.core.clock import now_epoch_ms as now_epoch_ms
from doeff_core_effects import slog as slog
from doeff_core_effects import FileFailed as FileFailed
from doeff_core_effects import ListDirectory as ListDirectory
from doeff_core_effects import PathKind as PathKind
from doeff_core_effects import ReadText as ReadText
from doeff_core_effects import StatPath as StatPath
from doeff_core_effects._file_effects import file_done as file_done
from doeff_core_effects import slog_handler as slog_handler
from doeff_core_effects import os_file_handler as os_file_handler
from doeff_core_effects import subprocess_handler as subprocess_handler
from doeff_core_effects import EnvEntry as EnvEntry
from doeff_core_effects import ReadEnvironment as ReadEnvironment
from doeff_cluster.shared.core.launch_rules import coordinator_commit_env_name as coordinator_commit_env_name
from doeff_core_effects import scheduled as scheduled
from doeff_cluster.shared.intent.protocol import ClusterTiming as ClusterTiming
from doeff_cluster._coordinator.intent.cluster_model import ClusterState as ClusterState
from doeff_cluster._coordinator.intent.cluster_model import ACCEPTED_FORMATS as ACCEPTED_FORMATS
from doeff_cluster._coordinator.protocol.cluster_json import naming_from_json as naming_from_json
from doeff_cluster._coordinator.core.cluster_policy import fresh_task_prefix as fresh_task_prefix
from doeff_cluster._coordinator.protocol.state_json import state_from_json as state_from_json
from doeff_cluster._coordinator.protocol.durable_kv import full_kv as full_kv
from doeff_cluster._coordinator.protocol.durable_kv import state_from_kv as state_from_kv
from doeff_cluster._coordinator.protocol.durable_kv import legacy_key_moves as legacy_key_moves
from doeff_cluster._coordinator.protocol.durable_kv import resume_writes as resume_writes
from doeff_cluster._foundation.wal_store import WalStore as WalStore
from doeff_cluster._coordinator.protocol.store import DurableStore as DurableStore
from doeff_cluster._coordinator.protocol.store import durable_exists as durable_exists
from doeff_cluster._coordinator.protocol.store import durable_load as durable_load
from doeff_cluster._coordinator.protocol.store import durable_persist as durable_persist
from doeff_cluster._coordinator.protocol.store import durable_checkpoint as durable_checkpoint
from doeff_cluster._coordinator.core.api_policy import resume_after_downtime as resume_after_downtime
from doeff_cluster._coordinator.core.resource_policy import adopt_legacy as adopt_legacy
from doeff_cluster._coordinator.protocol.kube import kube_api as kube_api
from doeff_cluster._coordinator.protocol.kube import kube_unavailable as kube_unavailable
from doeff_cluster._foundation.kube_client import kube_available as kube_available
from doeff_cluster._foundation.kube_client import kube_client as kube_client
from doeff_cluster._coordinator.intent.kube_model import KubeUnavailable as KubeUnavailable
from doeff_cluster._foundation.coordinator_inbox import RequestInbox as RequestInbox
from doeff_cluster._foundation.coordinator_inbox import StopState as StopState
from doeff_cluster._foundation.coordinator_inbox import stop_on_signals as stop_on_signals
from doeff_cluster._coordinator.entry.handler_sets import memory_notices as memory_notices
from doeff_cluster._coordinator.entry.handler_sets import production_handlers as production_handlers
from doeff_cluster._coordinator.entry.handler_sets import redis_notices as redis_notices
from doeff_events import MemoryBroker as MemoryBroker
from doeff_cluster.worker.entry.line_stamp import COORDINATOR_LOG_ENV_NAMES as COORDINATOR_LOG_ENV_NAMES
from doeff_cluster.worker.entry.line_stamp import SHIP_LIMITS as SHIP_LIMITS
from doeff_cluster.worker.entry.line_stamp import coordinator_log_plan as coordinator_log_plan
from doeff_cluster.worker.entry.line_stamp import process_lines as process_lines
from doeff_cluster._coordinator.core.program import run_coordinator as run_coordinator
LOG_END_SECONDS: float

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

def log_environment() -> _Program[dict, object]:
    ...

def state_on_start(state_file: str, store: DurableStore) -> _Program[ClusterState, object]:
    ...

def main() -> None:
    ...
