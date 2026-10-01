"""系の宣言の 3 つの module の型の宣言(.pyi)と実装の食い違いの失敗ケース。

service_model.hy の判断と構成子を、層に合わせて core/service_rules.hy と entry/service_build.hy へ分けた。型の宣言は手書きなので、
関数を移す・引数を変えると、宣言だけが古い置き場に黙って残る(使い手の strict では Unknown か、実行時に無い名を型が通す)。
ここで .hy の source を読み、module の直下で定義した公開の名と宣言の名を照らし、さらに:

- defrecord の型は dataclass の欄の名と順が宣言の欄と同じ。
- deff / defk の関数は引数の名と順が同じ。
- doeff-cluster の Hy の file(src と検)が 3 つの module から import する名は、どれも宣言に在る。
"""

import ast
import dataclasses
import inspect
import re
import types
from pathlib import Path

import hy  # .hy の module を読む import hook を有効にする(名の mangle と source の読みにも使う)
import pytest
from doeff_cluster.shared.core import service_rules
from doeff_cluster.shared.entry import service_build
from doeff_cluster.shared.intent import service_model

PACKAGE = Path(__file__).resolve().parents[1]
SOURCE = PACKAGE / "src"

#: 型の宣言を持つ系の宣言の module。
MODULES = (service_model, service_rules, service_build)

#: module の直下で名を定義する Hy の form の頭。
DEFINING_HEADS = frozenset({"defrecord", "defclass", "defk", "deff", "val", "var", "setv"})

#: 宣言に載せない名(linter が読む印 — 使い手は読まない)。
BOOKKEEPING = frozenset({"MODULE_TAGS"})


def _path(module: types.ModuleType, suffix: str) -> Path:
    return SOURCE / (module.__name__.replace(".", "/") + suffix)


def _defined_name(form: object) -> str | None:
    """.hy の直下の form 1 つが定義する名(Python の名へ mangle した物)。定義の form でなければ None。"""
    match form:
        case hy.models.Expression() if (
            len(form) >= 2 and str(form[0]) in DEFINING_HEADS and isinstance(form[1], hy.models.Symbol)
        ):
            return hy.mangle(str(form[1]))
        # 飾りの付いた class(`(defclass [runtime-checkable] 名 [Protocol] …)`)は名が 3 つ目に来る。
        case hy.models.Expression() if (
            len(form) >= 3
            and str(form[0]) == "defclass"
            and isinstance(form[1], hy.models.List)
            and isinstance(form[2], hy.models.Symbol)
        ):
            return hy.mangle(str(form[2]))
        case _:
            return None


def _hy_defined_names(module: types.ModuleType) -> tuple[str, ...]:
    """.hy の module の直下で定義した公開の名(書いた順)。"""
    forms = hy.read_many(_path(module, ".hy").read_text(encoding="utf-8"))
    names = (_defined_name(form) for form in forms)
    return tuple(n for n in names if n is not None and not n.startswith("_") and n not in BOOKKEEPING)


def _stub_tree(module: types.ModuleType) -> ast.Module:
    return ast.parse(_path(module, ".pyi").read_text(encoding="utf-8"))


def _declared_name(node: ast.stmt) -> str | None:
    """宣言の直下の文 1 つが宣言する名(import は宣言でない — None)。"""
    match node:
        case ast.ClassDef(name=name) | ast.FunctionDef(name=name) | ast.AnnAssign(target=ast.Name(id=name)):
            return name
        case ast.Assign(targets=[ast.Name(id=name)]):
            return name
        case _:
            return None


def _stub_declared_names(tree: ast.Module) -> tuple[str, ...]:
    """宣言の module の直下で宣言した公開の名(_ で始まる宣言の中だけの型は除く)。"""
    names = (_declared_name(node) for node in tree.body)
    return tuple(n for n in names if n is not None and not n.startswith("_"))


def _stub_fields(node: ast.ClassDef) -> tuple[str, ...]:
    return tuple(
        stmt.target.id
        for stmt in node.body
        if isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name)
    )


def _stub_parameters(node: ast.FunctionDef) -> tuple[str, ...]:
    args = node.args
    return tuple(a.arg for a in (*args.posonlyargs, *args.args, *args.kwonlyargs))


def _mismatches(runtime: types.ModuleType, tree: ast.Module) -> tuple[str, ...]:
    """宣言(tree)と実装の食い違いの一覧(空 = 一致)。"""
    defined = _hy_defined_names(runtime)
    declared = _stub_declared_names(tree)
    missing = tuple(f"宣言に無い公開の名: {n}" for n in defined if n not in declared)
    extra = tuple(f"実装に無い宣言の名: {n}" for n in declared if n not in defined)
    public = [node for node in tree.body if _declared_name(node) in defined]
    fields = tuple(
        f"{node.name} の欄が違う"
        for node in public
        if isinstance(node, ast.ClassDef)
        # 欄を照らすのは dataclass そのもの(record の印 RecordArgument は欄の宣言 __dataclass_fields__ を持つ Protocol で、欄は無い)。
        and "__dataclass_params__" in vars(getattr(runtime, node.name))
        and _stub_fields(node) != tuple(f.name for f in dataclasses.fields(getattr(runtime, node.name)))
    )
    parameters = tuple(
        f"{node.name} の引数が違う"
        for node in public
        if isinstance(node, ast.FunctionDef)
        and tuple(inspect.signature(getattr(runtime, node.name)).parameters) != _stub_parameters(node)
    )
    return missing + extra + fields + parameters


@pytest.mark.parametrize("module", MODULES, ids=lambda m: m.__name__.rsplit(".", 1)[-1])
def test_the_stub_matches_the_hy_module(module: types.ModuleType) -> None:
    assert _mismatches(module, _stub_tree(module)) == ()


def test_a_function_left_in_the_old_stub_is_found() -> None:
    # 構成子 job の宣言を service_model.pyi に戻す(移した後に古い置き場へ残した形)と、実装に無い宣言の名として見つかる。
    stub = _stub_tree(MODULES[0])
    broken = ast.Module(body=[*stub.body, *ast.parse("def job(name: str) -> object: ...").body], type_ignores=[])
    assert "実装に無い宣言の名: job" in _mismatches(MODULES[0], broken)


def test_the_stubs_declare_every_name_the_package_imports() -> None:
    # doeff-cluster の Hy の file(src と検)が 3 つの module から import する名は全部宣言に在る。
    for module in MODULES:
        form = re.compile(r"\(import " + re.escape(module.__name__) + r" \[([^\]]*)\]")
        used: set[str] = set()
        for path in sorted(PACKAGE.rglob("*.hy")):
            for match in form.finditer(path.read_text(encoding="utf-8")):
                words = re.sub(r":as \S+", "", match.group(1)).split()
                used |= {hy.mangle(word) for word in words}
        assert used, module.__name__
        assert sorted(used - set(_stub_declared_names(_stub_tree(module)))) == [], module.__name__
