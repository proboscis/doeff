# doeff_hy._static_stub が作った型の宣言 — 手で直さない(元 = _seeded_random.hy・作り直し = python -m doeff_hy._static_stub --write <この .pyi の隣の .hy>)

from doeff import Program as _Program
from doeff_hy._static_types import Handler as _Handler
from doeff_core_effects._random_effects import RandomBytes as RandomBytes

def seeded_bytes(seed: int, call: int, count: int) -> _Program[bytes, object]:
    ...

def seeded_random_handler(seed: int) -> _Handler:
    ...
