# doeff_hy._static_stub generated these type declarations; do not edit by hand (source = shim_timing.hy; regenerate with python -m doeff_hy._static_stub --write <the .hy next to this .pyi>)

from doeff import Program as _Program
from dataclasses import dataclass as dataclass
from doeff_cluster._worker.intent.worker_model import WorkerPolicy as WorkerPolicy

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
