# doeff_hy.static_stub が作った型の宣言 — 手で直さない(元 = shim_timing.hy・作り直し = python -m doeff_hy.static_stub --write <この .pyi の隣の .hy>)

from doeff import Program as _Program
from dataclasses import dataclass as dataclass
from doeff_cluster.worker.intent.worker_model import WorkerPolicy as WorkerPolicy

@dataclass(frozen=True, kw_only=True)
class ShimSpans:
    shim_grace_ms: int
    sweep_margin_ms: int
    stop_grace_ms: int

@dataclass(frozen=True, kw_only=True)
class ShimOutlastsTheKill:
    deadline_ms: int
    kill_ms: int
    spans: ShimSpans

def shim_spans(policy: WorkerPolicy) -> _Program[ShimSpans, object]:
    ...

def shim_deadline_ms(spans: ShimSpans) -> _Program[int, object]:
    ...

def shim_ends_before_the_kill(spans: ShimSpans) -> _Program[tuple[ShimOutlastsTheKill, ...], object]:
    ...
