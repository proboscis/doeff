# doeff_hy._static_stub generated these type declarations; do not edit by hand (source = _inline_compute.hy; regenerate with python -m doeff_hy._static_stub --write <the .hy next to this .pyi>)

from doeff import Program as _Program
from doeff_hy._static_types import Handler as _Handler
from doeff import DoExpr as DoExpr
from doeff import PyVM as PyVM
from doeff_core_effects._compute_effects import Compute as Compute
from doeff_core_effects._compute_effects import Computed as Computed
from doeff_core_effects._compute_effects import ComputeFailed as ComputeFailed
from doeff_core_effects._compute_effects import ComputeLoad as ComputeLoad
from doeff_core_effects._compute_effects import ReadComputeLoad as ReadComputeLoad
INLINE_LOAD: ComputeLoad

def computed(program: DoExpr) -> _Program[Computed | ComputeFailed, object]:
    ...
inline_compute_handler: _Handler
