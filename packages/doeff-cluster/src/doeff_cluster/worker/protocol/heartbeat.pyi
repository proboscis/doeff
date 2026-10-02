# doeff_hy.static_stub が作った型の宣言 — 手で直さない(元 = heartbeat.hy・作り直し = python -m doeff_hy.static_stub --write <この .pyi の隣の .hy>)

from doeff import Program as _Program
from doeff_cluster.shared.intent.protocol import PROTOCOL_FORMAT as PROTOCOL_FORMAT
from doeff_cluster.worker.intent.worker_model import CodeState as CodeState
from doeff_cluster.worker.intent.worker_model import CodeView as CodeView
from doeff_cluster.worker.intent.worker_model import JobStatus as JobStatus
from doeff_cluster.worker.core.worker_rules import ENV_KEY_PREFIX as ENV_KEY_PREFIX
from doeff_cluster.worker.core.heartbeat_rules import finished_task_id as finished_task_id

def env_report(views: tuple[CodeView, ...], capacity: str) -> dict[str, object]:
    ...

def env_heartbeat_part(report: dict[str, object], platform: str) -> dict[str, object]:
    ...

def heartbeat_body(*, name: str, provides: tuple[str, ...], exclusive: tuple[str, ...], node: str, capacity: int, versions: dict[str, str], statuses: list[dict[str, object]], endpoint: str, boot: str, boot_at: int, tools: dict[str, object], kept: tuple[str, ...], stopping: bool=False) -> _Program[dict[str, object], object]:
    ...

def status_report(statuses: tuple[JobStatus, ...], task_echo: dict[str, dict[str, object]], results: dict[str, str | None]) -> _Program[list[dict[str, object]], object]:
    ...

def status_rows_json(statuses: tuple[JobStatus, ...]) -> _Program[tuple[dict[str, object], ...], object]:
    ...

def status_row(s: JobStatus) -> _Program[dict[str, object], object]:
    ...
