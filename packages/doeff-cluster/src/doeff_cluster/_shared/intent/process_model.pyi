# doeff_hy._static_stub generated these type declarations; do not edit by hand (source = process_model.hy; regenerate with python -m doeff_hy._static_stub --write <the .hy next to this .pyi>)

from doeff import EffectBase as _doeff_effect_base
from dataclasses import dataclass as _doeff_dataclass
from dataclasses import dataclass as dataclass

@dataclass(frozen=True, kw_only=True)
class ProcessEnded:
    job: str
    instance: str
    worker: str

@dataclass(frozen=True, kw_only=True)
class ProcessWaitExpired:
    job: str
    waited_seconds: float

@_doeff_dataclass(frozen=True)
class AwaitProcessEnded(_doeff_effect_base[ProcessEnded | ProcessWaitExpired]):
    job: str
    timeout_seconds: float | None = None
