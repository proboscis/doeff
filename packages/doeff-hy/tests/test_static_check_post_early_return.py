"""Failure cases of type-check errors for a defk / deff that has both a `:post` type contract `(: % T)` and a `(return x)` in the middle of the body (
parent #3167).

Since doeff 66d4c9428, a `(return x)` in the middle of a definition with `:post` is rewritten into the same exit as the last expression -- `_contract_result = x` ->
guard -> `_hy_let_% = _contract_result` -> the `:post` check -> `return _contract_result`. The type annotation `_contract_result: T` was attached
only to the assignment of the last expression, and when the body ended in a `while True` form the last assignment was placed where it is unreachable, so the annotation took effect nowhere.
Type holes in the value of a mid-body exit (such as `tuple[Unknown, ...]`) propagated to the guard argument, `_hy_let_%`, and the copy into return, and
errors the author could not fix from their code increased by 3 per return (appearing at the position where the `:post` check was synthesized = the `:post` line). If the expansion declares
`_contract_result: T` without a value at the head of the function (right after the `:pre` check), and makes the mid-body and last assignments un-annotated one-element tuple assignments `(_contract_result,) = (value,)`:

- In strict, there are 0 errors on the `:post` line and 0 errors whose message contains `_hy_let_*` (a body ending in while True, a form reaching the last expression, and a form with
  multiple mid-body returns). Errors whose message contains `_contract_result` remain only one per return line whose value has a type hole -- the author's value hole itself
  (the same count and the same line as "Return type ... is partially unknown" when writing `return tuple(rows)` in Python. pyright reports type holes in the assigned value
  even for an assignment to a declared variable), and they disappear if the author types the value.
- A definition whose last expression is an exhaustive match has 0 errors. With a plain assignment `(setv _contract_result value)`, Hy moves the match temporary name
  (including `temp = None` before the case) over to `_contract_result`, and that None was matched against the head declaration T, becoming an error
  the author could not fix (a user's measurement -- 18 in a module that measures speed). No such move happens for a tuple assignment target.
- Types do not escape: a definition with no type holes has 0 errors, and returning a type different from T in a mid-body return makes that return line an error.
- The type-check expansion and the run-time expansion have the same shape (both declare once at the head, and assignments are un-annotated tuple assignments).
- A definition with no type contract in `:post` stays as before (no declaration is emitted).

Outside this fix: when the value type is entirely Unknown (such as an element from iterating a list with no type arguments), pyright narrows the declared variable to that Unknown,
so the errors at the guard, `_hy_let_%`, and return remain (the last expression's exit has been the same shape since before 66d4c9428).
"""

import ast
import contextlib
import importlib
import io
import json
import re
import shutil
from collections import Counter
from pathlib import Path

import hy
import pytest

needs_pyright = pytest.mark.skipif(shutil.which("pyright") is None, reason="pyright is not installed")

# The body ends in while True and only the mid-body return is an exit (the last expression's assignment is placed where it is unreachable).
LOOP = """\
(require doeff-hy.macros [defk])

(defk drain [rows]
  {:pre [(: rows list)] :post [(: % (get tuple #(int ...)))]}
  (while True
    (when (not rows)
      (return (tuple rows)))
    (.pop rows)))
"""

# Has both a mid-body return and an exit reaching the last expression.
REACHES_LAST = """\
(require doeff-hy.macros [defk])

(defk pick [rows]
  {:pre [(: rows list)] :post [(: % (get tuple #(int ...)))]}
  (when (not rows)
    (return (tuple rows)))
  #(1 2))
"""

# Multiple mid-body returns (inside a loop, inside while True). deff goes through the same expansion. The value of every return is a tuple made from a list with no type arguments
# (it has a type hole).
MANY_RETURNS = """\
(require doeff-hy.macros [defk deff])

(defk split-rows [rows]
  {:pre [(: rows list)] :post [(: % (get tuple #(int ...)))]}
  (for [n (range 3)]
    (when (= (len rows) n)
      (return (tuple rows))))
  (while True
    (when (not rows)
      (return (tuple (reversed rows))))
    (.pop rows)))

(deff drain-names [names]
  {:pre [(: names list)] :post [(: % (get tuple #(str ...)))]}
  (while True
    (when (not names)
      (return (tuple names)))
    (when (= (len names) 1)
      (return (tuple (reversed names))))
    (.pop names)))
"""

# A definition with no type holes (type arguments written on the argument) -- 0 errors, and only a type mix-up in a mid-body return becomes an error.
TYPED = """\
(require doeff-hy.macros [defk])

(defk drain-typed [rows]
  {:pre [(: rows (get list int))] :post [(: % (get tuple #(int ...)))]}
  (while True
    (when (not rows)
      (return (tuple rows)))
    (.pop rows)))
"""

# A match whose last expression is exhaustive (Hy puts None into a temporary name before the case).
MATCH_LAST = """\
(require doeff-hy.macros [defk])
(import dataclasses [dataclass])

(defclass [(dataclass :frozen True)] InMemory [])
(defclass [(dataclass :frozen True)] OverHttp [])

(defk place-label [place]
  {:pre [(: place (| InMemory OverHttp))] :post [(: % str)]}
  (match place
    (InMemory) "memory"
    (OverHttp) "http"))
"""

# There is no type to carry to :post (the type is only a description string -- defk / deff require (: % …) in :post).
UNTYPED_POST = """\
(require doeff-hy.macros [defk deff])

(defk nonempty [rows]
  {:pre [(: rows (get list int))] :post [(: % "non-empty tuple") (> (len %) 0)]}
  (when (not rows)
    (return (tuple [0])))
  (tuple rows))

(deff nonempty-pure [rows]
  {:pre [(: rows (get list int))] :post [(: % "non-empty tuple") (> (len %) 0)]}
  (when (not rows)
    (return (tuple [0])))
  (tuple rows))
"""


def _check(root: Path, text: str) -> list[dict[str, object]]:
    from doeff_hy._static_check import main

    (root / "probe.hy").write_text(text, encoding="utf-8")
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        main(["--root", str(root), "--json", "--no-cache", "--strict", str(root / "probe.hy")])
    printed = out.getvalue()
    found: list[dict[str, object]] = json.loads(printed) if printed.strip() else []
    return [d for d in found if d["severity"] == "error"]


def _lines_with(text: str, fragment: str) -> list[int]:
    return [index for index, line in enumerate(text.splitlines(), 1) if fragment in line]


def _line_of(text: str, fragment: str) -> int:
    return _lines_with(text, fragment)[0]


@needs_pyright
@pytest.mark.parametrize("probe", [LOOP, REACHES_LAST, MANY_RETURNS], ids=["while-true", "reaches-last", "many-returns"])
def test_early_returns_add_no_error_of_the_expansion(tmp_path: Path, probe: str) -> None:
    errors = _check(tmp_path, probe)
    # The test definition reached type checking (the author's value holes -- the argument of a list with no type arguments -- remain as errors on the author's lines).
    assert [d for d in errors if d["rule"] == "reportMissingTypeArgument"], errors
    # No errors at the positions where the expansion synthesized the :post check (the guard argument, `_hy_let_%`, the copy into return).
    post_lines = _lines_with(probe, ":post")
    assert [d for d in errors if d["line"] in post_lines] == [], errors
    assert [d for d in errors if "_hy_let_" in str(d["message"])] == [], errors
    # Errors whose message contains `_contract_result` are exactly one per return line -- only the type hole in the value (the author's line, which the author can fix).
    naming_result = Counter(d["line"] for d in errors if "_contract_result" in str(d["message"]))
    assert naming_result == Counter(_lines_with(probe, "(return ")), errors


@needs_pyright
def test_a_typed_definition_with_early_returns_is_clean(tmp_path: Path) -> None:
    assert _check(tmp_path, TYPED) == []


@needs_pyright
def test_a_match_as_the_last_form_is_clean(tmp_path: Path) -> None:
    # With a plain assignment Hy moves the match temporary name (the None before the case) over to `_contract_result`,
    # and "Type "None" is not assignable to declared type "str"" appears on the match line.
    assert _check(tmp_path, MATCH_LAST) == []


@needs_pyright
def test_an_early_return_of_the_wrong_type_is_red_at_that_return(tmp_path: Path) -> None:
    # That the head declaration does not let the type of a mid-body exit escape (if it escaped to Any there would be no error).
    text = TYPED.replace("(return (tuple rows))", "(return [1])")
    errors = _check(tmp_path, text)
    assert _line_of(text, "(return [1])") in [d["line"] for d in errors], errors


def _expand(source: str) -> str:
    # importing doeff_hy sets up the Hy importer and macros (import for side effects)
    importlib.import_module("doeff_hy")
    return ast.unparse(hy.compiler.hy_compile(hy.read_many(source), "__main__"))


def _both_expansions(source: str) -> dict[str, str]:
    from doeff_hy._static_view import static_view

    runtime = _expand(source)
    with static_view():
        static = _expand(source)
    return {"runtime": runtime, "static": static}


@pytest.mark.parametrize("view", ["runtime", "static"])
def test_both_expansions_declare_the_result_once_at_the_head(view: str) -> None:
    text = _both_expansions(LOOP + REACHES_LAST.replace("(require doeff-hy.macros [defk])\n", ""))[view]
    for name in ("drain", "pick"):
        body = text.split(f"def {name}(", 1)[1].split("\ndef ", 1)[0]
        lines = [line.strip() for line in body.splitlines()]
        # Exactly one declaration without a value. No annotated assignment or plain assignment (exits are tuple assignments).
        assert lines.count("_contract_result: 'tuple[int, ...]'") == 1, body
        assert not re.search(r"_contract_result: '[^']*' =", body), body
        assert not re.search(r"^\s*_contract_result = ", body, re.MULTILINE), body
        # The declaration comes before the first assignment (a mid-body exit) -- at the function head (right after the :pre check, before the body).
        declared = lines.index("_contract_result: 'tuple[int, ...]'")
        first_assign = next(i for i, line in enumerate(lines) if line.startswith("_contract_result, = ("))
        first_body = next(i for i, line in enumerate(lines) if line.startswith(("while ", "if ")))
        assert declared < first_body < first_assign, body
        if view == "runtime":
            # The run-time expansion emits the :pre isinstance -- the declaration comes after it (the type-check expansion does not emit an argument isinstance).
            pre_check = next(i for i, line in enumerate(lines) if line.startswith("assert isinstance(rows, list)"))
            assert pre_check < declared, body


@pytest.mark.parametrize("view", ["runtime", "static"])
def test_a_post_without_a_type_contract_declares_nothing(view: str) -> None:
    text = _both_expansions(UNTYPED_POST)[view]
    assert "_contract_result:" not in text, text
    # The mid-body exits and the last expression's assignment (2 definitions x 2) are un-annotated tuple assignments.
    assert text.count("_contract_result, = (") == 4, text
