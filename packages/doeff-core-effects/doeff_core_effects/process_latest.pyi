# doeff_hy.static_stub が作った型の宣言 — 手で直さない(元 = process_latest.hy・作り直し = python -m doeff_hy.static_stub --write <この .pyi の隣の .hy>)

from _typeshed import Incomplete
from doeff import Program as _Program
from doeff_hy.static_types import Handler as _Handler
from functools import partial as partial
from doeff_core_effects.latest_effects import PublishLatest as PublishLatest
from doeff_core_effects.latest_effects import ReadLatest as ReadLatest
from doeff_core_effects.latest_effects import AwaitLatest as AwaitLatest
from doeff_core_effects.scheduler import CreateExternalPromise as CreateExternalPromise
from doeff_core_effects.scheduler import ExternalPromise as ExternalPromise
from doeff_core_effects.scheduler import Wait as Wait
from doeff_core_effects.scheduler import PRIORITY_IDLE as PRIORITY_IDLE
from doeff import Pass as Pass
from doeff_vm import WithHandler as WithHandler
from doeff import Some as Some
from doeff_core_effects.effects import Put as Put
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
