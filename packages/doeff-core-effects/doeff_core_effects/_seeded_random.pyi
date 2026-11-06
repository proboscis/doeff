# doeff_hy._static_stub generated these type declarations; do not edit by hand (source = _seeded_random.hy; regenerate with python -m doeff_hy._static_stub --write <the .hy next to this .pyi>)

from doeff import Program as _Program
from doeff_hy._static_types import Handler as _Handler
from doeff_core_effects._random_effects import RandomBytes as RandomBytes

def seeded_bytes(seed: int, call: int, count: int) -> _Program[bytes, object]:
    ...

def seeded_random_handler(seed: int) -> _Handler:
    ...
