# doeff_hy._static_stub generated these type declarations; do not edit by hand (source = worker_notices.hy; regenerate with python -m doeff_hy._static_stub --write <the .hy next to this .pyi>)

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
