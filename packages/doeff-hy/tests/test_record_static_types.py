"""defrecord の :check の展開と doeff_hy.record の型(record.pyi)の失敗ケース(agora-redesign #2252)。

:check つきの defrecord は `(import doeff_hy.declarations doeff_hy.record)` を出し、`doeff_hy.record.DoExpr`・
`doeff_hy.record.EffectBase`・`doeff_hy.record.require_check` を名指す。前は使う側の defrecord の行に strict の赤が 5 件出ていた:

- record.hy は Hy の module で型の宣言が無く、名指す 4 つの名(record・DoExpr・EffectBase・require_check)が Unknown。
  → record.pyi で宣言する。
- 型検査の展開は記帳(setattr の `__doeff_tags__`)を外すので declarations は読まれなくなるが、record と同じ 1 つの import の文に
  並ぶため文ごと残り、reportUnusedImport になっていた。→ 記帳の module の import は名を 1 つずつ外す(static_check)。
"""

import ast
import contextlib
import inspect
import io
import json
import shutil
from dataclasses import dataclass
from pathlib import Path

import pytest

import doeff
import doeff_hy  # noqa: F401  # Hy の import hook を有効にする
from doeff_hy import record
from doeff_hy.static_check import without_bookkeeping

needs_pyright = pytest.mark.skipif(shutil.which("pyright") is None, reason="pyright が無い")

MODULE = """\
(require doeff-hy.record [defrecord])
(import dataclasses [dataclass])

(defrecord Answer
  "検体の答え。"
  {:tags {:context "probe" :role "type"}
   :check [(isinstance value key)]}
  (#^ type key)
  (#^ object value))

(defrecord Span
  "検体の範囲(:tags の無い :check)。"
  {:check [(<= start end)]}
  (#^ int start)
  (#^ int end))
"""

#: 前に defrecord の行に出ていた赤の文言(どれも書き手に直せない)。
FORMER_ERRORS: tuple[str, ...] = (
    '"doeff_hy.declarations" is not accessed',
    'Type of "record" is unknown',
    'Type of "DoExpr" is unknown',
    'Type of "EffectBase" is unknown',
    'Type of "require_check" is unknown',
)


@dataclass(frozen=True)
class Run:
    """doeff-hy-check を 1 回走らせた結果(JSON の診断)。"""

    diagnostics: tuple[dict[str, object], ...]

    def errors(self) -> list[tuple[str, int, str]]:
        return [
            (str(d["rule"]), int(str(d["line"])), str(d["message"]))
            for d in self.diagnostics
            if d["severity"] == "error"
        ]


def _check(root: Path) -> Run:
    from doeff_hy.static_check import main

    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        main(["--root", str(root), "--json", "--strict", "--no-cache", str(root / "probe.hy")])
    text = out.getvalue()
    return Run(tuple(json.loads(text)) if text.strip() else ())


@needs_pyright
def test_a_checked_defrecord_has_no_unreadable_red(tmp_path: Path) -> None:
    (tmp_path / "probe.hy").write_text(MODULE, encoding="utf-8")
    errors = _check(tmp_path).errors()
    assert not [e for e in errors if any(m in e[2] for m in FORMER_ERRORS)], errors


def test_an_unread_bookkeeping_name_leaves_a_shared_import_alone() -> None:
    # 1 つの文に並んだ記帳の module の名のうち、読まれない declarations だけを外し、読まれる record は残す。
    tree = ast.parse(
        "import doeff_hy.declarations, doeff_hy.record\n"
        "doeff_hy.record.require_check('A', ('x',), '(f x)', True, {'x': 1})\n"
        "setattr(A, '__doeff_tags__', doeff_hy.declarations.DefinitionTags(context='c', role='type'))\n"
    )
    projected = ast.unparse(without_bookkeeping(tree))
    assert projected.splitlines()[0] == "import doeff_hy.record", projected


def test_a_bookkeeping_import_with_no_reader_is_dropped() -> None:
    tree = ast.parse(
        "import doeff_hy.declarations, doeff_hy.record\n"
        "setattr(A, '__doeff_tags__', doeff_hy.declarations.DefinitionTags(context='c', role='type'))\n"
    )
    assert ast.unparse(without_bookkeeping(tree)) == ""


def test_the_stub_matches_record_hy() -> None:
    # record.pyi が宣言する名は record.hy に在り、require_check の引数の名と順は実装と同じ(宣言だけが先へ行かない)。
    stub = ast.parse(Path(record.__file__).with_suffix(".pyi").read_text(encoding="utf-8"))
    declared = [
        alias.asname or alias.name
        for node in stub.body
        if isinstance(node, ast.ImportFrom)
        for alias in node.names
    ] + [node.name for node in stub.body if isinstance(node, ast.FunctionDef)]
    assert [name for name in declared if not hasattr(record, name)] == []
    (function,) = [node for node in stub.body if isinstance(node, ast.FunctionDef)]
    assert [a.arg for a in function.args.args] == list(inspect.signature(record.require_check).parameters)
    assert record.DoExpr is doeff.DoExpr
    assert record.EffectBase is doeff.EffectBase
