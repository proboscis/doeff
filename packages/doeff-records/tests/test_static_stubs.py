"""doeff_records の値・effect・memory の置き場の型(values.pyi・effects.pyi・faults.pyi・memory.pyi・store_choice.pyi)の検
(agora-redesign #2311・#2245)。

doeff_records の module は Hy なので、型の宣言(.pyi)が無いと pyright は import した名を全部 Unknown として読み、使う側
(agora の表の宣言・行の読みの答え・変更の欄・memory の置き場の handler)に書き手に直せない reportUnknown* が連なる。

- 失敗ケース: 同じ小さな .hy を、stub を外した写しと stub を置いた写しの 2 通りで doeff-hy-check --strict にかける。
  外すと import した名・`(<- answer (ReadRow …))` の answer・変更の欄が Unknown の赤になり、置くと消える。置いた側では
  型の取り違え(int の版に文字列を足す)が赤になる(stub が型を運んでいる)。
- 一致: stub が宣言する名は実行時の module に在り、dataclass の欄の名・順・既定値の有無、関数の引数の名、答えの union の
  型の並びが実装と同じ(宣言だけが先へ行かない)。
"""

import ast
import contextlib
import dataclasses
import inspect
import io
import json
import shutil
import types
import typing
from dataclasses import dataclass
from pathlib import Path

import pytest

import doeff_hy  # noqa: F401  # Hy の import hook を有効にする
from doeff import EffectBase
from doeff_records import effects, faults, memory, store_choice, values

needs_pyright = pytest.mark.skipif(shutil.which("pyright") is None, reason="pyright が無い")

PACKAGE = Path(values.__file__).parent
STUBBED: tuple[types.ModuleType, ...] = (values, effects, faults, memory, store_choice)

MODULE = """\
(require doeff-hy.macros [defk <- val])
(import doeff_records.values [RecordsSchema Row RowChanged Changes])
(import doeff_records.effects [ReadRow WatchChanges])
(import doeff_records.memory [MemoryStore memory-records-handler])

(val SCHEMA (RecordsSchema))
(val HANDLER (memory-records-handler (MemoryStore SCHEMA) "writer"))

(defk version-of [table key]
  {:pre [(: table str) (: key str)] :post [(: % int)]}
  (<- answer (ReadRow table #(key)))
  (match answer
    (Row :version version) version
    _ 0))

(defk changed-tables [watch]
  {:pre [(: watch WatchChanges)] :post [(: % int)]}
  (<- answer (WatchChanges watch.tables watch.cursor))
  (match answer
    (Changes :items items) (len (lfor change items :if (isinstance change RowChanged) change.table))
    _ 0))

(defk store-of [schema]
  {:pre [(: schema RecordsSchema)] :post [(: % MemoryStore)]}
  (MemoryStore schema))

(defk wrong-version [table key]
  {:pre [(: table str) (: key str)] :post [(: % int)]}
  (<- answer (ReadRow table #(key)))
  (match answer
    (Row :version version) (+ version "x")
    _ 0))
"""

#: stub を外すと Unknown になる名(import の行の名・答えを受けた変数・変更の欄)。
UNKNOWN_NAMES: tuple[str, ...] = (
    '"RecordsSchema"',
    '"Row"',
    '"ReadRow"',
    '"WatchChanges"',
    '"MemoryStore"',
    '"memory_records_handler"',
    '"answer"',
    '"version"',
    '"items"',
    '"table"',
)


@dataclass(frozen=True)
class Run:
    """doeff-hy-check を 1 回走らせた結果(JSON の診断)。"""

    diagnostics: tuple[dict[str, object], ...]

    def errors(self) -> list[tuple[str, int, str]]:
        return [
            (str(d["rule"]), int(str(d["line"])), str(d["message"]))
            for d in self.diagnostics
            if d["severity"] == "error"
        ]

    def unknown(self) -> list[tuple[str, int, str]]:
        return [e for e in self.errors() if e[0].startswith("reportUnknown")]


def _line_of(marker: str) -> int:
    """検体の中で marker を含む行の番号(1 から)。"""
    return next(n for n, line in enumerate(MODULE.splitlines(), start=1) if marker in line)


def _check(tmp_path: Path, *, with_stubs: bool) -> Run:
    """doeff_records の写し(.hy と、with_stubs なら .pyi)を import の根に置き、検体を doeff-hy-check --strict にかける。

    写しは __init__.py を持つ package にする — 名前空間の package のままだと pyright は写しに無い module を次の根(入れた
    doeff_records)へ探しに行き、外したはずの stub を読む。
    """
    from doeff_hy.static_check import main

    copy = tmp_path / ("stubbed" if with_stubs else "bare") / "doeff_records"
    copy.mkdir(parents=True)
    (copy / "__init__.py").write_text("", encoding="utf-8")
    for source in PACKAGE.iterdir():
        if source.suffix == ".hy" or (with_stubs and source.suffix == ".pyi"):
            shutil.copy(source, copy / source.name)
    root = tmp_path / "root"
    root.mkdir()
    (root / "probe.hy").write_text(MODULE, encoding="utf-8")
    (root / "pyrightconfig.json").write_text(json.dumps({"extraPaths": [str(copy.parent)]}), encoding="utf-8")
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        main(["--root", str(root), "--json", "--strict", "--no-cache", str(root / "probe.hy")])
    text = out.getvalue()
    return Run(tuple(json.loads(text)) if text.strip() else ())


@needs_pyright
def test_without_the_stubs_the_imported_names_are_unknown(tmp_path: Path) -> None:
    unknown = _check(tmp_path, with_stubs=False).unknown()
    missing = [name for name in UNKNOWN_NAMES if not any(name in e[2] for e in unknown)]
    assert missing == [], unknown


@needs_pyright
def test_with_the_stubs_nothing_is_unknown_and_a_wrong_type_is_red(tmp_path: Path) -> None:
    run = _check(tmp_path, with_stubs=True)
    # 正しい使い(検体の頭から wrong-version の前まで)には赤が 1 つも無い — Unknown も型の取り違えも。
    right = range(1, _line_of("(defk wrong-version"))
    assert [e for e in run.errors() if e[1] in right] == [], run.errors()
    # int の版に文字列を足すと赤(stub が答えの型 Row | Missing | Unreachable を運び、Row の版が int と読める)。
    wrong = _line_of('(+ version "x")')
    assert [e for e in run.errors() if e[1] == wrong and e[0] == "reportOperatorIssue"], run.errors()


# --- stub と実装の一致 ---------------------------------------------------------------------------------------------


def _stub_of(module: types.ModuleType) -> ast.Module:
    return ast.parse(Path(str(module.__file__)).with_suffix(".pyi").read_text(encoding="utf-8"))


def _public(name: str) -> bool:
    return not name.startswith("_")


def _names_of(node: ast.stmt) -> tuple[str, ...]:
    """stub の直下の文 1 つが宣言する名(class・関数・注記つきの名・型の別名・`as` の再公開)。"""
    match node:
        case ast.ClassDef(name=name) | ast.FunctionDef(name=name) | ast.AnnAssign(target=ast.Name(id=name)):
            return (name,)
        case ast.ImportFrom(names=aliases):
            return tuple(a.asname for a in aliases if a.asname is not None)
        case _:
            return ()


def _declared_names(tree: ast.Module) -> list[str]:
    """stub の直下で宣言した公開の名。"""
    return [name for node in tree.body for name in _names_of(node) if _public(name)]


def _is_dataclass_stub(node: ast.ClassDef) -> bool:
    return any(
        isinstance(d, ast.Call) and isinstance(d.func, ast.Name) and d.func.id == "dataclass" for d in node.decorator_list
    )


def _stub_fields(node: ast.ClassDef) -> list[tuple[str, bool]]:
    """dataclass の stub の欄(名・既定値の有無)。"""
    return [
        (item.target.id, item.value is not None)
        for item in node.body
        if isinstance(item, ast.AnnAssign) and isinstance(item.target, ast.Name)
    ]


def _runtime_fields(fields: tuple[dataclasses.Field[object], ...]) -> list[tuple[str, bool]]:
    """実装の dataclass の欄(名・既定値の有無)。"""
    return [
        (f.name, f.default is not dataclasses.MISSING or f.default_factory is not dataclasses.MISSING) for f in fields
    ]


@pytest.mark.parametrize("module", STUBBED, ids=lambda m: m.__name__)
def test_every_declared_name_exists(module: types.ModuleType) -> None:
    assert [name for name in _declared_names(_stub_of(module)) if not hasattr(module, name)] == []


@pytest.mark.parametrize("module", STUBBED, ids=lambda m: m.__name__)
def test_dataclass_fields_match(module: types.ModuleType) -> None:
    for node in _stub_of(module).body:
        if isinstance(node, ast.ClassDef) and _is_dataclass_stub(node):
            runtime = getattr(module, node.name)
            assert dataclasses.is_dataclass(runtime), node.name
            assert _stub_fields(node) == _runtime_fields(dataclasses.fields(runtime)), node.name


@pytest.mark.parametrize("module", STUBBED, ids=lambda m: m.__name__)
def test_function_parameters_match(module: types.ModuleType) -> None:
    for node in _stub_of(module).body:
        if isinstance(node, ast.FunctionDef) and _public(node.name):
            stub = [a.arg for a in (*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs)]
            assert stub == list(inspect.signature(getattr(module, node.name)).parameters), node.name


@pytest.mark.parametrize("module", STUBBED, ids=lambda m: m.__name__)
def test_answer_unions_match(module: types.ModuleType) -> None:
    # 型の別名(`X: TypeAlias = A | B`)は実装の union と同じ型の並び。
    for node in _stub_of(module).body:
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) and node.value is not None:
            if not (isinstance(node.annotation, ast.Name) and node.annotation.id == "TypeAlias"):
                continue
            stub = [n.id for n in ast.walk(node.value) if isinstance(n, ast.Name)]
            runtime = [t.__name__ for t in typing.get_args(getattr(module, node.target.id))]
            assert sorted(stub) == sorted(runtime), node.target.id


def test_effects_are_effects_and_methods_exist() -> None:
    # effect の stub は EffectBase の部分型と宣言する — 実装も EffectBase の部分型。
    for node in _stub_of(effects).body:
        if isinstance(node, ast.ClassDef) and any(
            isinstance(b, ast.Subscript) and isinstance(b.value, ast.Name) and b.value.id == "EffectBase" for b in node.bases
        ):
            assert issubclass(getattr(effects, node.name), EffectBase), node.name
    # class の中の関数(TableDecl.writers-of など)は実装の class に在る。
    for module in STUBBED:
        for node in _stub_of(module).body:
            if isinstance(node, ast.ClassDef):
                methods = [m.name for m in node.body if isinstance(m, ast.FunctionDef) and _public(m.name)]
                assert [m for m in methods if not hasattr(getattr(module, node.name), m)] == [], node.name


def test_memory_store_attributes_match() -> None:
    # MemoryStore の stub の欄 = 実装の __init__ が置く欄(検と模擬の世界が直に読む置き場の data)。
    (node,) = [n for n in _stub_of(memory).body if isinstance(n, ast.ClassDef) and n.name == "MemoryStore"]
    stub = sorted(name for name, _ in _stub_fields(node))
    assert stub == sorted(vars(memory.MemoryStore(values.RecordsSchema())))
    assert memory.StoreOperation is faults.StoreOperation
