"""Test of the table of spellings of doeff names that the expansion of handle / defhandler reads (doeff_hy/_expansion_names.py).

The table is the single source of truth for the aliases of the names the expansion reads (`_doeff_<name>`) and for imports. That an expansion's names do not shadow an author's effect of the same name is checked by
tests/test_handle_writer_names.py; that no reader returns to writing bare names without going through the table is checked here.
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

# Names the expansion reads. The author may use them as their own effect names (Transfer for a bank transfer, Put / Get for KV, etc.), so reading them with the bare spelling would shadow them.
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
    """Make a different Symbol on every call so that the position-attaching macro (locate-synthesized) does not reuse a Symbol."""
    assert expansion_names.alias_symbol("Put") is not expansion_names.alias_symbol("Put")


# typing names that type-building definitions (the :check of defeffect, defwire, defrecord) read in the type body. There is no worry of sharing them with the author's names, but
# the type body and methods written in a class body look up names from the module level, so distinguish from the table the imports that the type-check projection moves to the module level
# (static_check._synthesized_import).
_TYPING_NAMES = ("ClassVar", "cast")


@pytest.mark.parametrize("name", (*_NAMES, *_TYPING_NAMES))
def test_every_expansion_name_is_importable_from_its_home(name: str) -> None:
    """From the table's location, the table's names can be imported (a mistake in the table makes the expansion that reads the name fail at run time with ImportError)."""
    assert hasattr(importlib.import_module(expansion_names.home(name)), name)


def test_home_refuses_a_name_outside_the_table() -> None:
    """The table is closed -- it does not decide that an unlisted name's location is doeff (an unknown name is not answered as an alias import; if an expansion
    starts reading a name not in the table, it fails at expansion time and requires adding it to the table)."""
    with pytest.raises(KeyError):
        expansion_names.home("do")


@pytest.mark.parametrize(
    ("module", "name", "asname", "expected"),
    [
        ("doeff", "Pass", "_doeff_Pass", True),
        ("doeff", "WithHandlerType", "_doeff_WithHandlerType", True),
        ("typing", "ClassVar", "_doeff_ClassVar", True),
        ("typing", "cast", "_doeff_cast", True),
        # The alias (a name not in the table) that the for/do docstring has the author write is the author's import
        ("doeff", "do", "_doeff_do", False),
        ("doeff_traverse", "Traverse", "_doeff_traverse_Traverse", False),
        ("doeff_vm", "Pass", "_doeff_Pass", False),
        # the VM node is read from doeff's root, never from doeff_vm
        ("doeff_vm", "WithHandler", "_doeff_WithHandler", False),
        ("doeff", "Pass", "Pass", False),
        ("doeff", "Pass", None, False),
    ],
)
def test_is_alias_import_answers_only_the_table(
    module: str, name: str, asname: str | None, expected: bool
) -> None:
    """`is_alias_import` answers as expansion synthesis only an import that binds a table name, from the table's location, with the table's alias."""
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
    """A Hy import form also binds the same alias from the same location as a Python import."""
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
# The sources that build the expansion do not write bare names without going through the table
# ---------------------------------------------------------------------------

_PACKAGE = Path(doeff_hy.__file__).parent


def _bare_symbols_in_hy(path: Path) -> frozenset[str]:
    """Expansion names written as a Symbol in Hy source (the hyphenated spelling like `typed-resume` is also mangled and counted. Inside an import form,
    names are bound by alias, so they are not counted)."""
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
    """Expansion names written as `Symbol("name")` in Python source."""
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
    """No source in doeff_hy writes a name that the expansion reads as a bare Symbol (it looks up the table's `alias-symbol`) -- a bare spelling shares one name with the author's
    effect of the same name."""
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
# Position -- the model the expansion builds does not pull in the position of the user's expression
# ---------------------------------------------------------------------------


def _positioned_models(form: object) -> list[object]:
    """Models within a form that carry a position (the `_start_line` that the reader attaches)."""
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
    """The model that `import_form` returns carries no position. A model the reader makes (such as `hy.read("doeff.do")`) carries a position of line 1 column 1,
    and the position-attaching macro (locate-synthesized) mistook it for the position of the user's expression and pulled the enclosing expression's range to line 1 -- the line of the type-check
    diagnostics (unused import, undefined name) became 1:1 instead of the handler's line (a regression of the fix that aliased the expansion's names,
    found by measurement in blind A of the design verification)."""
    assert _positioned_models(expansion_names.import_form(names)) == []
