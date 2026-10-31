"""doeff-hy の macro を使う Hy の module の誤り 2 つに、書き足す 1 行を警告で言う(取り組み doeff-publish)。

- **require し忘れ**: README の Hy の例から ``(require doeff-hy.macros [do! <-])`` を抜くと、Hy は ``do!`` を関数の呼び出しとして
  compile し、実行の時に ``NameError: name 'hyx_doXexclamation_markX' is not defined`` になる(Hy の仕様 — 未知の記号は関数の呼び出し)。
- **import で入れた**: ``(import doeff-hy.handle [defhandler])`` は macro を属性として探して ``ImportError: cannot import name
  'defhandler' …`` になる(macro は module の属性でなく macro の表に在る)。

どちらも文は Python が出し、通る frame に doeff の code が無い(``hy file.hy`` の落ちた時の表示は Hy が差す excepthook が出す)ので、
実行の前に言う: Hy の module を読む口(:mod:`doeff_hy_bytecode_guard._loader_hooks` の ``get_code`` — ``hy file.hy``・``hy -m``・
import・pytest の全部が通る)が返す code と記録を、:func:`warn_about_macro_use` が見る。

見分け方(誤警告を出さない事が先):

- require し忘れ = 記録の ``absent``(展開の時に module の macro の表を引いて無かった名 — 関数の呼び出しも入る)に doeff-hy の
  macro の名が在り、かつ module の code がその名を **束縛せずに読む**(LOAD_NAME・LOAD_GLOBAL が在り、STORE_NAME・STORE_GLOBAL が
  無い)。自分で ``(defn check …)`` と定義した・別の module から import した同名の関数は束縛が在るので警告しない。``import *``
  (IMPORT_STAR)が在る module は何が入るか分からないので警告しない。
- import で入れた = module の先頭の code が、提供元の module(``doeff_hy.macros`` など)から、macro の名を ``IMPORT_FROM`` で取る。
  提供元の module そのものの import・macro でない名の import は正しい(表の名が提供元の属性と重ならない事は検が守る)。

macro の名は ``MACRO_HOMES`` の 1 か所(提供元の module の名 → ``(require 提供元 *)`` が入れる名)で、実物の macro の表との一致は
packages/doeff-hy/tests/test_macro_hints.py が測る。doeff-hy の外の package の macro(doeff-conductor・doeff-cluster など — 持ち主の package が出す)は入れない。

venv の全ての Python の起動時に読まれる :mod:`doeff_hy_bytecode_guard` の一部だが、この module は Hy の module を初めて読む時に
読む(起動時に読まない — ``hy`` は名の表を作る時だけ読む)。正しい module では記録の名と co_names の照合だけで終わり、
bytecode を歩かない(macro と同じ綴りを局所の名にだけ使う module も)。
"""

import dis
import sys
import warnings
from collections.abc import Callable, Iterable, Iterator, Mapping
from dataclasses import dataclass
from functools import cache
from types import CodeType

from doeff_hy_bytecode_guard._records import MacroRecord


class HyMacroUseWarning(UserWarning):
    """doeff-hy の macro を require し損ねた(または import で入れた)Hy の module — 書き足す 1 行を言う。"""


#: macro の提供元の Hy の module の名 → ``(require 提供元 *)`` が入れる macro の名(Hy の綴り)。提供元の並びが、書き足す行の
#: 提供元の選び(名を入れる最初の提供元)を決める: ``defhandler`` は doeff-hy.macros も入れるので、そちらの 1 行にまとまる。
MACRO_HOMES: Mapping[str, tuple[str, ...]] = {
    "doeff-hy.macros": (
        "<-",
        "<->",
        "absent-as",
        "check",
        "defeffect",
        "deff",
        "defhandler",
        "defk",
        "defmcp-tool",
        "defp",
        "defpipeline",
        "defpp",
        "deftest",
        "do!",
        "fnk",
        "for/do",
        "handle",
        "lazy",
        "on-raise",
        "session",
        "set!",
        "traverse",
        "val",
        "validate",
        "var",
        "with-handler",
    ),
    "doeff-hy.handle": ("defhandler", "handle", "with-handler"),
    "doeff-hy.record": ("defenum", "deff", "defrecord", "defwire"),
}

#: 名を読む命令(束縛を引かない)・束縛する命令。局所の名(LOAD_FAST など)は module の名前空間の名でないので数えない。
_READS = frozenset({"LOAD_NAME", "LOAD_GLOBAL"})
_BINDS = frozenset({"STORE_NAME", "STORE_GLOBAL", "DELETE_NAME", "DELETE_GLOBAL"})


@dataclass(frozen=True)
class MacroName:
    """表の macro 1 つ: Hy の綴り・Python の名(Hy が mangle した形 — 記録と code に現れる)・書き足す require の提供元。"""

    spelling: str
    mangled: str
    home: str


@dataclass(frozen=True)
class MacroTable:
    """表を Hy の mangle で引き直した 2 つの見方: 提供元の Hy の名 → (Python の名 → macro)と、Python の名 → 書き足す提供元の macro。"""

    by_home: Mapping[str, Mapping[str, MacroName]]
    first_home: Mapping[str, MacroName]


@dataclass(frozen=True)
class Hint:
    """module の誤り 1 つ分の警告 — 使った行と、書き足す 1 行を含む文。"""

    line: int
    message: str


def macro_table() -> MacroTable | None:
    """:data:`MACRO_HOMES` を Hy の mangle で引き直した表を引くため(Hy の module を読む時にだけ作る)。

    ``import hy`` の最中は表を作れず None を返す: Hy は自分の core の .hy の module をその最中に読み込み、その読みもこの口を
    通るが、Hy の core に doeff-hy の macro は無く、``hy.mangle`` はまだ無い。
    """
    hy = sys.modules.get("hy")
    if hy is None or not hasattr(hy, "mangle"):
        return None
    return _mangled_table(hy.mangle)


@cache
def _mangled_table(mangle: Callable[[str], str]) -> MacroTable:
    """mangle 関数で :data:`MACRO_HOMES` を引き直した表を、process に 1 度だけ作るため。"""
    by_home = {
        home: {
            mangle(spelling): MacroName(spelling, mangle(spelling), home) for spelling in spellings
        }
        for home, spellings in MACRO_HOMES.items()
    }
    # 同じ名を入れる提供元が複数なら、前の提供元が勝つ(後ろから重ねる)。
    first_home = {
        mangled: macro
        for macros in reversed(tuple(by_home.values()))
        for mangled, macro in macros.items()
    }
    return MacroTable(by_home, first_home)


def warn_about_macro_use(code: CodeType, record: MacroRecord, path: str, fullname: str) -> None:
    """読みの口が返した Hy の module の code に誤りがあれば、書き足す 1 行を言う警告を出すため(誤りの無い module では何もしない)。

    ``record`` は code に載った記録(展開が表を引いて無かった名を持つ)— 候補の絞りは記録の名の照合だけで、正しい module では
    bytecode を歩かない。警告は module を読む度に出る(.pyc から読んだ時も — 直すまで毎回言う)。
    """
    for hint in _hints(code, record):
        warnings.warn_explicit(hint.message, HyMacroUseWarning, path, hint.line, module=fullname)


def _hints(code: CodeType, record: MacroRecord) -> Iterator[Hint]:
    """code の誤りごとの警告(require し忘れ・import で入れた)を出すため。候補が無ければ bytecode を歩かない。"""
    table = macro_table()
    if table is None:
        return
    candidates = frozenset(name for name in record.absent if name in table.first_home)
    if candidates:
        yield from _forgotten_requires(code, candidates, table)
    # 提供元の module の名と、その macro の名の 1 つが、先頭の code の名に在る module だけ命令を歩く(全部の命令を歩くのは
    # 大きな module で十数 ms — 名の照合は µs)。
    names = frozenset(code.co_names)
    importable = {
        _python_module(home): macros
        for home, macros in table.by_home.items()
        if _python_module(home) in names and not names.isdisjoint(macros)
    }
    if importable:
        yield from _imported_macros(code, importable)


def _python_module(home: str) -> str:
    """提供元の Hy の module の名(``doeff-hy.macros``)に対応する Python の module の名(``doeff_hy.macros``)を引くため。"""
    return home.replace("-", "_")


def _listed(spellings: Iterable[str]) -> str:
    """macro の綴りの並びを、警告の文の中の英語の並び(`a`・`a` and `b`・`a`, `b` and `c`)にするため。"""
    quoted = [f"`{spelling}`" for spelling in spellings]
    return quoted[0] if len(quoted) == 1 else f"{', '.join(quoted[:-1])} and {quoted[-1]}"


def _units(code: CodeType) -> Iterator[CodeType]:
    """module の code と、その中の関数・class の code を全部(入れ子も)歩くため。"""
    yield code
    for const in code.co_consts:
        if isinstance(const, CodeType):
            yield from _units(const)


def _line(instruction: dis.Instruction) -> int:
    """instruction の行を引くため(位置の無い instruction は 1 行目)。"""
    positions = instruction.positions
    line = None if positions is None else positions.lineno
    return 1 if line is None else line


def _forgotten_requires(
    code: CodeType, candidates: frozenset[str], table: MacroTable
) -> Iterator[Hint]:
    """候補の名のうち、module の code が束縛せずに読む物を、require の 1 行(提供元ごと)つきの警告にするため。"""
    units = tuple(_units(code))
    # 読み(LOAD_NAME・LOAD_GLOBAL)の名も束縛(STORE_NAME・STORE_GLOBAL など)の名も co_names から引くので、候補が co_names の
    # 和に無ければ警告は出ない — 局所の名として同じ綴りを使う module(``(check row)`` は LOAD_FAST)では命令を歩かない
    # (大きな module で 1 本 1 秒前後 — free-threaded の dis は遅い)。
    if candidates.isdisjoint(name for unit in units for name in unit.co_names):
        return
    instructions = tuple(ins for unit in units for ins in dis.get_instructions(unit))
    if any(ins.opname == "IMPORT_STAR" for ins in instructions):
        return  # 何が入るか分からない module は、束縛が無いと言い切れない
    bound = frozenset(ins.argval for ins in instructions if ins.opname in _BINDS)
    reads = [
        (_line(ins), ins.argval)
        for ins in instructions
        if ins.opname in _READS and ins.argval in candidates and ins.argval not in bound
    ]
    if not reads:
        return
    first_line = {name: min(line for line, other in reads if other == name) for _, name in reads}
    macros = [table.first_home[name] for name in sorted(first_line, key=first_line.__getitem__)]
    lines = [
        f"(require {home} [{' '.join(macro.spelling for macro in macros if macro.home == home)}])"
        for home in MACRO_HOMES
        if any(macro.home == home for macro in macros)
    ]
    verb = "is a doeff-hy macro" if len(macros) == 1 else "are doeff-hy macros"
    yield Hint(
        min(first_line.values()),
        f"Add {' and '.join(lines)} to this module: {_listed(macro.spelling for macro in macros)} "
        f"{verb} used here without a require, so Hy compiled the use as a call to an undefined "
        f"name (at run time: NameError: name {macros[0].mangled!r} is not defined).",
    )


@dataclass(frozen=True)
class _ImportedMacro:
    """``from 提供元 import macro`` の 1 つ: 提供元の Python の名・使った行・macro。"""

    source: str
    line: int
    macro: MacroName


def _macro_imports(
    code: CodeType, importable: Mapping[str, Mapping[str, MacroName]]
) -> Iterator[_ImportedMacro]:
    """module の先頭の code の ``from 提供元 import 名`` のうち、名が提供元の macro の物を出すため。"""
    source = ""
    for instruction in dis.get_instructions(code):
        if instruction.opname == "IMPORT_NAME":
            source = instruction.argval
        elif instruction.opname == "IMPORT_FROM":
            macro = importable.get(source, {}).get(instruction.argval)
            if macro is not None:
                yield _ImportedMacro(source, _line(instruction), macro)


def _imported_macros(
    code: CodeType, importable: Mapping[str, Mapping[str, MacroName]]
) -> Iterator[Hint]:
    """macro を import で入れた誤りを、提供元ごとに require の 1 行つきの警告にするため。"""
    imports = tuple(_macro_imports(code, importable))
    for source in dict.fromkeys(item.source for item in imports):
        mine = [item for item in imports if item.source == source]
        home = mine[0].macro.home
        names = " ".join(item.macro.spelling for item in mine)
        yield Hint(
            min(item.line for item in mine),
            f"Require, do not import: (require {home} [{names}]). "
            f"{_listed(item.macro.spelling for item in mine)} "
            f"{'is a macro' if len(mine) == 1 else 'are macros'} of {source}, and import cannot "
            f"bring in a macro (at run time: ImportError: cannot import name "
            f"{mine[0].macro.spelling!r} from {source!r}).",
        )
