"""doeff_agents の package の根が遅延で配る名を、型の検査からも定義元の型で見せる事の検。

package の根は import を遅らせるため、名を ``__getattr__(name) -> Any`` で配る(``_LAZY_EXPORTS``)。型の検査は
``__getattr__`` の答えの型しか知らないので、``from doeff_agents import LaunchEffect`` の ``LaunchEffect`` は ``Any``
に見え、使い手の ``isinstance(x, LaunchEffect)`` の絞りや欄の型が全部 Unknown になる(agora-controllers の日次の
型の赤 — card ki-9ca6c2f97ea7 の ``LaunchEffect.agent_type``)。根は ``if TYPE_CHECKING:`` の節で、遅延の表の名を
全部、表と同じ定義元から import する(doeff-adr の ``__init__.py`` と同じ形)。この検は、節と表が 1 名ずつ同じ
定義元で揃う事を、根の file の構文木で確かめる(表に足して節に足し忘れた名は、型の検査で黙って ``Any`` に戻る)。
"""

from __future__ import annotations

import ast
import importlib.util
from pathlib import Path

import doeff_agents

ROOT = Path(doeff_agents.__file__)


def _defined_in_untyped_hy(name: str) -> bool:
    """遅延の名 name の実物の定義元が、型の宣言(同じ名の .pyi)を持たない Hy の module か — 型の検査はその名を解けないので、
    節に載せると根の file に解けない import の赤を足す(handlers/testing.hy の Mock*・Scenario*)。"""
    module = getattr(getattr(doeff_agents, name), "__module__", None)
    if not isinstance(module, str):
        return False  # 定数(str など)は表の定義元(.py)で定義される
    spec = importlib.util.find_spec(module)
    origin = None if spec is None else spec.origin
    return origin is not None and origin.endswith(".hy") and not Path(origin).with_suffix(".pyi").exists()


def _absolute(module: str) -> str:
    """表の相対の定義元(``.handlers``)と節の絶対の定義元(``doeff_agents._handlers`` — 相対の import は doeff-linter の DOEFF016)を
    同じ綴りで比べるため、根の package の下の絶対の名にする。"""
    return "doeff_agents" + module if module.startswith(".") else module


def _lazy_exports(tree: ast.Module) -> dict[str, str]:
    for node in tree.body:
        if (
            isinstance(node, ast.Assign)
            and len(node.targets) == 1
            and isinstance(node.targets[0], ast.Name)
            and node.targets[0].id == "_LAZY_EXPORTS"
        ):
            table = ast.literal_eval(node.value)
            assert isinstance(table, dict)
            return {str(name): _absolute(str(module)) for name, module in table.items()}
    raise AssertionError("doeff_agents/__init__.py に _LAZY_EXPORTS が無い")


def _type_checking_imports(tree: ast.Module) -> dict[str, str]:
    """``if TYPE_CHECKING:`` の節が ``from <定義元> import 名 as 名`` で import する名 → 定義元(絶対の名)。"""
    found: dict[str, str] = {}
    for node in tree.body:
        if not (isinstance(node, ast.If) and isinstance(node.test, ast.Name) and node.test.id == "TYPE_CHECKING"):
            continue
        for stmt in node.body:
            assert isinstance(stmt, ast.ImportFrom), ast.dump(stmt)
            module = _absolute("." * stmt.level + (stmt.module or ""))
            for alias in stmt.names:
                # ``as 名`` で同じ名を付ける形だけが再輸出(pyright の strict は別名の無い import を再輸出と読まない)。
                assert alias.asname == alias.name, (module, alias.name, alias.asname)
                found[alias.name] = module
    return found


def test_every_lazy_export_is_imported_for_type_checkers_from_its_own_module() -> None:
    tree = ast.parse(ROOT.read_text(encoding="utf-8"))
    untyped = {name for name in _lazy_exports(tree) if _defined_in_untyped_hy(name)}
    lazy = {name: module for name, module in _lazy_exports(tree).items() if name not in untyped}
    typed = _type_checking_imports(tree)
    shown_untyped = sorted(untyped & set(typed))
    assert shown_untyped == [], f"型の宣言の無い Hy の module の名を節が import している(型の検査が解けない): {shown_untyped}"
    missing = sorted(name for name in lazy if name not in typed)
    assert missing == [], f"型の検査の節に無い遅延の名 {len(missing)} 個: {missing[:10]}"
    moved = sorted((name, lazy[name], typed[name]) for name in lazy if typed[name] != lazy[name])
    assert moved == [], f"表と節で定義元が違う名: {moved[:10]}"
    extra = sorted(name for name in typed if name not in lazy)
    assert extra == [], f"表に無い名を節が import している: {extra}"
