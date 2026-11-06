# doeff_hy._static_stub generated these type declarations; do not edit by hand (source = env_marker_model.hy; regenerate with python -m doeff_hy._static_stub --write <the .hy next to this .pyi>)

from doeff import EffectBase as _doeff_effect_base
from dataclasses import dataclass as _doeff_dataclass
from dataclasses import dataclass as dataclass
ENV_MARKER: str
ENV_MARKER_FORMAT: int

@dataclass(frozen=True, kw_only=True)
class TreeCounts:
    name: str
    stored: int
    rebuilt: int
    reused: int
    failed: int

@dataclass(frozen=True, kw_only=True)
class BytecodeCounts:
    stored: int
    rebuilt: int
    reused: int
    failed: int
    scan_seconds: float
    closure_seconds: float
    compile_seconds: float
    trees: tuple[TreeCounts, ...]

@_doeff_dataclass(frozen=True)
class FileSha256(_doeff_effect_base[str | None]):
    path: str
