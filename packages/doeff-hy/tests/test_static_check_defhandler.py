"""defhandler の展開の型(agora-redesign #2279)の失敗ケース。

前は defhandler の展開が作る関数に型が無く、strict の doeff-hy-check で handler 1 本につき書き手に直せない赤が
約 20 件出た: 節を回す関数の引数 k と答え(Generator[Transfer, Unknown, Unknown])・本文を受ける関数の引数
__doeff_body__ と答え(WithHandler[Unknown])・節の終わり方の検め(clause_endings は .hy で型が見えない)・
使わない Resume / Transfer の import・節の型で注記した effect への「いつも真」の isinstance。defk の (! …) も
注記の無い generator の yield の値(Unknown)で、使う所ごとに Unknown の赤になっていた。

- strict で、引数つき・:when つき・EffectBase の柵・節の中の <- を持つ defhandler と (! …) を使う defk の検体に赤が 0 件。
- 型は逃げていない: 節の答えの型違い、handler を被せた Program の答えの型違いは赤になる。
- 型検査のための展開だけが形を変え、実行時の展開(注記なし・Resume / Transfer / clause_endings の import)は今までどおり。
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

needs_pyright = pytest.mark.skipif(shutil.which("pyright") is None, reason="pyright が無い")

EFFECTS = """\
(import dataclasses [dataclass])
(import doeff [EffectBase])

(defclass [(dataclass :frozen True)] Tick [(get EffectBase int)]
  #^ int step)

(defclass [(dataclass :frozen True)] Name [(get EffectBase str)]
  #^ str prefix)

(defclass [(dataclass :frozen True)] Pair [(get EffectBase int)]
  #^ int left
  #^ int right)
"""

PROBE = """\
(require doeff-hy.macros [defk defhandler <- val])
(import doeff [EffectBase])
(import probe_effects [Tick Name Pair])

(defhandler counting
  "Tick と Name に答える。"
  {:tags {:context "probe" :role "protocol"}}
  (Tick [step]
    (val doubled (* step 2))
    (resume {TICK_ANSWER}))
  (Name [prefix]
    :when (> (len prefix) 0)
    (<- n int (Tick 1))
    (resume (+ prefix (str n)))))

(defhandler scaled [#^ int factor]
  (Tick [step]
    (transfer (* step factor))))

(defhandler only-tick
  (Tick [step]
    (resume step)))

(defhandler everything-else
  (EffectBase []
    :when (not (isinstance effect Tick))
    (reperform effect)))

(defhandler left-only
  "Pair の right を本体で使わない — 欄の名で束ねるので書き手は名を変えられない(agora-redesign #2514)。"
  (Pair [{PAIR_FIELDS}]
    (resume left)))

(defk use-handlers [start]
  {:pre [(: start int)] :post [(: % {USE_TYPE})]}
  (<- n (counting ((scaled 3) (only-tick (everything-else (Tick start))))))
  n)

(defk bang-sum [start]
  {:pre [(: start int)] :post [(: % int)]}
  (+ (! (Tick start)) (! (counting (Tick 2)))))
"""

BASE = {"TICK_ANSWER": "(+ doubled 1)", "USE_TYPE": "int", "PAIR_FIELDS": "left right"}


def _render(change: dict[str, str]) -> str:
    text = PROBE
    for key, value in (BASE | change).items():
        text = text.replace("{" + key + "}", value)
    return text


def _check(root: Path, text: str) -> list[dict[str, object]]:
    from doeff_hy.static_check import main

    (root / "probe_effects.hy").write_text(EFFECTS, encoding="utf-8")
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
def test_strict_has_no_error_from_defhandler_expansions(tmp_path: Path) -> None:
    assert _check(tmp_path, _render({})) == []


@needs_pyright
def test_wrong_clause_answer_is_still_caught(tmp_path: Path) -> None:
    # Tick は int で答える effect — 節が str で再開すれば typed_transfer の引数の型違い(節の型は逃げていない)。
    text = _render({"TICK_ANSWER": "(str doubled)"})
    errors = _check(tmp_path, text)
    assert ("reportArgumentType", _line_of(text, "(resume (str doubled))")) in [
        (d["rule"], d["line"]) for d in errors
    ], errors


@needs_pyright
def test_handled_program_keeps_the_body_answer_type(tmp_path: Path) -> None:
    # handler を被せた Program の答えは本文の答え(int — HandledScope[HandledAnswer])で、注記が型を逃がさない:
    # 束縛した値を str を返す defk の答えにすれば赤(前は pyright の呼び出し側の推論だけが運んでいた)。
    text = _render({"USE_TYPE": "str"})
    errors = _check(tmp_path, text)
    assert ("reportAssignmentType", _line_of(text, "  n)")) in [
        (d["rule"], d["line"]) for d in errors
    ], errors


@needs_pyright
def test_field_the_body_does_not_use_is_not_an_unused_variable(tmp_path: Path) -> None:
    # 節の欄 [left right] の right を本体が使わない。前は型検査のための展開が right = effect.right を束ねるだけで、
    # strict で reportUnusedVariable の赤になっていた(agora-redesign #2514 — 名は欄の名なので書き手に直せない)。
    text = _render({})
    errors = _check(tmp_path, text)
    assert [d for d in errors if d["rule"] == "reportUnusedVariable"] == [], errors


@needs_pyright
def test_misspelled_field_is_still_caught(tmp_path: Path) -> None:
    # 使わない欄を「読んだ」ことにしても、欄の名の綴りの誤りは effect の属性の読みで赤のまま。
    text = _render({"PAIR_FIELDS": "left rihgt"})
    errors = _check(tmp_path, text)
    assert ("reportAttributeAccessIssue", _line_of(text, "(Pair [left rihgt]")) in [
        (d["rule"], d["line"]) for d in errors
    ], errors


def _expand(source: str) -> str:
    # doeff_hy の import が Hy の importer と macro を用意する(副作用のための import)
    importlib.import_module("doeff_hy")
    return ast.unparse(hy.compiler.hy_compile(hy.read_many(source), "__main__"))


def test_static_view_annotates_only_the_static_expansion() -> None:
    from doeff_hy.static_view import static_view

    runtime = _expand(_render({}))
    with static_view():
        static = _expand(_render({}))
    # 型検査のための展開: 節を回す関数・本文を受ける関数に注記、節の終わり方の検めは module の頭の型付きの名
    assert "(effect: object, k: _doeff_Continuation) -> _doeff_ClauseRun:" in static, static
    assert (
        "def __doeff_handler_fn__(__doeff_body__: _doeff_HandlerBody[_doeff_HandledAnswer])"
        " -> _doeff_HandledScope[_doeff_HandledAnswer]:"
    ) in static
    assert "doeff_hy.clause_endings" not in static
    assert "from doeff import Pass" in static
    assert "Resume, Transfer" not in static
    # (! e) は <- と同じ _doeff_perform(yield の値は注記の無い generator では Unknown)
    assert "_doeff_perform(Tick(start)) + _doeff_perform(counting(Tick(2)))" in static
    # 実行時の展開: 注記なし・effect は節の型で注記(VM の絞り込み)・Resume / Transfer と clause_endings を import
    assert "def __doeff_handler_fn__(__doeff_body__):" in runtime, runtime
    assert "from doeff import Resume, Transfer, Pass" in runtime
    assert "from doeff_hy.clause_endings import check_clause_endings_once" in runtime
    assert "from doeff_hy.clause_endings import fell_through" in runtime
    for name in ("_doeff_Continuation", "_doeff_ClauseRun", "_doeff_HandlerBody", "_doeff_perform"):
        assert name not in runtime
    # 欄の束ねの「読んだ」印(_ = 欄)は型検査のための展開だけ(agora-redesign #2514)
    assert "right = effect.right\n" in static, static
    assert "_ = right\n" in static, static
    assert "right = effect.right\n" in runtime, runtime
    assert "_ = right" not in runtime, runtime


def test_every_handler_name_is_declared_in_the_stub() -> None:
    # 表(static_view.HANDLER_STATIC_NAMES)の名は static_types.pyi に宣言が在る(無ければ注記と呼びが Unknown になる)
    from doeff_hy import static_view

    stub = Path(static_view.__file__).with_name("static_types.pyi")
    body = ast.parse(stub.read_text(encoding="utf-8")).body
    declared = (
        {
            node.target.id
            for node in body
            if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name)
        }
        | {
            target.id
            for node in body
            if isinstance(node, ast.Assign)
            for target in node.targets
            if isinstance(target, ast.Name)
        }
        | {node.name for node in body if isinstance(node, ast.FunctionDef)}
    )
    assert [name for name in static_view.HANDLER_STATIC_NAMES if name not in declared] == []
