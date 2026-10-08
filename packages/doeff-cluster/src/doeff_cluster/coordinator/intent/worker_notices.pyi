# doeff_hy.static_stub が作った型の宣言 — 手で直さない(元 = worker_notices.hy・作り直し = python -m doeff_hy.static_stub --write <この .pyi の隣の .hy>)

from dataclasses import dataclass as dataclass

@dataclass(frozen=True, kw_only=True)
class WorkerGone:
    worker: str
    boot: str | None
    deadline_ms: int

@dataclass(frozen=True, kw_only=True)
class WorkerBack:
    worker: str
    boot: str | None
    seen_ms: int
