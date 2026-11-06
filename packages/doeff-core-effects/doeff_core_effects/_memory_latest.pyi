# doeff_hy._static_stub generated these type declarations; do not edit by hand (source = _memory_latest.hy; regenerate with python -m doeff_hy._static_stub --write <the .hy next to this .pyi>)

from doeff_hy._static_types import Handler as _Handler
from dataclasses import dataclass as dataclass
from functools import partial as partial
from doeff_core_effects._latest_effects import PublishLatest as PublishLatest
from doeff_core_effects._latest_effects import ReadLatest as ReadLatest
from doeff_core_effects._latest_effects import AwaitLatest as AwaitLatest
from doeff_core_effects._scheduler import CreateExternalPromise as CreateExternalPromise
from doeff_core_effects._scheduler import ExternalPromise as ExternalPromise
from doeff_core_effects._scheduler import Wait as Wait
from doeff_core_effects._scheduler import TaskPriority as TaskPriority

@dataclass(frozen=True, kw_only=True)
class MemoryLatestStore:
    values: dict[type, object]
    bells: dict[type, tuple[ExternalPromise[object], ...]]

def drop_bell(bells: dict, kind: type, bell: ExternalPromise) -> None:
    ...
memory_latest_handler: _Handler
