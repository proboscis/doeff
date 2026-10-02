# doeff_hy.static_stub が作った型の宣言 — 手で直さない(元 = seeded_random.hy・作り直し = python -m doeff_hy.static_stub --write <この .pyi の隣の .hy>)

from doeff import Program as _Program
from doeff_hy.static_types import Handler as _Handler
import hashlib as hashlib
from doeff_core_effects.random_effects import RandomBytes as RandomBytes
from doeff import Pass as Pass
from doeff_vm import WithHandler as WithHandler
from doeff import Some as Some
from doeff_core_effects.effects import Put as Put

def seeded_bytes(seed: int, call: int, count: int) -> _Program[bytes, object]:
    ...

def seeded_random_handler(seed: int) -> _Handler:
    ...
