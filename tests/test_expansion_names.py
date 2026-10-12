"""handle・defhandler の展開が読む doeff の名の綴りの表(doeff_hy/expansion_names.py)の検(card ki-16180480129e)。

表は展開が読む名の別名(`_doeff_<名>`)と import の唯一の定義元。展開の名が書き手の同じ名の effect を隠さない事そのものは
tests/test_handle_writer_names.py が、表を介さずに素の名を書く読み手が戻らない事はここが確かめる。
"""

import ast
import importlib
import sys
import types
from pathlib import Path

import doeff_core_effects.effects
import doeff_hy
import doeff_vm
import hy
import pytest
from doeff_hy import expansion_names
from hy.models import Expression, Sequence, Symbol

import doeff

# 展開が読む名。書き手が自分の effect の名にしうる(振込の Transfer・KV の Put / Get など)ので、素の綴りで読むと隠す。
_NAMES = ("Resume", "Transfer", "Pass", "WithHandler", "Get", "Put", "Some", "typed_resume", "typed_transfer")


def test_alias_is_the_doeff_prefixed_name() -> None:
    assert expansion_names.alias("Transfer") == "_doeff_Transfer"
    assert str(expansion_names.alias_symbol("Put")) == "_doeff_Put"


def test_alias_symbol_makes_a_new_symbol_each_call() -> None:
    """位置を付ける macro(locate-synthesized)が Symbol を使い回さないよう、呼ぶたびに別の Symbol を作る(agora-redesign #1004)。"""
    assert expansion_names.alias_symbol("Put") is not expansion_names.alias_symbol("Put")


@pytest.mark.parametrize("name", _NAMES)
def test_every_expansion_name_is_importable_from_its_home(name: str) -> None:
    """表の置き場から、表の名を import できる(表の誤りは、その名を読む展開が実行の時に ImportError で落ちる)。"""
    assert hasattr(importlib.import_module(expansion_names.home(name)), name)


def test_import_source_aliases_the_names_grouped_by_home() -> None:
    text = expansion_names.import_source(["Pass", "WithHandler", "Put", "Pass", "Some"])
    assert ast.dump(ast.parse(text)) == ast.dump(
        ast.parse(
            "from doeff import Pass as _doeff_Pass, Some as _doeff_Some\n"
            "from doeff_vm import WithHandler as _doeff_WithHandler\n"
            "from doeff_core_effects.effects import Put as _doeff_Put\n"
        )
    )


def test_import_form_aliases_the_names_grouped_by_home() -> None:
    """Hy の import の形も、Python の import と同じ別名を同じ置き場から束ねる。"""
    mod = types.ModuleType("expansion_names_probe")
    sys.modules[mod.__name__] = mod
    hy.eval(expansion_names.import_form(["Pass", "Put", "WithHandler"]), mod.__dict__, module=mod)
    aliased = {name: value for name, value in vars(mod).items() if name.startswith("_doeff_")}
    assert aliased == {
        "_doeff_Pass": doeff.Pass,
        "_doeff_Put": doeff_core_effects.effects.Put,
        "_doeff_WithHandler": doeff_vm.WithHandler,
    }


def test_import_form_without_names_is_empty() -> None:
    assert expansion_names.import_form([]) == hy.read("(do)")


# ---------------------------------------------------------------------------
# 展開を作る source は、表を介さずに素の名を書かない
# ---------------------------------------------------------------------------

_PACKAGE = Path(doeff_hy.__file__).parent


def _bare_symbols_in_hy(path: Path) -> frozenset[str]:
    """Hy の source に Symbol として書かれた展開の名(`typed-resume` のハイフンの綴りも mangle して数える。import の形の中は、
    名を別名で束ねる綴りなので数えない)。"""
    found: set[str] = set()

    def walk(form: object) -> None:
        match form:
            case Expression() if len(form) and form[0] in (Symbol("import"), Symbol("require")):
                return
            case Symbol() if hy.mangle(str(form)) in _NAMES:
                found.add(hy.mangle(str(form)))
            case Sequence():
                for child in form:
                    walk(child)

    for form in hy.read_many(path.read_text(encoding="utf-8"), filename=str(path)):
        walk(form)
    return frozenset(found)


def _bare_symbols_in_python(path: Path) -> frozenset[str]:
    """Python の source の `Symbol("名")` に書かれた展開の名。"""
    return frozenset(
        node.args[0].value
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8")))
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "Symbol"
        and len(node.args) == 1
        and isinstance(node.args[0], ast.Constant)
        and node.args[0].value in _NAMES
    )


def test_no_source_spells_an_expansion_name_bare() -> None:
    """doeff_hy のどの source も、展開が読む名を素の Symbol で書かない(表の `alias-symbol` を引く) — 素の綴りは書き手の同じ名の
    effect と 1 つの名を分け合う(card ki-16180480129e)。"""
    spelled = {
        path.name: names
        for path in sorted([*_PACKAGE.glob("*.hy"), *_PACKAGE.glob("*.py")])
        if path.name != "expansion_names.py"
        if (
            names := _bare_symbols_in_hy(path)
            if path.suffix == ".hy"
            else _bare_symbols_in_python(path)
        )
    }
    assert spelled == {}
