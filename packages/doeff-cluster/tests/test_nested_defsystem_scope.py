"""関数の本体の中に入れ子で書いた doeff_cluster.macros の定義の macro(defsystem)が、外の関数に局所の名を作らない事の検
(macro の全部を検体で数え、型検査の投影も検める)。

root の tests/test_nested_definition_scope.py(doeff_hy.macros・doeff_hy.record が出す定義の macro の検)から、持ち主の package
の macro の分を分けた(2026-11-04 — root の tests が内輪の package を import しないため)。検め方は root の検と同じ:
defsystem は展開の頭で `doeff_hy`・`_doeff_cluster_service_model`・`_doeff_cluster_service_build` を import する。関数の本体の中で
それを import すると名が関数全体の局所の名になり、同じ関数で先に読む所が UnboundLocalError で落ちる。型検査の投影では、
関数・class の本体の中の展開の import が局所の名にならず、module の段で解ける事を検める。
"""

import ast
import symtable
import sys
import types
from collections.abc import Callable
from pathlib import Path

import hy
import pytest
from doeff_hy._static_check import Projection, project
from doeff_hy_bytecode_guard._records import MACRO_TABLE

# 入れ子の定義の補助の名(doeff_hy の定義の macro の補助と、doeff_cluster.macros の補助)
_SCOPE_LEAKING_NAMES = frozenset(
    {
        "_doeff_do",
        "_doeff_effect_base",
        "_install_guard_globals",
        "_guard_performed",
        "_guard_statement_value",
        "_doeff_check_program_return",
        "_doeff_QuotedForms",
        "doeff_hy",
        "pytest",
        "_doeff_cluster_service_model",
        "_doeff_cluster_service_build",
    }
)

# module の段の defk が 1 つ在る(書き手の module の普通の形)— 入れ子の外の関数が先に読む補助は module の globals に在る
_HEADER = """
(require doeff-hy.macros [defk])
(require doeff-cluster.macros [defsystem])
(defk _module-level [] {:pre [] :post [(: % int)]} 0)
"""


def _eval_module(code: str, header: str = _HEADER) -> types.ModuleType:
    """Hy の source を新しい module に読み込む — 検ごとに module の globals を分けるため。"""
    mod = types.ModuleType("nested_defsystem_scope")
    sys.modules["nested_defsystem_scope"] = mod
    for form in hy.read_many(header + code):
        hy.eval(form, mod.__dict__, module=mod)
    return mod


_CASES = {
    # 外の関数が module の段の doeff_hy を読んだ後に、入れ子の defsystem の import(doeff_hy・doeff_cluster)が来る
    "defsystem-after-module-doeff-hy-read": (
        """
        (defn subject [x]
          (setv owner (. doeff_hy __name__))
          (defsystem lab [#^ object foundation] "lab" (job (fn [] None) :replicas 1 :peak-mib 200 :settle-mib 100))
          #(owner (. lab __name__) x))
        """,
        lambda mod: mod.subject(5),
        ("doeff_hy", "lab", 5),
    ),
}


@pytest.mark.parametrize(("source", "call", "expected"), _CASES.values(), ids=_CASES.keys())
def test_nested_definition_runs(
    source: str, call: Callable[[types.ModuleType], object], expected: object
) -> None:
    assert call(_eval_module(source)) == expected


@pytest.mark.parametrize(("source", "call", "expected"), _CASES.values(), ids=_CASES.keys())
def test_outer_function_binds_no_helper_name(
    source: str, call: Callable[[types.ModuleType], object], expected: object
) -> None:
    """入れ子の定義の補助の名は、外の関数の局所(と閉包の cell)に現れない — 現れると先の読みが未束縛になる。"""
    subject = _eval_module(source).subject
    code = getattr(subject, "__doeff_generator_function__", subject).__code__
    assert _SCOPE_LEAKING_NAMES.isdisjoint(code.co_varnames + code.co_cellvars)


# doeff_cluster.macros が出す定義の macro ごとの、関数の本体の中に置く検体と、その検体が外の関数に束ねてよい名(定義した名)。
# macro を足したら検体も足す — 足さないと test_every_definition_macro_has_a_nested_sample が赤になる。
_DEFINITION_SAMPLES: dict[str, tuple[str, frozenset[str]]] = {
    "defsystem": (
        '(defsystem lab [#^ object foundation] "lab" (job (fn [] None) :replicas 1 :peak-mib 200 :settle-mib 100))',
        frozenset({"lab"}),
    ),
}


def _owner_macro_names() -> set[str]:
    """`(require doeff-cluster.macros *)` が書き手の module に引く macro の名 — 持ち主の macro の module は Python から
    import せず、書き手と同じに Hy の require で読む。"""
    reader = types.ModuleType("owner_macro_names")
    hy.eval(hy.read("(require doeff-cluster.macros *)"), reader.__dict__, module=reader)
    return {hy.unmangle(name) for name in vars(reader)[MACRO_TABLE]}


def test_every_definition_macro_has_a_nested_sample() -> None:
    """doeff_cluster.macros が出す定義の macro には、関数の本体の中に置く検体が要る — 検体の無い macro は、補助の import が
    外の関数の局所の名になるかを誰も検めない(足した macro は検体を足すまで赤)。"""
    defined = {name for name in _owner_macro_names() if name.startswith("def")}
    assert defined == set(_DEFINITION_SAMPLES)


@pytest.mark.parametrize(
    ("source", "defined"), _DEFINITION_SAMPLES.values(), ids=_DEFINITION_SAMPLES.keys()
)
def test_function_with_only_a_definition_binds_only_its_names(
    source: str, defined: frozenset[str]
) -> None:
    """定義 1 つだけを書いた関数の局所(と閉包の cell)は、定義した名と Hy が作る一意の名(`_hy_` の頭)の外に無い。"""
    subject = _eval_module(f"(defn subject [] {source} None)").subject
    names = set(subject.__code__.co_varnames + subject.__code__.co_cellvars)
    assert {name for name in names if not name.startswith("_hy_")} == defined


def _module_globals_view(mod: types.ModuleType) -> dict[str, tuple[int, int | None]]:
    """module の globals の名ごとの (値の id, 入れ物なら要素の数) — 外の関数を呼んで globals に名や記録が増えたかを比べるため。"""
    return {
        name: (id(value), len(value) if isinstance(value, list | dict | set | tuple) else None)
        for name, value in vars(mod).items()
    }


@pytest.mark.parametrize(("source", "call", "expected"), _CASES.values(), ids=_CASES.keys())
def test_nested_definition_leaves_module_globals_unchanged(
    source: str, call: Callable[[types.ModuleType], object], expected: object
) -> None:
    """関数の中の定義は、外の関数を何度呼んでも module の globals に名も記録も足さない。"""
    mod = _eval_module(source)
    before = _module_globals_view(mod)
    call(mod)
    call(mod)
    assert _module_globals_view(mod) == before


def _member_ids(value: object) -> tuple[int, ...] | None:
    """入れ物の要素(dict は鍵と値)の id の並び — 入れ物を替えずに中身を足す・差し替える登録を見分けるため。"""
    match value:
        case dict():
            return tuple(id(part) for item in value.items() for part in item)
        case list() | tuple() | set() | frozenset():
            return tuple(id(member) for member in value)
        case _:
            return None


def _module_state_view(
    mod: types.ModuleType,
) -> dict[tuple[str, str], tuple[int, tuple[int, ...] | None]]:
    """定義を書いた module と、読み込み済みの doeff の module(名の頭が doeff)の globals の (module, 名) ごとの
    (値の id, 入れ物の要素の id)。"""
    modules = [mod] + [
        module
        for name, module in sorted(sys.modules.items())
        if name.split(".")[0].startswith("doeff") and isinstance(module, types.ModuleType)
    ]
    return {
        (module.__name__, name): (id(value), _member_ids(value))
        for module in modules
        for name, value in list(vars(module).items())
    }


@pytest.mark.parametrize(
    ("source", "defined"), _DEFINITION_SAMPLES.values(), ids=_DEFINITION_SAMPLES.keys()
)
def test_definition_sample_leaves_module_state_unchanged(
    source: str, defined: frozenset[str]
) -> None:
    """定義の macro の検体を関数の中に書くと、外の関数を何度呼んでも、定義を書いた module にも doeff の module にも
    名・記録・登録を足さない。初めの呼びは補助の module を読み込むので、比べるのは 2 回目と 3 回目の呼びの前後。"""
    mod = _eval_module(f"(defn subject [] {source} None)")
    mod.subject()
    before = _module_state_view(mod)
    mod.subject()
    mod.subject()
    after = _module_state_view(mod)
    assert {
        key for key in before.keys() | after.keys() if before.get(key) != after.get(key)
    } == set()


# 型検査の投影の検が使う header — 書き手の module が `doeff_hy` を import している形(外の関数が module の段の `doeff_hy` を読む)。
_PROJECTION_HEADER = """
(require doeff-hy.macros [defk])
(require doeff-cluster.macros [defsystem])
(import doeff_hy)
"""

# macro が展開の import に付ける別名の頭(expansion_names.PREFIX)と、根の module の名。
_PROJECTION_MACRO_NAME_PREFIXES = ("_doeff_",)
_PROJECTION_ROOT_NAMES = frozenset({"doeff_hy", "doeff_core_effects", "doeff_cluster"})


def _projection_of(tmp_path: Path, body: str) -> ast.Module:
    """書き手の module(`doeff_hy` を import し、定義の macro を require する)に body を書いた型検査の投影の AST。"""
    source = tmp_path / "nested_static.hy"
    source.write_text(_PROJECTION_HEADER + body + "\n", encoding="utf-8")
    projection = project(tmp_path, [tmp_path], source)
    assert isinstance(projection, Projection)
    return ast.parse(projection.text)


def _bound_by_imports(scope: ast.AST) -> set[str]:
    """scope の中(入れ子の関数・class の本体を含む)の import が束ねる名。"""
    return {
        alias.asname or alias.name.split(".")[0]
        for node in ast.walk(scope)
        if isinstance(node, ast.Import | ast.ImportFrom)
        for alias in node.names
    }


def _module_level_names(tree: ast.Module) -> set[str]:
    """module の段で束ねられた名(import・代入・定義)。"""
    table = symtable.symtable(ast.unparse(tree), "projection", "exec")
    return {
        symbol.get_name()
        for symbol in table.get_symbols()
        if symbol.is_imported() or symbol.is_assigned() or symbol.is_namespace()
    }


def _macro_names_read_from_module(tree: ast.Module) -> set[str]:
    """入れ子の scope が module の段の名として読む、macro の別名と根の module の名(名の引きは Python の規則 — symtable)。"""
    found: set[str] = set()

    def walk(table: symtable.SymbolTable) -> None:
        for symbol in table.get_symbols():
            name = symbol.get_name()
            macro_name = (
                name.startswith(_PROJECTION_MACRO_NAME_PREFIXES) or name in _PROJECTION_ROOT_NAMES
            )
            if macro_name and symbol.is_referenced() and symbol.is_global():
                found.add(name)
        for child in table.get_children():
            walk(child)

    walk(symtable.symtable(ast.unparse(tree), "projection", "exec"))
    return found


_STATIC_SCOPES_SOURCE = "(defn subject [] {source} None)\n(defclass Outer [] {source})"


# 型検査の投影で、展開が関数・class の本体の中に残す import の名(定義の macro ごと)。残る名は、その定義の本体と class の本体の
# 直下でだけ読まれ、入れ子の scope は読まない。
_IMPORTS_LEFT_IN_SCOPE: dict[str, frozenset[str]] = {}


@pytest.mark.parametrize("macro", _DEFINITION_SAMPLES.keys())
def test_static_projection_of_nested_definition_leaves_only_known_imports_in_its_scope(
    tmp_path: Path, macro: str
) -> None:
    """型検査の投影で、関数の本体と class の本体に書いた定義の展開が scope に残す import は、上の表の名だけ。"""
    source, _ = _DEFINITION_SAMPLES[macro]
    tree = _projection_of(tmp_path, _STATIC_SCOPES_SOURCE.format(source=source))
    allowed = _IMPORTS_LEFT_IN_SCOPE.get(macro, frozenset())
    scopes = {
        node.name: node
        for node in tree.body
        if isinstance(node, ast.FunctionDef | ast.ClassDef) and node.name in {"subject", "Outer"}
    }
    assert {name: _bound_by_imports(scope) - allowed for name, scope in scopes.items()} == {
        "subject": set(),
        "Outer": set(),
    }


@pytest.mark.parametrize(
    ("source", "defined"), _DEFINITION_SAMPLES.values(), ids=_DEFINITION_SAMPLES.keys()
)
def test_static_projection_nested_scopes_read_macro_names_bound_at_module_level(
    tmp_path: Path, source: str, defined: frozenset[str]
) -> None:
    """投影の入れ子の scope が module の段の名として読む macro の別名と根の module の名は、どれも module の段で束ねられている。"""
    tree = _projection_of(tmp_path, _STATIC_SCOPES_SOURCE.format(source=source))
    assert _macro_names_read_from_module(tree) <= _module_level_names(tree)
