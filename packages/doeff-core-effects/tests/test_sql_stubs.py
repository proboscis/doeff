"""sql_effects.pyi・sqlite_sql.pyi(Hy の module の隣の型の宣言)と実装の食い違いの失敗ケース(agora-redesign #2320)。

型の宣言は手書きなので、.hy の側で欄を足す・消す・並べ替える・関数の引数を変えると、宣言だけが古いまま黙って残る。
ここで .hy の source を読み、module の直下で定義した公開の名と宣言の名を照らし、さらに:

- defrecord / defeffect の型は dataclass の欄の名・順・既定値の有無が宣言の欄と同じ。
- defenum の型は member の名と値が同じ。
- defk / defhandler の関数は引数の名と順が同じ。
"""

import ast
import copy
import dataclasses
import enum
import importlib
import inspect
from dataclasses import dataclass
from pathlib import Path

import doeff_hy  # noqa: F401  # Hy の import hook を有効にする
import hy
import pytest

PACKAGE = Path(__file__).resolve().parent.parent / "doeff_core_effects"

#: 型の宣言を持つ Hy の module(宣言を足したらここへ並べる)。
STUBBED_MODULES = ("sql_effects", "sqlite_sql")

#: module の直下で名を定義する Hy の form の頭。
DEFINING_HEADS = frozenset(
    {
        "defrecord",
        "defenum",
        "defeffect",
        "defclass",
        "defk",
        "deff",
        "defhandler",
        "val",
        "var",
        "setv",
    }
)


@dataclass(frozen=True)
class StubField:
    """dataclass の欄 1 つ(名と既定値の有無)。"""

    name: str
    has_default: bool


def _defined_name(form: object) -> str | None:
    """.hy の直下の form 1 つが定義する名(Python の名へ mangle した物)。定義の form でなければ None。"""
    match form:
        case hy.models.Expression() if (
            len(form) >= 2
            and str(form[0]) in DEFINING_HEADS
            and isinstance(form[1], hy.models.Symbol)
        ):
            return hy.mangle(str(form[1]))
        case _:
            return None


def _hy_defined_names(module: str) -> tuple[str, ...]:
    """.hy の module の直下で定義した公開の名(書いた順)。"""
    forms = hy.read_many((PACKAGE / f"{module}.hy").read_text(encoding="utf-8"))
    names = (_defined_name(form) for form in forms)
    return tuple(n for n in names if n is not None and not n.startswith("_"))


def _stub_tree(module: str) -> ast.Module:
    return ast.parse((PACKAGE / f"{module}.pyi").read_text(encoding="utf-8"))


def _declared_name(node: ast.stmt) -> str | None:
    """宣言の直下の文 1 つが宣言する名(import は宣言でない — None)。"""
    match node:
        case (
            ast.ClassDef(name=name)
            | ast.FunctionDef(name=name)
            | ast.AnnAssign(target=ast.Name(id=name))
        ):
            return name
        case ast.Assign(targets=[ast.Name(id=name)]):
            return name
        case _:
            return None


def _stub_declared_names(tree: ast.Module) -> tuple[str, ...]:
    """宣言の module の直下で宣言した公開の名(_ で始まる宣言の中だけの型は除く)。"""
    names = (_declared_name(node) for node in tree.body)
    return tuple(n for n in names if n is not None and not n.startswith("_"))


def _stub_fields(node: ast.ClassDef) -> tuple[StubField, ...]:
    """宣言の class の本体の注記した欄(書いた順)。"""
    return tuple(
        StubField(name=stmt.target.id, has_default=stmt.value is not None)
        for stmt in node.body
        if isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name)
    )


def _stub_enum_members(node: ast.ClassDef) -> tuple[tuple[str, str], ...]:
    """宣言の StrEnum の member(名と値・書いた順)。"""
    return tuple(
        (stmt.targets[0].id, stmt.value.value)
        for stmt in node.body
        if isinstance(stmt, ast.Assign)
        and isinstance(stmt.targets[0], ast.Name)
        and isinstance(stmt.value, ast.Constant)
        and isinstance(stmt.value.value, str)
    )


def _runtime_fields(cls: type) -> tuple[StubField, ...]:
    return tuple(
        StubField(
            name=f.name,
            has_default=f.default is not dataclasses.MISSING
            or f.default_factory is not dataclasses.MISSING,
        )
        for f in dataclasses.fields(cls)
    )


def _stub_parameters(node: ast.FunctionDef) -> tuple[str, ...]:
    args = node.args
    return tuple(a.arg for a in (*args.posonlyargs, *args.args, *args.kwonlyargs))


def _class_mismatch(name: str, value: object, node: ast.ClassDef) -> str | None:
    """宣言の class 1 つと実装の型の食い違い(None = 一致・照らす形でない)。"""
    match value:
        case type() if issubclass(value, enum.Enum):
            actual = tuple((m.name, str(m.value)) for m in value)
            declared = _stub_enum_members(node)
            return (
                None
                if actual == declared
                else f"{name} の member が違う: 実装 {actual} / 宣言 {declared}"
            )
        case type() if dataclasses.is_dataclass(value):
            actual_fields = _runtime_fields(value)
            declared_fields = _stub_fields(node)
            return (
                None
                if actual_fields == declared_fields
                else f"{name} の欄が違う: 実装 {actual_fields} / 宣言 {declared_fields}"
            )
        case _:
            return None


def _function_mismatch(name: str, value: object, node: ast.FunctionDef) -> str | None:
    """宣言の関数 1 つと実装の関数の引数の食い違い(None = 一致)。"""
    match value:
        case _ if callable(value):
            actual = tuple(inspect.signature(value).parameters)
            declared = _stub_parameters(node)
            return (
                None
                if actual == declared
                else f"{name} の引数が違う: 実装 {actual} / 宣言 {declared}"
            )
        case _:
            return f"{name} は実装で関数でない"


def _mismatches(module: str, tree: ast.Module) -> tuple[str, ...]:
    """宣言(tree)と実装の食い違いの一覧(空 = 一致)。"""
    runtime = importlib.import_module(f"doeff_core_effects.{module}")
    defined = _hy_defined_names(module)
    declared = _stub_declared_names(tree)
    public = [node for node in tree.body if _declared_name(node) in defined]
    missing = tuple(f"宣言に無い公開の名: {n}" for n in defined if n not in declared)
    extra = tuple(f"実装に無い宣言の名: {n}" for n in declared if n not in defined)
    classes = (
        _class_mismatch(node.name, getattr(runtime, node.name), node)
        for node in public
        if isinstance(node, ast.ClassDef)
    )
    functions = (
        _function_mismatch(node.name, getattr(runtime, node.name), node)
        for node in public
        if isinstance(node, ast.FunctionDef)
    )
    return missing + extra + tuple(m for m in (*classes, *functions) if m is not None)


@pytest.mark.parametrize("module", STUBBED_MODULES)
def test_the_stub_matches_the_hy_module(module: str) -> None:
    assert _mismatches(module, _stub_tree(module)) == ()


def _without_field(tree: ast.Module, class_name: str, field: str) -> ast.Module:
    """宣言の class class_name から欄 field を消した写し。"""
    broken = copy.deepcopy(tree)
    for node in broken.body:
        match node:
            case ast.ClassDef(name=name) if name == class_name:
                node.body = [
                    s
                    for s in node.body
                    if not (isinstance(s, ast.AnnAssign) and ast.unparse(s.target) == field)
                ]
            case _:
                pass
    return broken


def test_a_dropped_stub_field_is_found() -> None:
    # 宣言の SqlRows から欄 rowcount を 1 つ消すと、欄の食い違いとして見つかる(検が黙って通らない)。
    broken = _without_field(_stub_tree("sql_effects"), "SqlRows", "rowcount")
    assert [m for m in _mismatches("sql_effects", broken) if m.startswith("SqlRows の欄が違う")]
