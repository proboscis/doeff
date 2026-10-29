# doeff_hy.static_stub が作った型の宣言 — 手で直さない(元 = _meter_prometheus.hy・作り直し = python -m doeff_hy.static_stub --write <この .pyi の隣の .hy>)

from _typeshed import Incomplete
from doeff import Program as _Program
from doeff_hy._frozen import FrozenMap as FrozenMap
from doeff_core_effects._meter_effects import MeterSnapshot as MeterSnapshot
CONTENT_TYPE: str
_HELP_ESCAPES: Incomplete

def render_prometheus(snapshot: MeterSnapshot, helps: FrozenMap[str]) -> _Program[str, object]:
    ...
