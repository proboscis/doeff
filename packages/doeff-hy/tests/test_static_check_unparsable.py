"""Failure cases: doeff-hy-check does not fail the whole check even when the range contains one .hy whose expanded Python does not parse.

The Hy 1.3.1 match does not mangle field names in a type pattern, so ``(A :foo-bar y)`` outside defk becomes a ``case A(foo-bar=y)`` tree,
and ``ast.parse(ast.unparse(tree))`` in the type-checking expansion raises SyntaxError. Previously the project made only HyLanguageError a failure of a single
file, and SyntaxError escaped from the expansion child process and brought down the check of the whole repo (agora-controllers' daily
hy-type-trend hit premise-unmet twice on 10-30).

- Serial or parallel in child processes, it becomes a diagnostic naming that file (hy-expansion, the match line), and other files' expansions are complete.
- The doeff-hy-check entry does not die on an exception; it emits that file's diagnostic and finishes.
"""

import contextlib
import io
import json
from pathlib import Path

from doeff_hy._static_check import CompileFailure, Projection, main, project, project_closure

#: A match with `-` in the field name of a type pattern (outside defk -- the expansion becomes `case A(foo-bar=y)`). The match is on line 5.
UNPARSABLE = """\
(defclass A []
  (setv foo_bar 1))

(defn pick [x]
  (match x
    (A :foo-bar y) y
    _ None))
"""

LEAF = "(setv {name} {value})\n"

ROOT = """\
(import alpha beta gamma)
(setv total (+ alpha.alpha gamma.gamma))
"""


def _tree(root: Path) -> Path:
    """Place the root file (main.hy) and the alpha, beta and gamma it imports (beta's expansion does not parse)."""
    (root / "alpha.hy").write_text(LEAF.format(name="alpha", value=1), encoding="utf-8")
    (root / "beta.hy").write_text(UNPARSABLE, encoding="utf-8")
    (root / "gamma.hy").write_text(LEAF.format(name="gamma", value=3), encoding="utf-8")
    main_file = root / "main.hy"
    main_file.write_text(ROOT, encoding="utf-8")
    return main_file


def test_an_unparsable_expansion_is_a_failure_naming_the_file_and_line(tmp_path: Path) -> None:
    source = tmp_path / "beta.hy"
    source.write_text(UNPARSABLE, encoding="utf-8")
    result = project(tmp_path, [tmp_path], source)
    assert isinstance(result, CompileFailure), result
    diagnostic = result.diagnostic
    assert (diagnostic.path, diagnostic.rule, diagnostic.severity) == (
        "beta.hy",
        "hy-expansion",
        "error",
    ), diagnostic
    assert diagnostic.line == 5, diagnostic  # the innermost statement that does not parse = match
    assert diagnostic.message.startswith("the expanded Python is not valid syntax: "), diagnostic


def test_an_unparsable_dependency_does_not_stop_its_wave(tmp_path: Path) -> None:
    main_file = _tree(tmp_path)
    for limit in (1, 3):  # serially in the parent, and in parallel in child processes
        closure = project_closure(tmp_path, [tmp_path], [main_file], None, limit=limit)
        assert sorted(p.name for p in closure.projections) == ["alpha.hy", "gamma.hy", "main.hy"], (
            limit,
            closure,
        )
        assert [(f.source.name, f.diagnostic.rule) for f in closure.failures] == [
            ("beta.hy", "hy-expansion")
        ], limit
        assert all(isinstance(p, Projection) for p in closure.projections.values())


def test_the_check_reports_the_unparsable_file_instead_of_crashing(tmp_path: Path) -> None:
    _tree(tmp_path)
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        code = main(
            [
                "--root",
                str(tmp_path),
                "--no-cache",
                "--json",
                str(tmp_path / "alpha.hy"),
                str(tmp_path / "beta.hy"),
            ]
        )
    errors = [
        (d["path"], d["line"], d["rule"])
        for d in json.loads(out.getvalue())
        if d["severity"] == "error"
    ]
    assert errors == [("beta.hy", 5, "hy-expansion")], errors
    assert code == 1
