"""handle・defhandler の展開が読む doeff の名の綴りの表(doeff_hy/_expansion_names.py)の検(card ki-16180480129e)。

表は展開が読む名の別名(`_doeff_<名>`)と import の唯一の定義元。展開の名が書き手の同じ名の effect を隠さない事そのものは
tests/test_handle_writer_names.py が、表を介さずに素の名を書く読み手が戻らない事はここが確かめる。
"""

import ast
import importlib
import sys
import types
from pathlib import Path

import doeff_core_effects._effects
import doeff_hy
import hy
import pytest
from doeff_hy import _expansion_names as expansion_names
from hy.models import Expression, Sequence, Symbol

import doeff

# 展開が読む名。書き手が自分の effect の名にしうる(振込の Transfer・KV の Put / Get など)ので、素の綴りで読むと隠す。
_NAMES = (
    "Resume",
    "Transfer",
    "Pass",
    "WithHandlerType",
    "Get",
    "Put",
    "Some",
    "typed_resume",
    "typed_transfer",
)


def test_alias_is_the_doeff_prefixed_name() -> None:
    assert expansion_names.alias("Transfer") == "_doeff_Transfer"
    assert str(expansion_names.alias_symbol("Put")) == "_doeff_Put"


def test_alias_symbol_makes_a_new_symbol_each_call() -> None:
    """位置を付ける macro(locate-synthesized)が Symbol を使い回さないよう、呼ぶたびに別の Symbol を作る(agora-redesign #1004)。"""
    assert expansion_names.alias_symbol("Put") is not expansion_names.alias_symbol("Put")


# 型を建てる定義(defeffect・defwire・defrecord の :check)が型の本体で読む typing の名。書き手の名と分け合う心配は無いが、
# class の本体に書いた型の本体と method は名を module の段から引くので、型検査の投影が module の段へ移す import を
# 表から見分ける(static_check._synthesized_import)。
_TYPING_NAMES = ("ClassVar", "cast")


@pytest.mark.parametrize("name", (*_NAMES, *_TYPING_NAMES))
def test_every_expansion_name_is_importable_from_its_home(name: str) -> None:
    """表の置き場から、表の名を import できる(表の誤りは、その名を読む展開が実行の時に ImportError で落ちる)。"""
    assert hasattr(importlib.import_module(expansion_names.home(name)), name)


def test_home_refuses_a_name_outside_the_table() -> None:
    """表は閉じている — 表に無い名の置き場を doeff と決めない(知らない名を別名の import と答えない・展開が表に無い名を
    読み始めたら、展開の時に落ちて表へ足す事を求める)。"""
    with pytest.raises(KeyError):
        expansion_names.home("do")


@pytest.mark.parametrize(
    ("module", "name", "asname", "expected"),
    [
        ("doeff", "Pass", "_doeff_Pass", True),
        ("doeff", "WithHandlerType", "_doeff_WithHandlerType", True),
        ("typing", "ClassVar", "_doeff_ClassVar", True),
        ("typing", "cast", "_doeff_cast", True),
        # for/do の docstring が書き手に書かせる別名(表に無い名)は、書き手の import
        ("doeff", "do", "_doeff_do", False),
        ("doeff_traverse", "Traverse", "_doeff_traverse_Traverse", False),
        ("doeff_vm", "Pass", "_doeff_Pass", False),
        # the VM node is read from doeff's root, never from doeff_vm (agora-redesign ki-ff728b00369a)
        ("doeff_vm", "WithHandler", "_doeff_WithHandler", False),
        ("doeff", "Pass", "Pass", False),
        ("doeff", "Pass", None, False),
    ],
)
def test_is_alias_import_answers_only_the_table(
    module: str, name: str, asname: str | None, expected: bool
) -> None:
    """`is_alias_import` は、表の名を表の置き場から表の別名で束ねる import だけを、展開の合成と答える。"""
    assert expansion_names.is_alias_import(module, name, asname) is expected


def test_import_source_aliases_the_names_grouped_by_home() -> None:
    text = expansion_names.import_source(["Pass", "WithHandlerType", "Put", "Pass", "Some"])
    assert ast.dump(ast.parse(text)) == ast.dump(
        ast.parse(
            "from doeff import Pass as _doeff_Pass, WithHandlerType as _doeff_WithHandlerType,"
            " Some as _doeff_Some\n"
            "from doeff_core_effects._effects import Put as _doeff_Put\n"
        )
    )


def test_import_form_aliases_the_names_grouped_by_home() -> None:
    """Hy の import の形も、Python の import と同じ別名を同じ置き場から束ねる。"""
    mod = types.ModuleType("expansion_names_probe")
    sys.modules[mod.__name__] = mod
    hy.eval(expansion_names.import_form(["Pass", "Put", "WithHandlerType"]), mod.__dict__, module=mod)
    aliased = {name: value for name, value in vars(mod).items() if name.startswith("_doeff_")}
    assert aliased == {
        "_doeff_Pass": doeff.Pass,
        "_doeff_Put": doeff_core_effects._effects.Put,
        "_doeff_WithHandlerType": doeff.WithHandlerType,
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
        if path.name != "_expansion_names.py"
        if (
            names := _bare_symbols_in_hy(path)
            if path.suffix == ".hy"
            else _bare_symbols_in_python(path)
        )
    }
    assert spelled == {}


# ---------------------------------------------------------------------------
# 位置 — 展開が作る model は、利用者の式の位置を引き寄せない
# ---------------------------------------------------------------------------


def _positioned_models(form: object) -> list[object]:
    """form の中の、位置(reader が付ける `_start_line`)を持つ model。"""
    found: list[object] = [form] if hasattr(form, "_start_line") else []
    if isinstance(form, Sequence):
        for child in form:
            found.extend(_positioned_models(child))
    return found


@pytest.mark.parametrize(
    "names",
    [["Pass"], ["WithHandlerType"], ["Put", "Get"], ["Pass", "Put", "WithHandlerType", "Some"]],
    ids=["doeff", "doeff-node", "dotted-module", "all-homes"],
)
def test_import_form_builds_no_positioned_model(names: list[str]) -> None:
    """`import_form` が返す model は位置を持たない。reader が作る model(`hy.read("doeff.do")` など)は 1 行目 1 列目の位置を
    持ち、位置を付ける macro(locate-synthesized)が利用者の式の位置と取り違えて、囲む式の範囲を 1 行目へ引く — 型検査の
    診断(未使用の import・未定義の名)の行が、handler の行でなく 1:1 になった(設計検証 lt-2SJXY98Q643WGJK2ASWJ3N8PWH の盲検 A の
    実測で見つけた、card ki-16180480129e の直しの退行)。"""
    assert _positioned_models(expansion_names.import_form(names)) == []
