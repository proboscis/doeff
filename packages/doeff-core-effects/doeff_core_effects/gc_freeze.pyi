# doeff_hy.static_stub が作った型の宣言 — 手で直さない(元 = gc_freeze.hy・作り直し = python -m doeff_hy.static_stub --write <この .pyi の隣の .hy>)

from doeff_hy.static_types import Handler as _Handler
from doeff_core_effects.heap_effects import CollectAndFreeze as CollectAndFreeze
from doeff import Pass as Pass
from doeff_vm import WithHandler as WithHandler
gc_freeze_handler: _Handler
