"""Failure case: a contract `(: x T)` can use a type alias that contains a generic type with element types (#1790).

The :pre type contract of defk is checked with isinstance at run time (a generic type with element types is mapped to its outer type -- #1790), and the type-check expansion also emitted the same
isinstance. A type alias (a name) is passed to isinstance as is, so even if an alias with element types (e.g. `Body = Mapping[str, Body] | …`) was written
only at type-check time (the same shape as doeff_hy/_json_value.py), type checking raised the error "isinstance second argument is not a type", and the only option was to leave the alias as a plain union
(`Mapping | tuple | …`) (leaving a "partially unknown" error in the user's strict).
-> The type-check expansion does not emit the isinstance for the :pre type contract (the annotation written from the contract carries the argument type). The run-time check is unchanged.
The first test checks there is no type-check error; the second checks that at run time the contract still rejects the wrong value as before.
"""

import contextlib
import importlib
import io
import json
import shutil
import sys
from pathlib import Path

import doeff_hy  # noqa: F401  # enable the Hy import hook
import pytest

from doeff import run

needs_pyright = pytest.mark.skipif(shutil.which("pyright") is None, reason="pyright is not installed")

MODULE = """\
(require doeff-hy.macros [defk val])
(import collections.abc [Mapping])
(import typing [TYPE_CHECKING TypeAlias])

;; At type-check time a recursive alias with element types; at run time a union that can be passed to isinstance (the same shape as doeff_hy/_json_value.py).
(if TYPE_CHECKING
    (setv #^ TypeAlias Body "Mapping[str, Body] | tuple[Body, ...] | str | int | None")
    (setv Body (| Mapping tuple str int None)))

(defk text-of [body key]
  {:pre [(: body Body) (: key str)] :post [(: % (| str None))] :tags {:context "probe" :role "judgment"}}
  "To read the value when the content is an object and the field key is a string (readable by the alias element type)."
  (val value (if (isinstance body Mapping) (.get body key) None))
  (if (isinstance value str) value None))
"""


def _errors(root: Path) -> list[tuple[str, int, str]]:
    from doeff_hy._static_check import main

    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        main(
            ["--root", str(root), "--json", "--strict", "--no-cache", str(root / "probe_alias.hy")]
        )
    text = out.getvalue()
    diagnostics = json.loads(text) if text.strip() else []
    return [
        (str(d["rule"]), int(str(d["line"])), str(d["message"]))
        for d in diagnostics
        if d["severity"] == "error"
    ]


@needs_pyright
def test_a_type_alias_with_element_types_can_be_a_pre_contract(tmp_path: Path) -> None:
    (tmp_path / "probe_alias.hy").write_text(MODULE, encoding="utf-8")
    errors = _errors(tmp_path)
    assert not [e for e in errors if e[0] == "hy-compile"], errors
    assert not [e for e in errors if "isinstance" in e[2]], errors
    assert not [e for e in errors if "unknown" in e[2].lower()], errors


def test_the_pre_contract_still_refuses_a_wrong_value_at_run_time(tmp_path: Path) -> None:
    (tmp_path / "probe_alias_run.hy").write_text(MODULE, encoding="utf-8")
    sys.path.insert(0, str(tmp_path))
    try:
        probe = importlib.import_module("probe_alias_run")
    finally:
        sys.path.remove(str(tmp_path))
    assert run(probe.text_of({"a": "x"}, "a")) == "x"
    assert run(probe.text_of((1, 2), "a")) is None
    with pytest.raises(AssertionError, match="pre-condition type error"):
        run(probe.text_of(object(), "a"))
