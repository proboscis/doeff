"""Hy の module の型の宣言(.pyi)を、型検査のための展開から作る(agora-redesign #2826)。

型検査のための展開(static_check.project)は、defk / deff の契約(:pre の `(: x T)`・:post の `(: % T)`)と
defrecord の欄の `#^` を Python の注記にした module の木を返す。ここはその木から本体を外し、公開面の宣言だけを
残す — macro の意味の定義点は展開の 1 か所のまま(.pyi のための 2 つ目の読み手を作らない)。

宣言の形:
- defk(`@_doeff_do` の関数)→ `def 名(引数) -> _Program[答え, object]: ...`。答え = :post の型(型でない :post は
  Incomplete)。effect の位置は object(Program は答えと effect の両方で共変 — doeff-records・doeff-cluster の手の .pyi と同じ)。
- defhandler(答え手を被せる関数を返す関数)→ `def 名(引数) -> _Handler: ...`(doeff_hy.static_types.Handler)。
- 普通の関数(deff・defn)→ 注記のまま本体を `...` に。注記の無い引数・答えは Incomplete。
- class → 飾り・基底・欄の注記・method の形のまま。欄と引数の既定値は literal なら残し、式なら `...`。
- 定数 → 注記があればそれ・literal / literal の組・辞書はその型・型の和(`A | B`)と TypeVar は形のまま。型を出せない
  値は Incomplete(typeshed の「型がまだ無い」印)にし、StubText.incomplete に名を残す。
- import → `from m import X as X` の形で公開し直す(実行時の module は import した名を属性に持ち、使い手はそれを引く —
  doeff_records.main の MaintenancePlan など)。macro の補助(`_doeff_*`・doeff_hy.static_types)と `hy` は外す。

一致の検は「作り直した物 == commit された物」(stale_stubs・CLI の --check)。手で書いた .pyi(先頭に MARK が無い物)は
--replace を付けない限り書き換えない。
"""

import argparse
import ast
import builtins
import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass, replace
from functools import reduce
from pathlib import Path

from doeff_hy.static_check import CompileFailure, hy_files, import_roots, project, pyright_settings

#: 道具が作った .pyi の 1 行目の頭(一致の検と --write が、手で書いた .pyi と見分ける印)。
MARK = "# doeff_hy.static_stub が作った型の宣言 — 手で直さない"

#: 型の和(`A | B`)の葉として型と読む組み込みの名(CamelCase の名は class として型と読む)。
_BUILTIN_TYPES = frozenset(
    {"dict", "list", "tuple", "set", "frozenset", "str", "int", "float", "bool", "bytes", "bytearray", "complex", "object", "type"}
)
#: 値の形のまま宣言に残す型の構成子(TypeVar などは代入の形そのものが型の宣言)。
_TYPE_MAKERS = frozenset({"TypeVar", "ParamSpec", "TypeVarTuple", "NewType"})
#: 宣言に残す method の飾り(型の意味を持つ物だけ)。
_METHOD_DECORATORS = frozenset({"staticmethod", "classmethod", "property", "overload", "abstractmethod"})
#: defk を包む飾りの名(型検査のための展開の `_doeff_do`・利用者が書く `do`)。
_DO_DECORATORS = frozenset({"_doeff_do", "do"})
#: module の直下の名のうち型の面に出さない物(module の印・Hy の gensym・macro の記帳)。
_HIDDEN_NAMES = frozenset({"MODULE_TAGS"})
_HIDDEN_PREFIXES = ("_hy_", "__doeff_", "_doeff_")
#: 答えが str の str の method(定数の値が `X.format(...)` のように作られる時に型を読むため)。
_STR_METHODS = frozenset({"format", "join", "strip", "lower", "upper", "replace", "removeprefix", "removesuffix"})
#: 答えが常に None の特別な method(注記が無くても None と読む)。
_NONE_METHODS = frozenset({"__init__", "__post_init__"})
#: 型を出せなかった所の印の名(typeshed の _typeshed.Incomplete)。
_INCOMPLETE = "Incomplete"
#: 答えが str の os.path の関数(module の場所から作る path の定数の型を読むため)。
_PATH_FUNCTIONS = frozenset({"dirname", "abspath", "basename", "join", "realpath", "normpath", "expanduser"})


@dataclass(frozen=True)
class StubText:
    """1 つの .hy から作った .pyi の本文と、型を出せず Incomplete にした所の名(書き手が契約を足す先の一覧)。"""

    text: str
    incomplete: tuple[str, ...]


@dataclass(frozen=True)
class Stale:
    """一致の検の食い違い 1 件: commit された .pyi が作り直した物と違う(または作れない)。"""

    source: Path
    reason: str


class StubUnmade(Exception):
    """.hy を型検査のための展開で読めず、.pyi を作れなかった(Hy の compile の赤)。"""


@dataclass(frozen=True)
class _Declared:
    """module の直下の宣言 1 つ(同じ名を後で宣言し直したら後の方を残すため、名と組にして持つ)。"""

    name: str
    node: ast.stmt


@dataclass(frozen=True)
class _Helper:
    """宣言が読む時だけ .pyi の頭に置く補助の型の import 1 つ(Program・Handler・Incomplete・TypeAlias)。"""

    reads: str
    module: str
    name: str
    asname: str | None


#: 補助の型の import(宣言の中の名 reads を読む時だけ置く)。
_HELPERS = (
    _Helper("TypeAlias", "typing", "TypeAlias", None),
    _Helper("Incomplete", "_typeshed", "Incomplete", None),
    _Helper("_Program", "doeff", "Program", "_Program"),
    _Helper("_Handler", "doeff_hy.static_types", "Handler", "_Handler"),
)


@dataclass(frozen=True)
class _Scan:
    """module の直下を上から読んだ途中の状態 — 後の定数が先の定数の型を引くために、読んだ順に持ち運ぶ。"""

    imports: tuple[ast.stmt, ...]
    declarations: tuple[_Declared, ...]
    #: module の直下の関数の宣言(型の面に出さない名も含む — `x = _hy_anon_3()` の値の型をその関数の答えから引くため)。
    functions: tuple[_Declared, ...] = ()

    def declared(self, name: str, node: ast.stmt) -> "_Scan":
        """宣言 1 つを足した次の状態を返す。"""
        return replace(self, declarations=(*self.declarations, _Declared(name, node)))

    def defined(self, name: str, node: ast.FunctionDef, public: bool) -> "_Scan":
        """関数 1 つを覚えた次の状態を返す(公開の名なら宣言にも足す)。"""
        known = replace(self, functions=(*self.functions, _Declared(name, node)))
        return known.declared(name, node) if public else known

    def answer_of(self, name: str) -> ast.expr | None:
        """先に読んだ module の関数を呼んだ値の型(関数の宣言の答え — 引数の無い defhandler の `x = _hy_anon_3()` など)。"""
        answers = [d.node.returns for d in self.functions if d.name == name and isinstance(d.node, ast.FunctionDef)]
        return answers[-1] if answers and answers[-1] is not None else None

    def kind_of(self, name: str) -> ast.expr | None:
        """先に読んだ定数の型(値を持たない注記の宣言の型 — 後の定数の値がその名を読む時に引く)。"""
        kinds = [
            d.node.annotation
            for d in self.declarations
            if d.name == name and isinstance(d.node, ast.AnnAssign) and d.node.value is None
        ]
        return kinds[-1] if kinds else None


def _absolute(path: Path) -> Path:
    """根の比べと相対 path の表示のため、symlink を辿らずに絶対 path へ正規化する。"""
    return Path(os.path.normpath(path.absolute()))


def _hidden(name: str) -> bool:
    """型の面に出さない名か(module の印・Hy の gensym・macro の補助と記帳)。"""
    return name in _HIDDEN_NAMES or name.startswith(_HIDDEN_PREFIXES)


def _camel(name: str) -> bool:
    """class の名と読める CamelCase の名か(全部大文字の定数の名と分けるため・頭の `_` は module の内の class の印として外して見る)。"""
    bare = name.lstrip("_")
    return bare[:1].isupper() and any(c.islower() for c in bare)


def _name(text: str) -> ast.Name:
    """宣言に置く名の式を作る(読みの文脈)。"""
    return ast.Name(id=text, ctx=ast.Load())


def _subscript(base: str, *items: ast.expr) -> ast.Subscript:
    """`base[items]` の型の式を作る(入れ物の要素の型・Program の答えの型)。"""
    index: ast.expr = items[0] if len(items) == 1 else ast.Tuple(elts=list(items), ctx=ast.Load())
    return ast.Subscript(value=_name(base), slice=index, ctx=ast.Load())


def _union(types: list[ast.expr]) -> ast.expr:
    """要素の型を 1 つの型の和にする(同じ型は 1 つに・現れた順)。"""
    keys = [ast.unparse(item) for item in types]
    unique = [item for index, item in enumerate(types) if keys[index] not in keys[:index]]
    return reduce(lambda folded, item: ast.BinOp(left=folded, op=ast.BitOr(), right=item), unique[1:], unique[0])


def _type_like(node: ast.expr) -> bool:
    """値が型の式か(型の和・型の添字は型の別名として残すため)。"""
    match node:
        case ast.Constant(value=None):
            return True
        case ast.Name(id=name):
            return name in _BUILTIN_TYPES or _camel(name)
        case ast.Attribute(attr=attr):
            return _camel(attr)
        case ast.Subscript(value=base):
            return _type_like(base)
        case ast.BinOp(op=ast.BitOr()):
            # 和の葉が全部型の式の形で、None か組み込みの型が 1 つでもあれば型の和(値どうしの `|` に None や int は混ざらない —
            # 小文字の class `datetime` を含む和も型と読むため)。
            leaves = _union_leaves(node)
            shaped = all(isinstance(leaf, ast.Name | ast.Attribute | ast.Subscript) or _none(leaf) for leaf in leaves)
            return shaped and any(_type_like(leaf) for leaf in leaves)
        case _:
            return False


def _annotation(node: ast.expr | None) -> ast.expr | None:
    """展開の注記(契約の型は文字列の注記 `'T'` で来る)を、.pyi に書く型の式にする。"""
    match node:
        case ast.Constant(value=str(text)):
            try:
                return ast.parse(text, mode="eval").body
            except SyntaxError:
                return node
        case _:
            return node


def _default(node: ast.expr) -> ast.expr:
    """既定値を .pyi の形にする: literal(負の数を含む)は残し、式は `...` にする(本体を持たない宣言に式を写さない)。"""
    match node:
        case ast.Constant():
            return node
        case ast.UnaryOp(op=ast.USub(), operand=ast.Constant()):
            return node
        case _:
            return ast.Constant(value=...)


def _elements(items: list[ast.expr], scan: _Scan) -> list[ast.expr] | None:
    """入れ物の要素の型の並び(1 つでも出せなければ None — 入れ物の型を半端に出さない)。"""
    found = [_value_type(item, scan) for item in items]
    return [kind for kind in found if kind is not None] if all(kind is not None for kind in found) else None


def _container(maker: str, items: list[ast.expr], scan: _Scan) -> ast.expr | None:
    """literal の入れ物の型(tuple は可変長の `tuple[T, ...]`)。"""
    kinds = _elements(items, scan)
    if not kinds:
        return None
    return _subscript("tuple", _union(kinds), ast.Constant(value=...)) if maker == "tuple" else _subscript(maker, _union(kinds))


def _value_type(node: ast.expr, scan: _Scan) -> ast.expr | None:
    """定数の値の型を literal の形から読む(読めなければ None — 呼び手が Incomplete にする)。"""
    match node:
        case ast.Constant(value=bool()):
            return _name("bool")
        case ast.Constant(value=None):
            return ast.Constant(value=None)
        case ast.Constant(value=int()):
            return _name("int")
        case ast.Constant(value=float()):
            return _name("float")
        case ast.Constant(value=str()) | ast.JoinedStr():
            return _name("str")
        case ast.Constant(value=bytes()):
            return _name("bytes")
        case ast.Name(id=name) if (kind := scan.kind_of(name)) is not None:
            return kind
        case ast.Tuple(elts=[]):
            return _subscript("tuple", ast.Tuple(elts=[], ctx=ast.Load()))
        case ast.Tuple(elts=items):
            return _container("tuple", items, scan)
        case ast.List(elts=items) if items:
            return _container("list", items, scan)
        case ast.Set(elts=items):
            return _container("set", items, scan)
        case ast.Call(func=ast.Name(id=("tuple" | "list" | "set" | "frozenset") as maker), args=[ast.Tuple(elts=items) | ast.List(elts=items)]) if items:
            return _container(maker, items, scan)
        case ast.Call(func=ast.Name(id=("tuple" | "list" | "set" | "frozenset") as maker), args=[ast.GeneratorExp(elt=item) | ast.ListComp(elt=item)]):
            return _container(maker, [item], scan)
        case ast.ListComp(elt=item):
            return _container("list", [item], scan)
        case ast.Dict(keys=keys, values=values) if keys and all(key is not None for key in keys):
            key_kinds = _elements([key for key in keys if key is not None], scan)
            value_kinds = _elements(values, scan)
            return _subscript("dict", _union(key_kinds), _union(value_kinds)) if key_kinds and value_kinds else None
        case ast.BinOp(left=left, op=ast.Add(), right=right):
            return _sum_type(_value_type(left, scan), _value_type(right, scan))
        case ast.BinOp(left=left, op=ast.Sub() | ast.Mult() | ast.FloorDiv() | ast.Mod() | ast.Pow(), right=right):
            return _number_type(_value_type(left, scan), _value_type(right, scan))
        case ast.Call(func=ast.Name(id=("str" | "int" | "float" | "bool" | "bytes") as maker)):
            return _name(maker)
        case ast.Call(func=ast.Attribute(value=base, attr=method)) if method in _STR_METHODS:
            kind = _value_type(base, scan)
            return kind if kind is not None and ast.unparse(kind) == "str" else None
        case ast.Call(func=ast.Attribute(value=ast.Attribute(value=ast.Name(id="os"), attr="path"), attr=function)) if function in _PATH_FUNCTIONS:
            return _name("str")
        case ast.Attribute(value=ast.Name(id=owner), attr=member) if _camel(owner) and member.isupper():
            return _name(owner)
        case ast.Call(func=ast.Name(id=function)) if (answer := scan.answer_of(function)) is not None:
            return answer
        case ast.Call(func=ast.Name(id=maker)) if _camel(maker):
            return _name(maker)
        case _:
            return None


def _sum_type(left: ast.expr | None, right: ast.expr | None) -> ast.expr | None:
    """`a + b` の型(同じ型どうし・可変長の tuple どうしは要素の型の和の tuple。ほかは読まない)。"""
    if left is None or right is None:
        return None
    match left, right:
        case (
            ast.Subscript(value=ast.Name(id="tuple"), slice=ast.Tuple(elts=[first, ast.Constant(value=builtins.Ellipsis)])),
            ast.Subscript(value=ast.Name(id="tuple"), slice=ast.Tuple(elts=[second, ast.Constant(value=builtins.Ellipsis)])),
        ):
            return _subscript("tuple", _union([first, second]), ast.Constant(value=...))
        case _ if ast.unparse(left) == ast.unparse(right) and ast.unparse(left) in ("str", "bytes", "int", "float"):
            return left
        case _:
            return None


def _number_type(left: ast.expr | None, right: ast.expr | None) -> ast.expr | None:
    """数どうしの演算(`(* 32 1024 1024)` のような大きさの定数)の型: int どうしは int・float が混ざれば float。"""
    kinds = {ast.unparse(k) for k in (left, right) if k is not None}
    if left is None or right is None or not kinds <= {"int", "float"}:
        return None
    return _name("float" if "float" in kinds else "int")


def _union_leaves(node: ast.expr) -> list[ast.expr]:
    """`A | B | C` の葉の並び(型の和かどうかを葉ごとに見るため)。"""
    match node:
        case ast.BinOp(left=left, op=ast.BitOr(), right=right):
            return [*_union_leaves(left), *_union_leaves(right)]
        case _:
            return [node]


def _none(node: ast.expr) -> bool:
    """None の literal か(型の和の葉の None)。"""
    return isinstance(node, ast.Constant) and node.value is None


def _keeps(decorator: ast.expr) -> bool:
    """関数の飾りのうち、型の意味を持つので .pyi に残す物か(`x.setter` を含む)。"""
    match decorator:
        case ast.Name(id=name):
            return name in _METHOD_DECORATORS
        case ast.Attribute(attr=("setter" | "getter" | "deleter")):
            return True
        case _:
            return False


def _reads_hidden(node: ast.expr) -> bool:
    """式が macro の補助の名を読むか(.pyi に写すと解けない名を残さないため)。"""
    return any(isinstance(n, ast.Name) and _hidden(n.id) for n in ast.walk(node))


def _class_var(annotation: ast.expr) -> bool:
    """欄の注記が ClassVar か(class の値は dataclass の欄でないので既定値を写さない)。"""
    match annotation:
        case ast.Name(id="ClassVar") | ast.Subscript(value=ast.Name(id="ClassVar")):
            return True
        case _:
            return False


def _arguments(arguments: ast.arguments, method: bool) -> ast.arguments:
    """引数の並びを .pyi の形にする(注記は型の式に・注記の無い引数は Incomplete・既定値は literal か `...`)。"""
    positional = [*arguments.posonlyargs, *arguments.args]
    receiver = positional[0].arg if method and positional and positional[0].arg in ("self", "cls") else None

    def typed(arg: ast.arg) -> ast.arg:
        """引数 1 つに型を付ける(method の self / cls は注記なしのまま)。"""
        annotation = _annotation(arg.annotation)
        if annotation is None and arg.arg != receiver:
            annotation = _name(_INCOMPLETE)
        return ast.arg(arg=arg.arg, annotation=annotation)

    return ast.arguments(
        posonlyargs=[typed(a) for a in arguments.posonlyargs],
        args=[typed(a) for a in arguments.args],
        vararg=None if arguments.vararg is None else typed(arguments.vararg),
        kwonlyargs=[typed(a) for a in arguments.kwonlyargs],
        kw_defaults=[None if d is None else _default(d) for d in arguments.kw_defaults],
        kwarg=None if arguments.kwarg is None else typed(arguments.kwarg),
        defaults=[_default(d) for d in arguments.defaults],
    )


def _returns(node: ast.FunctionDef | ast.AsyncFunctionDef) -> ast.expr:
    """関数の答えの型: defk は Program に包み、defhandler は Handler、ほかは注記のまま(無ければ Incomplete)。"""
    if any(isinstance(d, ast.Name) and d.id in _DO_DECORATORS for d in node.decorator_list):
        return _subscript("_Program", _annotation(node.returns) or _name(_INCOMPLETE), _name("object"))
    if any(isinstance(s, ast.FunctionDef) and s.name == "__doeff_handler_fn__" for s in node.body):
        return _name("_Handler")
    declared = _annotation(node.returns)
    if declared is not None:
        return declared
    return ast.Constant(value=None) if node.name in _NONE_METHODS else _name(_INCOMPLETE)


def _function(node: ast.FunctionDef | ast.AsyncFunctionDef, method: bool) -> ast.FunctionDef:
    """関数 1 つの宣言(本体は `...`・型の意味を持たない飾りは外す)。"""
    return ast.FunctionDef(
        name=node.name,
        args=_arguments(node.args, method),
        body=[ast.Expr(value=ast.Constant(value=...))],
        decorator_list=[d for d in node.decorator_list if _keeps(d)],
        returns=_returns(node),
        type_params=[],
    )


def _member(statement: ast.stmt) -> ast.stmt | None:
    """class の本体の文 1 つの宣言(欄・class の値・method・入れ子の class。型の面を持たない文は None)。"""
    match statement:
        case ast.AnnAssign(target=ast.Name() as target, annotation=annotation, value=value):
            kind = _annotation(annotation) or annotation
            kept = None if value is None or _class_var(kind) else _default(value)
            return ast.AnnAssign(target=target, annotation=kind, value=kept, simple=1)
        case ast.Assign(targets=[ast.Name() as target], value=value) if not _hidden(target.id):
            return ast.Assign(targets=[target], value=_default(value))
        case ast.FunctionDef() | ast.AsyncFunctionDef() if not _hidden(statement.name):
            return _function(statement, method=True)
        case ast.ClassDef():
            return _class(statement)
        case _:
            return None


def _class(node: ast.ClassDef) -> ast.ClassDef:
    """class 1 つの宣言(飾り・基底・欄・method の形を残し、本体の式は外す)。"""
    body = [member for statement in node.body if (member := _member(statement)) is not None]
    return ast.ClassDef(
        name=node.name,
        bases=node.bases,
        keywords=node.keywords,
        body=body or [ast.Expr(value=ast.Constant(value=...))],
        decorator_list=[d for d in node.decorator_list if not _reads_hidden(d)],
        type_params=[],
    )


def _constant(name: str, value: ast.expr, scan: _Scan) -> ast.stmt:
    """module の定数 1 つの宣言(型の別名と TypeVar は形のまま・ほかは値の型の注記)。"""
    match value:
        case ast.BinOp(op=ast.BitOr()) | ast.Subscript() if _type_like(value):
            return ast.AnnAssign(target=_name(name), annotation=_name("TypeAlias"), value=value, simple=1)
        case ast.Name(id=other) if _camel(other):
            return ast.Assign(targets=[_name(name)], value=value)
        case ast.Call(func=ast.Name(id=maker) | ast.Attribute(attr=maker)) if maker in _TYPE_MAKERS:
            return ast.Assign(targets=[_name(name)], value=value)
        case _:
            kind = _value_type(value, scan) or _name(_INCOMPLETE)
            return ast.AnnAssign(target=_name(name), annotation=kind, value=None, simple=1)


def _macro_helper(module: str | None, name: str) -> bool:
    """macro の展開が import する doeff_hy.macros の補助か(_install_guard_globals・_guard_performed など — 型の面ではないので
    公開し直さない。公開し直すと .pyi が macro の module に依存する形になり、品質検査の依存の契約に当たった — #2842)。"""
    return module == "doeff_hy.macros" and name.startswith("_")


def _reexports(statement: ast.Import | ast.ImportFrom) -> tuple[ast.stmt, ...]:
    """import 1 文を、名を公開し直す形(`X as X`)の文に分ける(macro の補助と `hy` は外す)。"""
    match statement:
        case ast.ImportFrom(module=str(module)) if module == "__future__" or module.startswith("doeff_hy.static_types"):
            return ()
        case ast.ImportFrom(module=module, names=names, level=level):
            return tuple(
                ast.ImportFrom(module=module, names=[ast.alias(name=a.name, asname=a.asname or a.name)], level=level)
                for a in names
                if a.name != "*" and not _hidden(a.asname or a.name) and not _macro_helper(module, a.name)
            )
        case ast.Import(names=names):
            return tuple(
                ast.Import(names=[ast.alias(name=a.name, asname=a.asname or (a.name if "." not in a.name else None))])
                for a in names
                if a.name.split(".")[0] != "hy" and not _hidden(a.asname or a.name)
            )


def _step(state: _Scan, statement: ast.stmt) -> _Scan:
    """module の直下の文 1 つを読み、宣言・公開し直す import・定数の型を足した次の状態を返す
    (型の面を持たない文 — 式・if・try など — は読み捨てる)。"""
    match statement:
        case ast.Import() | ast.ImportFrom():
            return replace(state, imports=(*state.imports, *_reexports(statement)))
        case ast.FunctionDef() | ast.AsyncFunctionDef():
            return state.defined(statement.name, _function(statement, method=False), public=not _hidden(statement.name))
        case ast.ClassDef() if not _hidden(statement.name):
            return state.declared(statement.name, _class(statement))
        case ast.AnnAssign(target=ast.Name(id=name), annotation=annotation, value=value) if not _hidden(name):
            kind = _annotation(annotation) or annotation
            alias = isinstance(kind, ast.Name) and kind.id == "TypeAlias"
            node = ast.AnnAssign(target=_name(name), annotation=kind, value=value if alias else None, simple=1)
            return state.declared(name, node)
        case ast.Assign(targets=[ast.Name(id=name)], value=value) if not _hidden(name):
            return state.declared(name, _constant(name, value, state))
        case _:
            return state


def _incomplete(node: ast.expr | None) -> bool:
    """型の式が Incomplete を含むか(型を出せなかった所の印)。"""
    return node is not None and any(isinstance(n, ast.Name) and n.id == _INCOMPLETE for n in ast.walk(node))


def _gaps(statement: ast.stmt, owner: str) -> tuple[str, ...]:
    """宣言 1 つの中で型を出せず Incomplete にした所の名(書き手が契約を足す先として報告するため)。"""
    match statement:
        case ast.AnnAssign(target=ast.Name(id=name), annotation=annotation) if _incomplete(annotation):
            return (f"{owner}{name}",)
        case ast.FunctionDef(name=name, args=arguments, returns=returns):
            listed = [*arguments.posonlyargs, *arguments.args, *arguments.kwonlyargs]
            rest = [a for a in (arguments.vararg, arguments.kwarg) if a is not None]
            missing = tuple(f"{owner}{name} の引数 {a.arg}" for a in (*listed, *rest) if _incomplete(a.annotation))
            return (*missing, *((f"{owner}{name} の答え",) if _incomplete(returns) else ()))
        case ast.ClassDef(name=name, body=body):
            return tuple(gap for member in body for gap in _gaps(member, f"{owner}{name}."))
        case _:
            return ()


def _bound(statement: ast.stmt) -> tuple[str, ...]:
    """import 1 文が束ねる名(補助の型の import を重ねて置かないため)。"""
    match statement:
        case ast.Import(names=names) | ast.ImportFrom(names=names):
            return tuple(a.asname or a.name for a in names)
        case _:
            return ()


def _render(state: _Scan, source_name: str) -> StubText:
    """読み終えた状態から .pyi の本文を組む(補助の型の import は宣言が読む物だけ・同じ import は 1 つに)。"""
    declared = state.declarations
    body = [d.node for index, d in enumerate(declared) if d.name not in {e.name for e in declared[index + 1 :]}]
    keys = [ast.unparse(line) for line in state.imports]
    imports = [line for index, line in enumerate(state.imports) if keys[index] not in keys[:index]]
    bound = {name for line in imports for name in _bound(line)}
    reads = {n.id for statement in body for n in ast.walk(statement) if isinstance(n, ast.Name)}
    head = [
        ast.ImportFrom(module=helper.module, names=[ast.alias(name=helper.name, asname=helper.asname)], level=0)
        for helper in _HELPERS
        if helper.reads in reads and (helper.asname or helper.name) not in bound
    ]
    module = ast.fix_missing_locations(ast.Module(body=[*head, *imports, *body], type_ignores=[]))
    first = f"{MARK}(元 = {source_name}・作り直し = python -m doeff_hy.static_stub --write <この .pyi の隣の .hy>)"
    gaps = tuple(gap for statement in body for gap in _gaps(statement, ""))
    return StubText(f"{first}\n\n{ast.unparse(module)}\n", gaps)


def stub_of(root: Path, roots: list[Path], source: Path) -> StubText:
    """1 つの .hy から .pyi の本文を作る(使い手の strict の型検査が Hy の module の名を Unknown と読まないため)。"""
    projected = project(root, roots, source)
    if isinstance(projected, CompileFailure):
        raise StubUnmade(projected.diagnostic.render())
    empty = _Scan(imports=(), declarations=())
    return _render(reduce(_step, ast.parse(projected.text).body, empty), source.name)


def generated(stub: Path) -> bool:
    """道具が作った .pyi か(1 行目が MARK で始まる — 手で書いた .pyi を一致の検と --write から外すため)。"""
    return stub.is_file() and stub.read_text(encoding="utf-8").startswith(MARK)


def _stale(root: Path, roots: list[Path], source: Path) -> Stale | None:
    """1 つの .hy の .pyi が作り直した物と違えば、その食い違いを返す(道具が作った .pyi を持たない .hy は見ない)。"""
    stub = source.with_suffix(".pyi")
    if not generated(stub):
        return None
    try:
        made = stub_of(root, roots, source).text
    except StubUnmade as error:
        return Stale(source, f"作れなかった: {error}")
    return None if made == stub.read_text(encoding="utf-8") else Stale(source, "作り直した物と違う")


def stale_stubs(root: Path, roots: list[Path], sources: list[Path]) -> tuple[Stale, ...]:
    """一致の検: 道具が作った .pyi が、今の .hy から作り直した物と同じかを全部の .hy について確かめる。"""
    return tuple(item for source in sources if (item := _stale(root, roots, source)) is not None)


def stale_in(directory: Path) -> tuple[Stale, ...]:
    """package の source の dir の中で、道具が作った .pyi を持つ .hy を全部照らす(各 package の検が一致の検を 1 行で撃つため)。"""
    root = _absolute(directory)
    return stale_stubs(root, [root], hy_files([root]))


#: 「型が分からない」の赤の文(全く分からない名・解けない import。型の一部が分からない名 — partially unknown — は契約を細かくする別件)。
_UNKNOWN = re.compile(r"is unknown|could not be resolved|unknown import symbol")


@dataclass(frozen=True)
class UsedModule:
    """使い手が import する 1 つの module(package の下の名)と、その名の並び(Hy の綴り)— 使い手の名の probe を組む材料。"""

    module: str
    names: tuple[str, ...]


def users_probe(package: str, used: tuple[UsedModule, ...]) -> str:
    """使い手の名を 1 つずつ別の名に束ねる検の module(束ねた名の型が Unknown なら、その行に strict の赤が出るように)。"""
    imports = [f"(import {package}.{u.module} [{' '.join(u.names)}])" for u in used]
    names = [name for u in used for name in u.names]
    bindings = [f"(setv used-{index} {name})" for index, name in enumerate(names)]
    return "\n".join([*imports, "", *bindings, ""])


def unknown_in_users(workdir: Path, package: str, used: tuple[UsedModule, ...]) -> tuple[str, ...]:
    """使い手の名を束ねた検の module を使い手の型の門と同じ strict で検め、「型が分からない」と Hy の compile の赤を返す
    (各 package の検が、.pyi が使い手に届いているかを 1 行で確かめるため — 空なら使い手はどの名も型つきで読める)。"""
    probe = workdir / "probe.hy"
    probe.write_text(users_probe(package, used), encoding="utf-8")
    command = [sys.executable, "-m", "doeff_hy.static_check", "--root", str(workdir), "--json", "--strict", "--no-cache", str(probe)]
    done = subprocess.run(command, capture_output=True, text=True, timeout=240, check=False)
    diagnostics: list[dict[str, object]] = json.loads(done.stdout) if done.stdout.strip() else []
    errors = [f"{d['line']}: {d['rule']}: {d['message']}" for d in diagnostics if d["severity"] == "error"]
    return tuple(e for e in errors if "hy-compile" in e or _UNKNOWN.search(e))


def main(argv: list[str] | None = None) -> int:
    """CLI の入口: .pyi を書く(--write)・一致を検める(--check)・標準出力へ出す(既定)。"""
    parser = argparse.ArgumentParser(
        prog="doeff-hy-stub", description="Hy の module の型の宣言(.pyi)を、型検査のための展開から作る"
    )
    parser.add_argument("paths", nargs="+", type=Path)
    parser.add_argument("--root", type=Path, default=Path.cwd())
    action = parser.add_mutually_exclusive_group()
    action.add_argument("--write", action="store_true", help=".hy の隣に .pyi を書く")
    action.add_argument("--check", action="store_true", help="道具が作った .pyi が作り直した物と同じかを検める")
    parser.add_argument("--replace", action="store_true", help="--write で手で書いた .pyi も置き換える")
    args = parser.parse_args(argv)
    root = _absolute(args.root)
    roots = import_roots(root, pyright_settings(root))
    # 根は後ろに足す(展開の require が引く macro の module を読めるように)。前に足すと、build した拡張を持たない
    # source の木(packages/doeff-vm)が、環境に入っている build 済みの物を隠す。
    sys.path.extend(str(entry) for entry in roots if str(entry) not in sys.path)
    sources = hy_files([p if p.is_absolute() else Path.cwd() / p for p in args.paths])
    if args.check:
        stale = stale_stubs(root, roots, sources)
        for item in stale:
            print(f"{item.source.relative_to(root)}: {item.reason}", file=sys.stderr)
        return 1 if stale else 0
    for source in sources:
        stub = source.with_suffix(".pyi")
        try:
            made = stub_of(root, roots, source)
        except StubUnmade as error:
            print(f"doeff-hy-stub: 作れなかった: {error}", file=sys.stderr)
            return 2
        if made.incomplete:
            print(f"{source.relative_to(root)}: Incomplete — {'・'.join(made.incomplete)}", file=sys.stderr)
        if not args.write:
            sys.stdout.write(made.text)
        elif stub.exists() and not generated(stub) and not args.replace:
            print(f"{stub.relative_to(root)}: 手で書いた .pyi なので書かない(置き換えるなら --replace)", file=sys.stderr)
        else:
            stub.write_text(made.text, encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
