"""Failure cases for the annotations used by the type check on arguments whose contract type is written as `(of base arg …)`.

`of` is a macro, but `_type-source`, which maps a contract type to an annotation string, passed the expression to hy-compile without macros. So
`(: rows (of tuple Row ...))` compiled to the call `of(tuple, Row, ...)`, produced no annotation, and was dropped (the argument had no annotation =
a red under strict: "no annotation / unknown type"). `(| (of tuple str ...) None)` became the false annotation `of(tuple, str, ...) | None`.
If `(of …)` is first mapped to the equivalent `(get …)` and then compiled:

- `(of …)`, nested forms, and `(of …)` inside `|` yield the same annotation as when written with `get` (unit).
- Under strict, a defk with a `(of tuple str ...)` argument gets 0 reds.
- The annotation does not slip away: passing a tuple of numbers to that argument is a red for a type mismatch.
"""

import contextlib
import io
import json
import shutil
from pathlib import Path

import hy
import pytest

needs_pyright = pytest.mark.skipif(shutil.which("pyright") is None, reason="pyright is not installed")

PROBE = """\
(require doeff-hy.macros [defk <- val])

(defk joined [names]
  {:pre [(: names (of tuple str ...))] :post [(: % str)]}
  (.join "," names))

(defk maybe-joined [names]
  {:pre [(: names (| (of tuple str ...) None))] :post [(: % str)]}
  (if (is names None) "" (.join "," names)))

(defk caller []
  {:pre [] :post [(: % str)]}
  (<- got str (joined {ARG}))
  got)
"""

BASE = {"ARG": '#("a" "b")'}


def _render(change: dict[str, str]) -> str:
    text = PROBE
    for key, value in (BASE | change).items():
        text = text.replace("{" + key + "}", value)
    return text


def _check(root: Path, text: str) -> list[dict[str, object]]:
    from doeff_hy._static_check import main

    (root / "probe.hy").write_text(text, encoding="utf-8")
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        main(["--root", str(root), "--json", "--no-cache", "--strict", str(root / "probe.hy")])
    printed = out.getvalue()
    found: list[dict[str, object]] = json.loads(printed) if printed.strip() else []
    return [d for d in found if d["severity"] == "error"]


def _line_of(text: str, fragment: str) -> int:
    return next(index for index, line in enumerate(text.splitlines(), 1) if fragment in line)


def test_of_forms_project_to_the_same_annotation_as_get() -> None:
    from doeff_hy.macros import _type_source

    cases = {
        "(of tuple str ...)": "tuple[str, ...]",
        "(of dict str int)": "dict[str, int]",
        "(of list (of tuple str int))": "list[tuple[str, int]]",
        "(| (of tuple str ...) None)": "tuple[str, ...] | None",
        "(get tuple #(str ...))": "tuple[str, ...]",
    }
    assert {src: _type_source(hy.read(src)) for src in cases} == cases
    # An expression that still contains a call does not become an annotation (no annotation is better than a false one).
    assert _type_source(hy.read("(| int (type x))")) is None


@needs_pyright
def test_strict_has_no_error_for_of_contracts(tmp_path: Path) -> None:
    assert _check(tmp_path, _render({})) == []


@needs_pyright
def test_of_contract_still_rejects_a_wrong_argument(tmp_path: Path) -> None:
    text = _render({"ARG": "#(1 2)"})
    errors = _check(tmp_path, text)
    line = _line_of(text, "(joined #(1 2))")
    assert [(e["rule"], e["line"]) for e in errors] == [("reportArgumentType", line)], errors
