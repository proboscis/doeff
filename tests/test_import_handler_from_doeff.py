"""``from doeff import <handler>`` names the module the handler is in.

Before: ``from doeff import state`` failed with ``ImportError: cannot import name 'state'
from 'doeff' (.../doeff/__init__.py)`` — the message names neither the module that has
``state`` nor the import to write (first-errors report 2026-10-17, table row 1,
card doeff-publish 8).

The import statement gets an ImportError, while ``hasattr`` and ``getattr`` with a
default still see the name as absent: Python swallows an AttributeError from a module
``__getattr__`` in both places, so the kind of error follows the caller.
"""

from __future__ import annotations

import re

import hy
import pytest

import doeff

HANDLER_HOMES = [
    ("state", "doeff_core_effects.handlers"),
    ("reader", "doeff_core_effects.handlers"),
    ("writer", "doeff_core_effects.handlers"),
    ("writer_log", "doeff_core_effects.handlers"),
    ("lazy_ask", "doeff_core_effects.handlers"),
    ("env_var_ask", "doeff_core_effects.handlers"),
    ("try_handler", "doeff_core_effects.handlers"),
    ("await_handler", "doeff_core_effects.handlers"),
    ("listen_handler", "doeff_core_effects.handlers"),
    ("local_handler", "doeff_core_effects.handlers"),
    ("slog_handler", "doeff_core_effects.handlers"),
    ("slog_discard_handler", "doeff_core_effects.handlers"),
    ("scheduled", "doeff_core_effects.scheduler"),
]


def _import_from_doeff(name: str) -> None:
    exec(f"from doeff import {name}", {})


@pytest.mark.parametrize(("name", "home"), HANDLER_HOMES)
def test_from_doeff_import_a_handler_names_its_module(name: str, home: str) -> None:
    with pytest.raises(ImportError) as info:
        _import_from_doeff(name)
    assert f"from {home} import {name}" in str(info.value)


@pytest.mark.parametrize(("name", "home"), HANDLER_HOMES)
def test_the_import_line_in_the_message_runs(name: str, home: str) -> None:
    with pytest.raises(ImportError) as info:
        _import_from_doeff(name)
    line = re.search(rf"from {re.escape(home)} import {name}\b", str(info.value))
    assert line is not None
    namespace: dict[str, object] = {}
    exec(line.group(0), namespace)
    assert name in namespace


def test_an_import_statement_in_a_function_body_gets_the_hint() -> None:
    def body() -> object:
        from doeff import state

        return state

    with pytest.raises(ImportError, match=r"from doeff_core_effects\.handlers import state"):
        body()


def test_an_import_of_several_names_gets_the_hint_for_the_missing_one() -> None:
    with pytest.raises(ImportError, match=r"from doeff_core_effects\.handlers import reader"):
        exec("from doeff import do, run, reader", {})


def test_the_hy_import_form_gets_the_hint() -> None:
    with pytest.raises(ImportError, match=r"from doeff_core_effects\.handlers import state"):
        hy.eval(hy.read("(import doeff [do run state])"))


def test_the_import_error_is_not_replaced_by_cannot_import_name() -> None:
    with pytest.raises(ImportError) as info:
        _import_from_doeff("state")
    assert "__init__.py" not in str(info.value)
    assert info.value.name == "doeff"


def test_hasattr_still_sees_a_handler_name_as_absent() -> None:
    assert not hasattr(doeff, "state")
    assert getattr(doeff, "scheduled", None) is None


def test_attribute_access_names_the_module_in_an_attribute_error() -> None:
    with pytest.raises(AttributeError, match=r"from doeff_core_effects\.handlers import state"):
        _ = doeff.state


def test_an_unknown_name_keeps_the_plain_import_error() -> None:
    with pytest.raises(
        ImportError, match=r"cannot import name 'no_such_name' from 'doeff'"
    ) as info:
        _import_from_doeff("no_such_name")
    assert "doeff_core_effects" not in str(info.value)


def test_a_name_doeff_has_still_imports() -> None:
    namespace: dict[str, object] = {}
    exec("from doeff import do, run, Get", namespace)
    assert namespace["run"] is doeff.run
