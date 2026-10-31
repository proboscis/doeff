"""doeff-hy-check が、展開した Python が文法に合わない .hy を 1 本含む範囲でも、検査全体を落とさない失敗ケース。

Hy 1.3.1 の match は型の pattern の欄の名を mangle しないので、defk の外の ``(A :foo-bar y)`` は ``case A(foo-bar=y)`` の木に
なり、型検査の展開の ``ast.parse(ast.unparse(tree))`` が SyntaxError を出す。以前は project が HyLanguageError だけを 1 本の
file の失敗にし、SyntaxError は展開の子の process から抜けて repo 全体の検査を落とした(agora-controllers の日次の
hy-type-trend が 10-30 に 2 回 premise-unmet — agora-redesign card ki-6aa23021c532)。

- 直列でも子の process で並べても、その file を名指した診断(hy-expansion・match の行)になり、他の file の展開は揃う。
- doeff-hy-check の入口は例外で落ちず、その file の診断を出して終わる。
"""

import contextlib
import io
import json
from pathlib import Path

from doeff_hy._static_check import CompileFailure, Projection, main, project, project_closure

#: 型の pattern の欄の名に `-` を持つ match(defk の外 — 展開が `case A(foo-bar=y)` になる)。match は 5 行目。
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
    """根の file(main.hy)と、それが import する alpha・beta・gamma を置く(beta は展開が文法に合わない)。"""
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
    assert diagnostic.line == 5, diagnostic  # 文法に合わない一番奥の文 = match
    assert diagnostic.message.startswith("展開した Python が文法に合わない: "), diagnostic


def test_an_unparsable_dependency_does_not_stop_its_wave(tmp_path: Path) -> None:
    main_file = _tree(tmp_path)
    for limit in (1, 3):  # 親で順に・子の process で並べる
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
