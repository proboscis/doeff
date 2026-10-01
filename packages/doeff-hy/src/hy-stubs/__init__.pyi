"""hy の公開面の型(型検査のための宣言 — 実行時は hy を読む・doeff-hy が配る部分の stub・agora-redesign #2313)。

hy は型の宣言(py.typed・stub)を持たないので、pyright strict は Hy の展開が必ず出す `import hy`・`import hy.models` に
「Stub file not found」(reportMissingTypeStubs)を出していた — 書き手に直せない赤が .hy の file ごとに 1〜2 件。
ここは hy の module の名前空間(`hy.<名>` で読める名)だけを宣言する。hy.models は models.pyi。
宣言の無い下の module(hy.compiler・hy.macros・hy.reader など)は、この stub の印(py.typed の partial)により
pyright が hy 本体の source から読む。

hy の __init__ は名を読まれた時に下の module から取る(_jit_imports)。ここの宣言はその先の関数の型。
"""

from collections.abc import Callable
from types import ModuleType
from typing import TextIO, TypeVar, overload

# `import hy` が実行時に読み込む下の module(hy/__init__ の hy.importer から連なる)— `import hy` だけで `hy.models.Symbol`・
# `hy.macros.require` と読めるので、属性として再公開する。models は models.pyi・他は hy の source(partial)。
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
    """`hy.I` の型 — `(hy.I.math.sqrt 2)`・`(hy.I "math")` で module を import して返す。"""

    def __call__(self, module_name: str) -> ModuleType: ...
    def __getattr__(self, s: str) -> ModuleType: ...

I: _Importer

#: hy/pyops.hy(Hy の module — 演算子の関数)。
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
