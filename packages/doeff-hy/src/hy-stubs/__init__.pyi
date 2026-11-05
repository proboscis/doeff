"""Types for the public surface of hy (declarations for type checking — at runtime hy is read; a stub for the parts doeff-hy ships).

hy has no type declarations (py.typed or stubs), so pyright strict reported "Stub file not found" (reportMissingTypeStubs) on the `import hy` and `import hy.models` that the Hy expansion always emits —
1 to 2 reds per .hy file that the writer could not fix.
This declares only the namespace of the hy module (names readable as `hy.<name>`). hy.models is in models.pyi.
Undeclared lower modules (hy.compiler, hy.macros, hy.reader, and so on) are read by pyright from the hy source itself, thanks to this stub's mark (partial in py.typed).

hy's __init__ fetches names from lower modules when they are read (_jit_imports). The declarations here are the types of the functions behind that.
"""

from collections.abc import Callable
from types import ModuleType
from typing import TextIO, TypeVar, overload

# Lower modules that `import hy` loads at runtime (chained from hy.importer in hy/__init__) — `import hy` alone lets you read `hy.models.Symbol` and
# `hy.macros.require`, so they are re-exported as attributes. models is models.pyi; the others are hy's source (partial).
from hy import compat as compat
from hy import compiler as compiler
from hy import core as core
from hy import errors as errors
from hy import hy_inspect as hy_inspect
from hy import importer as importer
from hy import macros as macros
from hy import model_patterns as model_patterns
from hy import models as models
from hy import reader as reader
from hy import scoping as scoping
from hy.models import Lazy, Object, Symbol
from hy.models import as_model as as_model
from hy.reader.exceptions import PrematureEndOfInput as PrematureEndOfInput
from hy.reader.hy_reader import HyReader as HyReader
from hy.reader.reader import Reader as Reader
from hy.repl import REPL as REPL

_T = TypeVar("_T")

__version__: str
nickname: str
last_version: str

class _Importer:
    """The type of `hy.I` — imports and returns a module with `(hy.I.math.sqrt 2)` or `(hy.I "math")`."""

    def __call__(self, module_name: str) -> ModuleType: ...
    def __getattr__(self, s: str) -> ModuleType: ...

I: _Importer

#: hy/pyops.hy (a Hy module — operator functions).
pyops: ModuleType

def mangle(s: object) -> str: ...
def unmangle(s: object) -> str: ...
def read(stream: str | TextIO, filename: str | None = None, reader: HyReader | None = None) -> Object: ...
def read_many(
    stream: str | TextIO, filename: str = "<string>", reader: HyReader | None = None, skip_shebang: bool = False
) -> Lazy: ...
def eval(
    model: object,
    globals: dict[str, object] | None = None,
    locals: dict[str, object] | None = None,
    module: ModuleType | str | None = None,
    macros: dict[str, Callable[..., object]] | None = None,
) -> object: ...
def repr(obj: object) -> str: ...
@overload
def repr_register(types: type[_T], f: Callable[[_T], str], placeholder: str | None = None) -> None: ...
@overload
def repr_register(
    types: tuple[type, ...] | list[type], f: Callable[[object], str], placeholder: str | None = None
) -> None: ...
def gensym(g: str = "") -> Symbol: ...
def macroexpand(
    model: Object, module: ModuleType | str | None = None, macros: dict[str, Callable[..., object]] | None = None
) -> Object: ...
def macroexpand_1(
    model: Object, module: ModuleType | str | None = None, macros: dict[str, Callable[..., object]] | None = None
) -> Object: ...
