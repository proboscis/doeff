# doeff_hy._static_stub generated these type declarations; do not edit by hand (source = _darwin_volume.hy; regenerate with python -m doeff_hy._static_stub --write <the .hy next to this .pyi>)

from _typeshed import Incomplete
from doeff import Program as _Program
import ctypes as ctypes
CORE_FOUNDATION_PATH: str
VOLUME_FREE_KEY: str
CF_NUMBER_SINT64: int
URL_OF_PATH: Incomplete
COPY_PROPERTY: Incomplete
NUMBER_VALUE: Incomplete
RELEASE: Incomplete

def number_value(library: ctypes.CDLL, number: int) -> _Program[int | None, object]:
    ...

def volume_property(library: ctypes.CDLL, url: int) -> _Program[int | None, object]:
    ...

def darwin_volume_free(path: str) -> _Program[int | None, object]:
    ...
