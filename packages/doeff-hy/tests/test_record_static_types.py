"""defrecord の :check の展開と doeff_hy.record の型(record.pyi)の失敗ケース(agora-redesign #2252)。

:check つきの defrecord は `(import doeff_hy.declarations doeff_hy.record doeff_hy._record_check)` を出し、`doeff_hy.record.DoExpr`・
`doeff_hy.record.EffectBase`・`doeff_hy._record_check.require_check` を名指す(require_check は前は `doeff_hy.record` に在った —
macro の module は macro だけを見せる・ADR-DOE-API-001 R14・card ki-a28c20b93638)。前は使う側の defrecord の行に strict の赤が 5 件出ていた:

- record.hy は Hy の module で型の宣言が無く、名指す 4 つの名(record・DoExpr・EffectBase・require_check)が Unknown。
  → record.pyi で宣言する。
- 型検査の展開は記帳(setattr の `__doeff_tags__`)を外すので declarations は読まれなくなるが、record と同じ 1 つの import の文に
  並ぶため文ごと残り、reportUnusedImport になっていた。→ 記帳の module の import は名を 1 つずつ外す(static_check)。
"""

import ast
import contextlib
import io
import json
import shutil
from dataclasses import dataclass
from pathlib import Path

import hy
import pytest

import doeff
import doeff_hy  # noqa: F401  # Hy の import hook を有効にする
from doeff_hy import record
from doeff_hy._static_check import without_bookkeeping

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
    from doeff_hy._static_check import main

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


def _expand(source: str) -> str:
    return ast.unparse(hy.compiler.hy_compile(hy.read_many(source), "__main__"))


def test_only_the_runtime_expansion_refuses_a_program_verdict() -> None:
    # :check の答えが Program かの判定(`isinstance(v, (DoExpr, EffectBase))`)は実行時の展開だけに在る(card ki-601652993a18)。
    # 型検査の展開に在ると、pyright 1.1.414 は同じ process で前に検めた file によってこの tuple を
    # `tuple[UnionType, type[EffectBase[_Answer]]]` と読み、class_or_tuple の reportArgumentType を出した(agora-controllers の
    # 日次 — repo 全体を `--threads 2` で測った時だけ・同じ file の同じ形 2 つのうち 1 つ)。書き手は直せない。
    from doeff_hy._static_view import static_view

    runtime = _expand(MODULE)
    with static_view():
        static = _expand(MODULE)
    assert "doeff_hy.record.DoExpr" in runtime, runtime
    assert "doeff_hy.record.DoExpr" not in static, static
    assert "doeff_hy.record.EffectBase" not in static, static
    # 偽の答えを断る require_check は両方の展開に残り、macro の module(doeff_hy.record)でなく内側の module から読む
    assert "doeff_hy._record_check.require_check" in static, static
    assert "doeff_hy._record_check.require_check" in runtime, runtime
    assert "doeff_hy.record.require_check" not in runtime + static


def test_an_unread_bookkeeping_name_leaves_a_shared_import_alone() -> None:
    # 1 つの文に並んだ記帳の module の名のうち、読まれない declarations だけを外し、読まれる record は残す。
    tree = ast.parse(
        "import doeff_hy.declarations, doeff_hy.record\n"
        "isinstance(v, (doeff_hy.record.DoExpr, doeff_hy.record.EffectBase))\n"
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
    # record.pyi が宣言する名は record.hy に在り(宣言だけが先へ行かない)、関数を宣言しない — record.hy は macro のほかに
    # 関数を見せない(require_check は内側の doeff_hy._record_check・card ki-a28c20b93638)。
    stub = ast.parse(Path(record.__file__).with_suffix(".pyi").read_text(encoding="utf-8"))
    declared = [
        alias.asname or alias.name
        for node in stub.body
        if isinstance(node, ast.ImportFrom)
        for alias in node.names
    ] + [node.name for node in stub.body if isinstance(node, ast.FunctionDef)]
    assert [name for name in declared if not hasattr(record, name)] == []
    assert [node for node in stub.body if isinstance(node, ast.FunctionDef)] == []
    assert not hasattr(record, "require_check")
    assert record.DoExpr is doeff.DoExpr
    assert record.EffectBase is doeff.EffectBase
