# doeff_hy.static_stub が作った型の宣言 — 手で直さない(元 = env_marker_model.hy・作り直し = python -m doeff_hy.static_stub --write <この .pyi の隣の .hy>)

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
