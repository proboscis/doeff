"""手で書いた型の宣言(.hy の隣の .pyi)の公開の名を、.hy の module が自分で定めた公開の名と照らす。

前は検ごとに宣言の名をもう一度手で書いた表(DECLARED)を持ち、宣言の名と等しいかを照らした。.hy と .pyi に名を足しても表は
誰も直さないので、足した commit が検を回さずに着地すると main で赤になった(96be48c3c の ServiceReached)。ここでは両側とも
source から名を引く — .hy の直下の定義の form と、.pyi の直下の宣言の文。名を足す・消すのどちらでも 3 つ目の手直しは要らず、
片側だけから名を消すと差として名指される。import してきた名(.hy の import・.pyi の `from m import X`)はどちらの側も数えない。
"""

import ast
import inspect
from pathlib import Path
from types import ModuleType

import hy

#: module の直下で名を定義する Hy の form の頭。
DEFINING_HEADS = frozenset({"defrecord", "defclass", "defeffect", "defk", "deff", "defn", "defhandler", "val", "var", "setv"})

#: 宣言に載せない名(linter が読む印 — 使い手は読まない)。
BOOKKEEPING = frozenset({"MODULE_TAGS"})


def _defined_name(form: object) -> str | None:
    """.hy の直下の form 1 つが定義する名(Python の名へ mangle した物)。定義の form でなければ None。

    飾りの並び(`(defclass [dataclass] 名 …)`)は飛ばし、注記つきの名(`(defn #^ str 名 …)` — 読むと `(annotate 名 str)`)は
    名を取り出す。
    """
    if not (isinstance(form, hy.models.Expression) and len(form) >= 2 and str(form[0]) in DEFINING_HEADS):
        return None
    rest = list(form[1:])
    if isinstance(rest[0], hy.models.List) and len(rest) >= 2:
        rest = rest[1:]
    match rest[0]:
        case hy.models.Symbol() as name:
            return hy.mangle(str(name))
        case hy.models.Expression() as note if len(note) >= 2 and str(note[0]) == "annotate" and isinstance(note[1], hy.models.Symbol):
            return hy.mangle(str(note[1]))
        case _:
            return None


def hy_defined_names(module: ModuleType) -> set[str]:
    """.hy の module の直下で定義した公開の名(_ で始まる名と印は除く)。"""
    forms = hy.read_many(Path(inspect.getfile(module)).read_text(encoding="utf-8"))
    names = (_defined_name(form) for form in forms)
    return {n for n in names if n is not None and not n.startswith("_") and n not in BOOKKEEPING}


def _declared_name(node: ast.stmt) -> str | None:
    """宣言の直下の文 1 つが宣言する名(import は宣言でない — None)。"""
    match node:
        case ast.ClassDef(name=name) | ast.FunctionDef(name=name) | ast.AnnAssign(target=ast.Name(id=name)):
            return name
        case ast.Assign(targets=[ast.Name(id=name)]):
            return name
        case _:
            return None


def stub_declared_names(stub: ast.Module) -> set[str]:
    """宣言の直下で宣言した公開の名(_ で始まる型の補助は除く)。"""
    names = (_declared_name(node) for node in stub.body)
    return {n for n in names if n is not None and not n.startswith("_")}


def name_mismatches(module: ModuleType, stub: ast.Module) -> list[str]:
    """宣言の公開の名と .hy の公開の名の差(空なら一致)。"""
    defined = hy_defined_names(module)
    declared = stub_declared_names(stub)
    return [f"宣言に無い公開の名: {n}" for n in sorted(defined - declared)] + [
        f"実装に無い宣言の名: {n}" for n in sorted(declared - defined)
    ]
