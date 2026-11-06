# doeff_hy._static_stub generated these type declarations; do not edit by hand (source = _meter_prometheus.hy; regenerate with python -m doeff_hy._static_stub --write <the .hy next to this .pyi>)

from _typeshed import Incomplete
from doeff import Program as _Program
from doeff_hy._frozen import FrozenMap as FrozenMap
from doeff_core_effects._meter_effects import MeterSnapshot as MeterSnapshot
CONTENT_TYPE: str
_HELP_ESCAPES: Incomplete

def render_prometheus(snapshot: MeterSnapshot, helps: FrozenMap[str]) -> _Program[str, object]:
    ...
