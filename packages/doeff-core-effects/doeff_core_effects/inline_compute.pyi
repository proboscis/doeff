# doeff_hy.static_stub が作った型の宣言 — 手で直さない(元 = inline_compute.hy・作り直し = python -m doeff_hy.static_stub --write <この .pyi の隣の .hy>)

from doeff import Program as _Program
from doeff_hy.static_types import Handler as _Handler
from doeff import DoExpr as DoExpr
from doeff import PyVM as PyVM
from doeff_core_effects.compute_effects import Compute as Compute
from doeff_core_effects.compute_effects import Computed as Computed
from doeff_core_effects.compute_effects import ComputeFailed as ComputeFailed
from doeff_core_effects.compute_effects import ComputeLoad as ComputeLoad
from doeff_core_effects.compute_effects import ReadComputeLoad as ReadComputeLoad
INLINE_LOAD: ComputeLoad

def computed(program: DoExpr) -> _Program[Computed | ComputeFailed, object]:
    ...
inline_compute_handler: _Handler
