"""Failure case: the type check must not count an effect name that appears only in the definition declaration :effects as an "unused import".

When a handler clause emits an effect through a subcontracted function (a defk in another module), the effect's type name appears only in the handler head's :effects declaration.
The expansion for type checking (static_check.without_bookkeeping) dropped the definition bookkeeping `setattr(name, '__doeff_effects__', …)` wholesale,
so a name appearing only in the declaration became an unread import and strict reported a reportUnusedImport red (agora's
controllers/messaging/protocol/intake_router.hy and intake.hy AppendEvent — the daily run of 2026-10-07 08:00). Removing the import makes the
:effects declaration fail to resolve the name at runtime, so the author cannot fix it.

- A handler sample that imports a name appearing only in the declaration has no reportUnusedImport red.
- The :effects statement of the bookkeeping is kept shrunk to the form `setattr(name, '__doeff_effects__', (A, B))` that does not call doeff_hy._declarations (other bookkeeping and
  the import of doeff_hy._declarations are dropped as before).
"""

import ast
import contextlib
import io
import json
import shutil
from pathlib import Path

import pytest
from doeff_hy._static_check import without_bookkeeping

needs_pyright = pytest.mark.skipif(shutil.which("pyright") is None, reason="pyright is missing")

EFFECTS = """\
(import dataclasses [dataclass])
(import doeff [EffectBase])

(defclass [(dataclass :frozen True)] Tick [(get EffectBase int)]
  #^ int step)

(defclass [(dataclass :frozen True)] Pair [(get EffectBase int)]
  #^ int left
  #^ int right)
"""

HELPERS = """\
(require doeff-hy.macros [defk <-])
(import probe_effects [Pair])

(defk paired [step]
  {:pre [(: step int)] :post [(: % int)] :tags {:context "probe" :role "protocol"}}
  "To emit Pair and return an answer (a subcontract called by the handler clause)."
  (<- n int (Pair step step))
  n)
"""

PROBE = """\
(require doeff-hy.macros [defhandler <-])
(import probe_effects [Tick Pair])
(import probe_helpers [paired])

(defhandler via-helper {:effects [Pair] :tags {:context "probe" :role "protocol"}}
  ;; Pair is emitted by the subcontract paired — in this module it appears only in the declaration :effects.
  (Tick [step]
    (<- n int (paired step))
    (resume n)))
"""


def _check(root: Path) -> list[dict[str, object]]:
    from doeff_hy._static_check import main

    (root / "probe_effects.hy").write_text(EFFECTS, encoding="utf-8")
    (root / "probe_helpers.hy").write_text(HELPERS, encoding="utf-8")
    (root / "probe.hy").write_text(PROBE, encoding="utf-8")
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        main(["--root", str(root), "--json", "--no-cache", "--strict", str(root / "probe.hy")])
    printed = out.getvalue()
    found: list[dict[str, object]] = json.loads(printed) if printed.strip() else []
    return [d for d in found if d["severity"] == "error"]


@needs_pyright
def test_a_name_only_in_the_effects_declaration_is_not_an_unused_import(tmp_path: Path) -> None:
    errors = _check(tmp_path)
    assert [e for e in errors if e["rule"] == "reportUnusedImport"] == [], errors


def test_the_effects_bookkeeping_keeps_its_names_without_the_declarations_module() -> None:
    tree = ast.parse(
        "from m import A, B\n"
        "import doeff_hy._declarations\n"
        "setattr(h, '__doeff_effects__', doeff_hy._declarations.effect_types('defhandler h', tuple([A, B])))\n"
        "setattr(h, '__doeff_tags__', doeff_hy._declarations.DefinitionTags(context='c', role='protocol'))\n"
        "setattr(h, '__doeff_needs__', None)\n"
    )
    projected = ast.unparse(without_bookkeeping(tree)).splitlines()
    assert projected == ["from m import A, B", "setattr(h, '__doeff_effects__', (A, B))"], projected


def test_an_undeclared_effects_bookkeeping_is_dropped() -> None:
    # The bookkeeping of a definition without :effects (None) is dropped as before.
    tree = ast.parse("setattr(h, '__doeff_effects__', None)\n")
    assert ast.unparse(without_bookkeeping(tree)) == ""
