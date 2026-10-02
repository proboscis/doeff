# doeff_hy.static_stub が作った型の宣言 — 手で直さない(元 = env_marker_model.hy・作り直し = python -m doeff_hy.static_stub --write <この .pyi の隣の .hy>)

from doeff import EffectBase as _doeff_effect_base
from dataclasses import dataclass as _doeff_dataclass
ENV_MARKER: str
ENV_MARKER_FORMAT: int

@_doeff_dataclass(frozen=True)
class FileSha256(_doeff_effect_base[str | None]):
    path: str
