# doeff_hy.static_stub が作った型の宣言 — 手で直さない(元 = cluster_json.hy・作り直し = python -m doeff_hy.static_stub --write <この .pyi の隣の .hy>)

from doeff import Program as _Program
from doeff_hy.macros import _install_guard_globals as _install_guard_globals
from doeff_hy.macros import _guard_performed as _guard_performed
from dataclasses import asdict as asdict
from dataclasses import fields as fields
from doeff_cluster.coordinator.core.cluster_rules import component_versions_of as component_versions_of
from doeff_cluster.coordinator.intent.cluster_model import HandoffPhase as HandoffPhase
from doeff_cluster.coordinator.intent.cluster_model import HandoffWatch as HandoffWatch
from doeff_cluster.coordinator.intent.cluster_model import ClusterNaming as ClusterNaming
from doeff_cluster.coordinator.intent.cluster_model import TaskRecord as TaskRecord
from doeff_cluster.coordinator.intent.cluster_model import ENDED_PHASES as ENDED_PHASES
from doeff_cluster.shared.core.capabilities import capabilities_of as capabilities_of
from doeff_cluster.shared.core.capabilities import environ_pairs as environ_pairs

def handoff_watch_from_json(data: dict) -> HandoffWatch:
    ...
RETIRED_NAMING_FIELDS: frozenset[str]

def naming_from_json(text: str) -> ClusterNaming:
    ...

def task_record_to_json(task: TaskRecord) -> dict:
    ...

def task_record_from_json(data: dict[str, object]) -> _Program[TaskRecord, object]:
    ...

def stored_str(data: dict, key: str, default: str | None=None) -> str:
    ...

def stored_optional_str(data: dict, key: str) -> str | None:
    ...

def stored_int(data: dict, key: str, default: int | None=None) -> int:
    ...

def stored_optional_int(data: dict, key: str) -> int | None:
    ...

def stored_bool(data: dict, key: str, default: bool) -> bool:
    ...

def stored_optional_dict(data: dict, key: str) -> dict | None:
    ...

def stored_items(data: dict, key: str) -> tuple:
    ...

def old_task_row_reason(old: dict | list | None, extra: list) -> str | None:
    ...
