# doeff_hy.static_stub が作った型の宣言 — 手で直さない(元 = launch_rules.hy・作り直し = python -m doeff_hy.static_stub --write <この .pyi の隣の .hy>)

from doeff import Program as _Program
from dataclasses import dataclass as dataclass
from enum import StrEnum as StrEnum
from doeff_core_effects.process_effects import EnvEntry as EnvEntry
from doeff_cluster.shared.intent.launch_model import WorkerLaunch as WorkerLaunch
from doeff_cluster.shared.intent.launch_model import CoordinatorLaunch as CoordinatorLaunch

class LaunchFieldKind(StrEnum):
    TEXT = 'text'
    NAMES = 'names'
    COUNT = 'count'

@dataclass(frozen=True, kw_only=True)
class LaunchField:
    env_name: str
    field: str
    kind: LaunchFieldKind
    optional: bool
WORKER_LAUNCH_FIELDS: tuple[LaunchField, ...]
COORDINATOR_LAUNCH_FIELDS: tuple[LaunchField, ...]

def worker_launch_names() -> _Program[frozenset[str], object]:
    ...

def coordinator_launch_names() -> _Program[frozenset[str], object]:
    ...

class LaunchEnvMissing(ValueError):
    ...

def shown_value(field: LaunchField, value: str | int | tuple) -> _Program[str, object]:
    ...

def read_value(field: LaunchField, text: str) -> _Program[str | int | tuple, object]:
    ...

def launch_env(table: tuple[LaunchField, ...], launch: WorkerLaunch | CoordinatorLaunch) -> _Program[tuple[EnvEntry, ...], object]:
    ...

def launch_values(table: tuple[LaunchField, ...], env: tuple[EnvEntry, ...]) -> _Program[dict, object]:
    ...

def worker_launch_env(launch: WorkerLaunch) -> _Program[tuple[EnvEntry, ...], object]:
    ...

def worker_launch_of_env(env: tuple[EnvEntry, ...]) -> _Program[WorkerLaunch, object]:
    ...

def coordinator_launch_env(launch: CoordinatorLaunch) -> _Program[tuple[EnvEntry, ...], object]:
    ...

def coordinator_launch_of_env(env: tuple[EnvEntry, ...]) -> _Program[CoordinatorLaunch, object]:
    ...
