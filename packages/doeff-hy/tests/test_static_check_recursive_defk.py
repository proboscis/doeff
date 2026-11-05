"""Failure cases for the answer type of a defk that calls itself (the pair #2279 / #2291).

The type-check expansion of defk is an ordinary function where `<-` and `(! ...)` become `_doeff_perform` and the body has no yield, and the type of `do`
(static_types.pyi) makes the answer `Expand[T, Never]`. Previously the function return had no annotation, and pyright inferred the answer type from the body.
In a self-calling defk (recursion that builds a sequence by calling a judgment per line), the self-call in the middle of inference became Unknown,
and strict reportUnknown* reds appeared on the name bound to the recursive answer and on the function return (reds the writer cannot fix - measured by cc1-w30 in
#2244). If the expansion copies the T of the contract `(: % T)` to the return annotation:

- Under strict, there are 0 reds on the specimens of recursion that binds the recursive answer to a name and of recursion that carries an accumulator in an argument.
- The answer type is not lost: adding a number to the recursive answer (a tuple) in the body is a red for a type mismatch.
- Recursion whose contract type is only a descriptive string `(: % "...")` has no type to copy, so it is as before (the Unknown reds remain).
- The runtime expansion does not annotate the return. A defk that writes yield directly in the body is a generator, so the type-check expansion does not annotate it either.
"""

import ast
import contextlib
import importlib
import io
import json
import shutil
from pathlib import Path

import hy
import pytest

needs_pyright = pytest.mark.skipif(shutil.which("pyright") is None, reason="pyright is not available")

PROBE = """\
(require doeff-hy.macros [defk val <-])

(defk label-of [n]
  {:pre [(: n int)] :post [(: % str)]}
  (str n))

(defk labels-of [items]
  {:pre [(: items (get tuple #(int ...)))] :post [(: % (get tuple #(str ...)))]}
  (if (not items)
      #()
      (do
        (val head (! (label-of (get items 0))))
        (val rest (! (labels-of (cut items 1 None))))
        (+ #(head) rest))))

(defk gather [items acc]
  {:pre [(: items (get tuple #(int ...))) (: acc (get tuple #(str ...)))]
   :post [(: % (get tuple #(str ...)))]}
  (if (not items)
      acc
      (! (gather (cut items 1 None) (+ acc #((! (label-of (get items 0)))))))))

(defk total-width [items]
  {:pre [(: items (get tuple #(int ...)))] :post [(: % int)]}
  (<- labels (labels-of items))
  (<- gathered (gather items #()))
  (+ (len labels) (len gathered)))
"""

DESCRIBED = """\
(require doeff-hy.macros [defk val])

(defk depth [n]
  {:pre [(: n int)] :post [(: % "depth of the recursion")]}
  (if (<= n 0)
      0
      (do
        (val below (! (depth (- n 1))))
        (+ below 1))))
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


def _line_of(text: str, fragment: str) -> int:
    return next(index for index, line in enumerate(text.splitlines(), 1) if fragment in line)


@needs_pyright
def test_recursive_defk_has_no_strict_error(tmp_path: Path) -> None:
    assert _check(tmp_path, PROBE) == []


@needs_pyright
def test_recursive_answer_used_as_the_wrong_type_is_red(tmp_path: Path) -> None:
    # The recursive answer rest in the body reads as tuple[str, ...], so adding 1 is a type-mismatch red
    # - a check that the return annotation does not let the answer escape to Any (with Any there would be no red).
    text = PROBE.replace("(+ #(head) rest)", "(+ #(head) (+ rest 1))")
    errors = _check(tmp_path, text)
    assert ("reportOperatorIssue", _line_of(text, "(+ rest 1)")) in [
        (d["rule"], d["line"]) for d in errors
    ], errors


@needs_pyright
def test_recursion_with_a_described_answer_keeps_the_unknown(tmp_path: Path) -> None:
    # If the contract type is only a descriptive string there is no type to copy - the name bound to the recursive answer is an Unknown red as before.
    errors = _check(tmp_path, DESCRIBED)
    below = _line_of(DESCRIBED, "(val below")
    assert [d for d in errors if d["line"] == below and str(d["rule"]).startswith("reportUnknown")], errors


def _expand(source: str) -> str:
    # importing doeff_hy prepares the Hy importer and macros (an import for its side effects)
    importlib.import_module("doeff_hy")
    return ast.unparse(hy.compiler.hy_compile(hy.read_many(source), "__main__"))


YIELDING = """\
(require doeff-hy.macros [defk])

(defk raw-step [n]
  {:pre [(: n int)] :post [(: % int)]}
  (yield n)
  n)
"""


def test_only_the_static_expansion_of_a_yield_free_defk_annotates_the_return() -> None:
    from doeff_hy._static_view import static_view

    source = PROBE
    runtime = _expand(source)
    with static_view():
        static = _expand(source)
        yielding = _expand(YIELDING)
    # Type-check expansion: the T of the contract (: % T) becomes the return annotation (a string annotation - not evaluated at definition time)
    assert "def labels_of(items: 'tuple[int, ...]') -> 'tuple[str, ...]':" in static, static
    assert "def label_of(n: 'int') -> 'str':" in static, static
    # The runtime defk is a generator, so the return is not annotated
    assert "def labels_of(items: 'tuple[int, ...]'):" in runtime, runtime
    assert "-> 'tuple[str, ...]'" not in runtime
    # A defk with yield written directly in the body is a generator in the type-check expansion too - T is not written on the return
    assert "def raw_step(n: 'int'):" in yielding, yielding
