# doeff_hy.static_stub が作った型の宣言 — 手で直さない(元 = process_latest.hy・作り直し = python -m doeff_hy.static_stub --write <この .pyi の隣の .hy>)

from _typeshed import Incomplete
from doeff_hy.static_types import Handler as _Handler
from doeff_core_effects.latest_effects import PublishLatest as PublishLatest
from doeff_core_effects.latest_effects import ReadLatest as ReadLatest
from doeff import Pass as Pass
from doeff_vm import WithHandler as WithHandler
from doeff import Some as Some
from doeff_core_effects.effects import Put as Put
BOARDS: Incomplete
BOARDS_LOCK: Incomplete

def latest_board(name: Incomplete) -> Incomplete:
    ...

def process_latest_handler(name: str) -> _Handler:
    ...
