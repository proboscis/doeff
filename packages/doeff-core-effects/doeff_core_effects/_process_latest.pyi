# doeff_hy._static_stub generated these type declarations; do not edit by hand (source = _process_latest.hy; regenerate with python -m doeff_hy._static_stub --write <the .hy next to this .pyi>)

from _typeshed import Incomplete
from doeff import Program as _Program
from doeff_hy._static_types import Handler as _Handler
from functools import partial as partial
from doeff_core_effects._latest_effects import PublishLatest as PublishLatest
from doeff_core_effects._latest_effects import ReadLatest as ReadLatest
from doeff_core_effects._latest_effects import AwaitLatest as AwaitLatest
from doeff_core_effects._scheduler import CreateExternalPromise as CreateExternalPromise
from doeff_core_effects._scheduler import ExternalPromise as ExternalPromise
from doeff_core_effects._scheduler import Wait as Wait
from doeff_core_effects._scheduler import TaskPriority as TaskPriority
BOARDS: Incomplete
BOARDS_LOCK: Incomplete
BELLS: Incomplete
BELLS_LOCK: Incomplete

def latest_board(name: str) -> _Program[dict, object]:
    ...

def latest_bells(name: str) -> _Program[dict, object]:
    ...

def hang_bell(bells: dict, kind: type, bell: ExternalPromise) -> _Program[None, object]:
    ...

def unhang_bell(bells: dict, kind: type, bell: ExternalPromise) -> None:
    ...

def ring_bells(bells: dict, kind: type) -> _Program[None, object]:
    ...

def process_latest_handler(name: str) -> _Handler:
    ...
