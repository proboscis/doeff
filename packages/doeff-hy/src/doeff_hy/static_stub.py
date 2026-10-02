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
    """宣言が読む時だけ .pyi の頭に置く補助の型の import 1 つ(Program・Handler・Incomplete・TypeAlias・defeffect の補助)。"""

    reads: str
    module: str
    name: str
    asname: str | None


#: 補助の型の import(宣言の中の名 reads を読む時だけ置く)。後の 2 つは defeffect の展開が import する macro の補助の名で、
#: 宣言の基底・飾りがそのまま読む — 隠す名(_doeff_)の import は公開し直さないので、ここで置かないと .pyi に
#: 定義の無い名が残り、使い手には effect の基底が Unknown(答えの型も Unknown)になっていた(agora-redesign #2886)。
_HELPERS = (
    _Helper("TypeAlias", "typing", "TypeAlias", None),
    _Helper("Incomplete", "_typeshed", "Incomplete", None),
    _Helper("_Program", "doeff", "Program", "_Program"),
    _Helper("_Handler", "doeff_hy.static_types", "Handler", "_Handler"),
    _Helper("_doeff_effect_base", "doeff", "EffectBase", "_doeff_effect_base"),
    _Helper("_doeff_dataclass", "dataclasses", "dataclass", "_doeff_dataclass"),
)
#: 補助の import が .pyi の頭に置ける名(隠す名でも、宣言に残してよい)。
_HELPER_NAMES = frozenset(helper.asname or helper.name for helper in _HELPERS)


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

    def type_vars(self) -> frozenset[str]:
        """先に読んだ型の引数の名(`T = TypeVar("T")` など — 後の class の答えの型がその名を読む時に型と読むため・#2925)。"""
        return frozenset(
            d.name
            for d in self.declarations
            if isinstance(d.node, ast.Assign)
            and isinstance(d.node.value, ast.Call)
            and isinstance(d.node.value.func, ast.Name | ast.Attribute)
            and (d.node.value.func.id if isinstance(d.node.value.func, ast.Name) else d.node.value.func.attr) in _TYPE_MAKERS
        )


def _absolute(path: Path) -> Path:
    """根の比べと相対 path の表示のため、symlink を辿らずに絶対 path へ正規化する。"""
    return Path(os.path.normpath(path.absolute()))


def hidden_name(name: str) -> bool:
    """型の面に出さない名か(module の印・Hy の gensym・macro の補助と記帳)。手書きの宣言と実装を照らす検
    (doeff-core-effects の test_hy_module_stubs)も、公開の名をこの 1 か所で決める(agora-redesign #2909 — MODULE_TAGS を
    道具は隠し検は数える食い違いで、頭のタグを足した module が赤になった)。"""
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
        case ast.UnaryOp(op=ast.USub() | ast.UAdd(), operand=ast.Constant(value=int() | float()) as operand) if not isinstance(
            operand.value, bool
        ):
            # 符号つきの数の literal(`(val STOPPED-CODE -15)` は展開で `-15` = 単項の演算 — 数の型は符号を付けても同じ・#2887)
            return _value_type(operand, scan)
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
    """式が、.pyi の頭に置けない macro の補助の名を読むか(.pyi に写すと解けない名を残さないため — 補助の import が
    置ける名は解けるので数えない: defeffect の `@_doeff_dataclass(frozen=True)` は残す)。"""
    return any(isinstance(n, ast.Name) and hidden_name(n.id) and n.id not in _HELPER_NAMES for n in ast.walk(node))


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
        # 契約の :tp(型の引数)は型検査の展開で `def f[T]` になる — 答えが引数の型で決まる関数の総称を .pyi へ運ぶ(#2893)。
        type_params=list(node.type_params),
    )


def _member(statement: ast.stmt) -> ast.stmt | None:
    """class の本体の文 1 つの宣言(欄・class の値・method・入れ子の class。型の面を持たない文は None)。macro の記帳の名
    (defeffect の `__doeff_answer__`・defwire の `__doeff_wire__` など隠す名)は写さない — 答えの型は基底が持ち、欄として写すと
    手の .pyi の欄の照らし(dataclass の欄の並び)にも欄と数えられた(#2886)。"""
    match statement:
        case ast.AnnAssign(target=ast.Name() as target, annotation=annotation, value=value) if not hidden_name(target.id):
            kind = _annotation(annotation) or annotation
            kept = None if value is None or _class_var(kind) else _default(value)
            return ast.AnnAssign(target=target, annotation=kind, value=kept, simple=1)
        case ast.Assign(targets=[ast.Name() as target], value=value) if not hidden_name(target.id):
            return ast.Assign(targets=[target], value=_default(value))
        case ast.FunctionDef() | ast.AsyncFunctionDef() if not hidden_name(statement.name):
            return _function(statement, method=True)
        case ast.ClassDef():
            return _class(statement)
        case _:
            return None


def _declared_answer(statement: ast.stmt, type_vars: frozenset[str]) -> ast.expr | None:
    """defeffect の class の本体の文が答えの型の置き場(`__doeff_answer__ = _doeff_cast(object, (A, B, …))`)なら、その要素の和。
    要素が型の式でない(`(type None)` のような実行時の値)なら None。module の型の引数の名(type_vars)は型と読む
    (`:answer (| T SqlFailed SqlUnreachable)` — 名が 1 文字の大文字で class の名の形の判定に当たらない・#2925)。"""
    match statement:
        case ast.AnnAssign(
            target=ast.Name(id="__doeff_answer__"),
            value=ast.Call(func=ast.Name(id="_doeff_cast"), args=[_, ast.Tuple(elts=[_, *_] as elements)]),
        ) if all(_type_like(element) or (isinstance(element, ast.Name) and element.id in type_vars) for element in elements):
            return _union(elements)
        case _:
            return None


def _effect_base(base: ast.expr, answer: ast.expr | None) -> ast.expr:
    """defeffect の基底(素の EffectBase)に答えの型を載せるため — 使い手の `<-`(型検査の展開では `_doeff_perform(e)` = e の
    Program[T] の T)が答えを読めるように(素のままだと使い手には答えが Unknown — agora-redesign #2886)。型検査の展開の
    defeffect の基底は素のまま(定義する module の検に、素の総称の答えの赤を足さない — #2322)で、.pyi だけが答えを持つ。"""
    match base:
        case ast.Name(id="_doeff_effect_base") if answer is not None:
            return _subscript("_doeff_effect_base", answer)
        case _:
            return base


def _member_name(member: ast.stmt) -> str | None:
    """class の本体の宣言 1 つが束ねる名(__init__ の欄を重ねて宣言しないため)。"""
    match member:
        case ast.AnnAssign(target=ast.Name(id=name)) | ast.Assign(targets=[ast.Name(id=name)]):
            return name
        case ast.FunctionDef(name=name) | ast.AsyncFunctionDef(name=name) | ast.ClassDef(name=name):
            return name
        case _:
            return None


@dataclass(frozen=True)
class _Placed:
    """__init__ の中で self に置く欄 1 つ — 名と、読める型(読めなければ None)。"""

    name: str
    kind: ast.expr | None


def _argument_type(init: ast.FunctionDef, name: str) -> ast.expr | None:
    """__init__ の引数 name の注記(引数でない・注記が無いなら None)— 引数をそのまま欄に置く時の欄の型を読むため。"""
    listed = [*init.args.posonlyargs, *init.args.args, *init.args.kwonlyargs]
    found = next((a.annotation for a in listed if a.arg == name and a.annotation is not None), None)
    return None if found is None else _annotation(found) or found


def _placed(statement: ast.AST, init: ast.FunctionDef) -> _Placed | None:
    """__init__ の中の文 1 つが self に置く欄(self の欄を置かない文は None)。注記つきの置き方は注記を、引数をそのまま置く
    置き方はその引数の注記を型と読む。"""
    match statement:
        case ast.AnnAssign(target=ast.Attribute(value=ast.Name(id="self"), attr=attr), annotation=annotation):
            return _Placed(attr, _annotation(annotation) or annotation)
        case ast.Assign(targets=[ast.Attribute(value=ast.Name(id="self"), attr=attr)], value=ast.Name(id=name)):
            return _Placed(attr, _argument_type(init, name))
        case ast.Assign(targets=[ast.Attribute(value=ast.Name(id="self"), attr=attr)]):
            return _Placed(attr, None)
        case _:
            return None


def _init_attributes(node: ast.ClassDef, declared: frozenset[str]) -> list[ast.stmt]:
    """__init__ の中で self に置く欄の宣言。型検査は .pyi に書かれていない欄を読めないので、method だけを宣言した class は欄を
    求める Protocol を満たさなかった(WalStore と ByteLog — agora-redesign #2972)。型は注記つきの置き方(`(setv #^ T self.x …)`)の
    T か、引数をそのまま置く時はその引数の注記。どちらでもない欄は型を推さず Incomplete にする(書き手が注記を足す先として
    報告される)。class の本体で既に宣言した名と、`_` で始まる私的な欄(使い手が読む型の面ではない — 出すと型の分からない欄の
    報告が増えるだけ)は足さない。"""
    init = next((s for s in node.body if isinstance(s, ast.FunctionDef) and s.name == "__init__"), None)
    if init is None:
        return []
    placed = [found for statement in ast.walk(init) if (found := _placed(statement, init)) is not None]
    names = dict.fromkeys(p.name for p in placed if not p.name.startswith("_") and p.name not in declared)
    return [
        ast.AnnAssign(
            target=_name(name),
            annotation=next((p.kind for p in placed if p.name == name and p.kind is not None), _name(_INCOMPLETE)),
            value=None,
            simple=1,
        )
        for name in names
    ]


def _class(node: ast.ClassDef, type_vars: frozenset[str] = frozenset()) -> ast.ClassDef:
    """class 1 つの宣言(飾り・基底・欄・method の形を残し、本体の式は外す)。type_vars = module の型の引数の名(答えの型を読むため)。"""
    members = [member for statement in node.body if (member := _member(statement)) is not None]
    declared = frozenset(name for member in members if (name := _member_name(member)) is not None)
    body = [*_init_attributes(node, declared), *members]
    answer = next(
        (found for statement in node.body if (found := _declared_answer(statement, type_vars)) is not None), None
    )
    return ast.ClassDef(
        name=node.name,
        bases=[_effect_base(base, answer) for base in node.bases],
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
                if a.name != "*" and not hidden_name(a.asname or a.name) and not _macro_helper(module, a.name)
            )
        case ast.Import(names=names):
            return tuple(
                ast.Import(names=[ast.alias(name=a.name, asname=a.asname or (a.name if "." not in a.name else None))])
                for a in names
                if a.name.split(".")[0] != "hy" and not hidden_name(a.asname or a.name)
            )


def _step(state: _Scan, statement: ast.stmt) -> _Scan:
    """module の直下の文 1 つを読み、宣言・公開し直す import・定数の型を足した次の状態を返す
    (型の面を持たない文 — 式・if・try など — は読み捨てる)。"""
    match statement:
        case ast.Import() | ast.ImportFrom():
            return replace(state, imports=(*state.imports, *_reexports(statement)))
        case ast.FunctionDef() | ast.AsyncFunctionDef():
            return state.defined(statement.name, _function(statement, method=False), public=not hidden_name(statement.name))
        case ast.ClassDef() if not hidden_name(statement.name):
            return state.declared(statement.name, _class(statement, state.type_vars()))
        case ast.AnnAssign(target=ast.Name(id=name), annotation=annotation, value=value) if not hidden_name(name):
            kind = _annotation(annotation) or annotation
            alias = isinstance(kind, ast.Name) and kind.id == "TypeAlias"
            node = ast.AnnAssign(target=_name(name), annotation=kind, value=value if alias else None, simple=1)
            return state.declared(name, node)
        case ast.Assign(targets=[ast.Name(id=name)], value=value) if not hidden_name(name):
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


def _unread_module_import(line: ast.stmt, reads: set[str]) -> bool:
    """module の import(`import a.b`・`import os as os`)で、宣言がその束ねる名を読まない物か — 写さないため。使い手は module を
    `from m import os` と引かないので、公開し直す意味が無く、.pyi に依存だけを足していた: defrecord の展開が引く `doeff_hy.record`
    は品質検査の module の依存の契約に(#2886)、`import os` は「純粋な層から IO の API へ依存」に当たった(#2925)。
    宣言が `a.b.X`・`os.PathLike` を読むなら残す。名を引く import(`from m import X as X`)は公開し直すので外さない。"""
    match line:
        case ast.Import(names=[ast.alias(name=name, asname=asname)]):
            return (asname or name.split(".")[0]) not in reads
        case _:
            return False


def _render(state: _Scan, source_name: str) -> StubText:
    """読み終えた状態から .pyi の本文を組む(補助の型の import は宣言が読む物だけ・同じ import は 1 つに)。"""
    declared = state.declarations
    body = [d.node for index, d in enumerate(declared) if d.name not in {e.name for e in declared[index + 1 :]}]
    keys = [ast.unparse(line) for line in state.imports]
    reads = {n.id for statement in body for n in ast.walk(statement) if isinstance(n, ast.Name)}
    imports = [
        line
        for index, line in enumerate(state.imports)
        if keys[index] not in keys[:index] and not _unread_module_import(line, reads)
    ]
    bound = {name for line in imports for name in _bound(line)}
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


def strict_errors(workdir: Path, probe_text: str) -> tuple[str, ...]:
    """検の module(Hy の source)を使い手の型の門と同じ strict で検め、赤を「行: 規則: 文」で返す — 各 package の検が、.pyi の型が
    使い手の書き方に届くか(取り違えが赤になるか)を確かめるため。"""
    probe = workdir / "probe.hy"
    probe.write_text(probe_text, encoding="utf-8")
    command = [sys.executable, "-m", "doeff_hy.static_check", "--root", str(workdir), "--json", "--strict", "--no-cache", str(probe)]
    done = subprocess.run(command, capture_output=True, text=True, timeout=240, check=False)
    diagnostics: list[dict[str, object]] = json.loads(done.stdout) if done.stdout.strip() else []
    return tuple(f"{d['line']}: {d['rule']}: {d['message']}" for d in diagnostics if d["severity"] == "error")


def unknown_in_users(workdir: Path, package: str, used: tuple[UsedModule, ...]) -> tuple[str, ...]:
    """使い手の名を束ねた検の module を strict で検め、「型が分からない」と Hy の compile の赤を返す
    (各 package の検が、.pyi が使い手に届いているかを 1 行で確かめるため — 空なら使い手はどの名も型つきで読める)。"""
    errors = strict_errors(workdir, users_probe(package, used))
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
