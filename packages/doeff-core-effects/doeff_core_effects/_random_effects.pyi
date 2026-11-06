# doeff_hy._static_stub generated these type declarations; do not edit by hand (source = _random_effects.hy; regenerate with python -m doeff_hy._static_stub --write <the .hy next to this .pyi>)

from doeff import EffectBase as _doeff_effect_base
from dataclasses import dataclass as _doeff_dataclass

@_doeff_dataclass(frozen=True)
class RandomBytes(_doeff_effect_base[bytes]):
    count: int
