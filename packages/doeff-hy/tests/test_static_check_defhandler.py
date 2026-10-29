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
    assert (
        "(_doeff_effect: object, _doeff_k: _doeff_Continuation) -> _doeff_ClauseRun:" in static
    ), static
    assert (
        "def __doeff_handler_fn__(__doeff_body__: _doeff_HandlerBody[_doeff_HandledAnswer])"
        " -> _doeff_HandledScope[_doeff_HandledAnswer]:"
    ) in static
    assert "doeff_hy.clause_endings" not in static
    assert "from doeff import Pass as _doeff_Pass" in static
    assert "Resume as" not in static
    assert "Transfer as" not in static
    # (! e) は <- と同じ _doeff_perform(yield の値は注記の無い generator では Unknown)
    assert "_doeff_perform(Tick(start)) + _doeff_perform(counting(Tick(2)))" in static
    # 実行時の展開: 注記なし・effect は節の型で注記(VM の絞り込み)・Resume / Transfer と clause_endings を import
    assert "def __doeff_handler_fn__(__doeff_body__):" in runtime, runtime
    assert (
        "from doeff import Resume as _doeff_Resume, Transfer as _doeff_Transfer, Pass as _doeff_Pass"
        in runtime
    )
    assert "from doeff_hy.clause_endings import check_clause_endings_once" in runtime
    assert "from doeff_hy.clause_endings import fell_through" in runtime
    for name in ("_doeff_Continuation", "_doeff_ClauseRun", "_doeff_HandlerBody", "_doeff_perform"):
        assert name not in runtime
    # 欄の束ねの「読んだ」印(_ = 欄)は型検査のための展開だけ(agora-redesign #2514)
    assert "right = _doeff_effect.right\n" in static, static
    assert "_ = right\n" in static, static
    assert "right = _doeff_effect.right\n" in runtime, runtime
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


# 書き手が、展開の読む名(Transfer・Pass・Put)を自分の effect の名にした module(card ki-16180480129e)。前は型検査のための展開が
# `Pass` を素の名で import し、書き手の `Pass` の effect と 1 つの名を分け合っていた。展開は `_doeff_` の頭の別名だけを読む。
NAMED_EFFECTS = """\
(import dataclasses [dataclass])
(import doeff [EffectBase])

(defclass [(dataclass :frozen True)] Transfer [(get EffectBase int)]
  #^ int amount)

(defclass [(dataclass :frozen True)] Pass [(get EffectBase int)]
  #^ int reason)

(defclass [(dataclass :frozen True)] Put [(get EffectBase int)]
  #^ str key)
"""

NAMED_PROBE = """\
(require doeff-hy.macros [defk defhandler <- val])
(import probe_effects [Transfer Pass Put])

(defhandler ledger
  (session var posted 0)
  (Transfer [amount]
    (:= posted (+ posted amount))
    (resume posted))
  (Pass [reason]
    :when (> reason 0)
    (transfer reason))
  (Put [key]
    (reperform effect)))

(defk post-twice [amount]
  {:pre [(: amount int)] :post [(: % int)]}
  (<- first int (ledger (Transfer amount)))
  (<- second int (ledger (Pass first)))
  second)
"""


@needs_pyright
def test_writer_effects_named_like_the_expansion_names_are_not_an_error(tmp_path: Path) -> None:
    from doeff_hy.static_check import main

    (tmp_path / "probe_effects.hy").write_text(NAMED_EFFECTS, encoding="utf-8")
    (tmp_path / "probe.hy").write_text(NAMED_PROBE, encoding="utf-8")
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        main(
            [
                "--root",
                str(tmp_path),
                "--json",
                "--no-cache",
                "--strict",
                str(tmp_path / "probe.hy"),
            ]
        )
    printed = out.getvalue()
    found: list[dict[str, object]] = json.loads(printed) if printed.strip() else []
    assert [d for d in found if d["severity"] == "error"] == []


def test_static_expansion_reads_only_aliases_of_the_expansion_names() -> None:
    """型検査のための展開は、書き手の Transfer・Pass・Put の effect と 1 つの名を分け合わない — 読む名は `_doeff_` の別名。"""
    from doeff_hy.static_view import static_view

    with static_view():
        static = _expand(NAMED_PROBE)
    assert "_doeff_Pass(_doeff_effect, _doeff_k)" in static, static
    assert "_doeff_WithHandlerType(" in static, static
    assert "_doeff_Put(" in static, static
    assert "from doeff import Pass as _doeff_Pass" in static, static


# 書き手が、節の欄を展開の局所名と同じ `k`・`effect` にした module(card ki-6af9862aed32)。前は節を回す関数の引数 `effect`・`k` を
# 欄の束ねが上書きし、続きの型 `Continuation` の `k` に `int` を代入する赤が出た。展開の局所名は `_doeff_effect`・`_doeff_k` で、
# 書き手が `effect`・`k` と書いた節だけが、同じ名を節の effect・続きとして読める。
FIELD_NAMED_EFFECTS = """\
(import dataclasses [dataclass])
(import doeff [EffectBase])

(defclass [(dataclass :frozen True)] Kv [(get EffectBase int)]
  #^ int k
  #^ int v)

(defclass [(dataclass :frozen True)] Note [(get EffectBase int)]
  #^ int effect
  #^ int extra)

(defclass [(dataclass :frozen True)] Bump [(get EffectBase int)]
  #^ int n)
"""

FIELD_NAMED_PROBE = """\
(require doeff-hy.macros [defk defhandler <- val])
(import doeff [EffectBase])
(import probe_effects [Kv Note Bump])

(defhandler keyed
  (Kv [k v]
    :when (> k 5)
    (resume (+ k v)))
  (Note [effect extra]
    (resume (+ effect extra))))

(defhandler fence
  (EffectBase []
    :when (not (isinstance effect Kv))
    (reperform effect))
  (Kv [k v]
    (resume (+ k v))))

(defhandler reads-its-effect
  (Bump [n]
    (resume (+ n effect.n)))
  (Note [extra]
    (resume (+ extra (sum (lfor k [1 2 3] k))))))

(defk use-keyed [start]
  {:pre [(: start int)] :post [(: % int)]}
  (<- first int (keyed (Kv start 1)))
  (<- second int (fence (keyed (Note start 2))))
  (+ first second))
"""


@needs_pyright
def test_clause_fields_named_k_and_effect_are_not_an_error(tmp_path: Path) -> None:
    from doeff_hy.static_check import main

    (tmp_path / "probe_effects.hy").write_text(FIELD_NAMED_EFFECTS, encoding="utf-8")
    (tmp_path / "probe.hy").write_text(FIELD_NAMED_PROBE, encoding="utf-8")
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        main(
            [
                "--root",
                str(tmp_path),
                "--json",
                "--no-cache",
                "--strict",
                str(tmp_path / "probe.hy"),
            ]
        )
    printed = out.getvalue()
    found: list[dict[str, object]] = json.loads(printed) if printed.strip() else []
    assert [d for d in found if d["severity"] == "error"] == []


def test_static_expansion_binds_the_writers_effect_and_k_only_where_the_clause_writes_them() -> (
    None
):
    """書き手が `effect`・`k` を書かない節は、型検査のための展開でも素の名を束ねない。書いた節の在る handler だけが、節を回す関数の
    頭で `effect` を絞る前の値に束ね、書いた節の判定が `effect` も節の型へ絞る(絞った型のまま読める)。`k` は書いた節が宣言して
    読む。欄の名が同じ名の節は束ねず、欄が優先する。"""
    from doeff_hy.static_view import static_view

    with static_view():
        static = _expand(
            FIELD_NAMED_EFFECTS
            + FIELD_NAMED_PROBE.replace("(import probe_effects [Kv Note Bump])", "")
        )
    assert "isinstance(_doeff_effect, EffectBase) and isinstance(effect, EffectBase)" in static, (
        static
    )
    assert "isinstance(_doeff_effect, Bump) and isinstance(effect, Bump)" in static, static
    # `effect` を書いた節の在る handler は fence・reads-its-effect の 2 本。keyed は欄を `effect` にした節だけなので束ねない
    assert static.count("    effect = _doeff_effect\n") == 2, static
    assert "k: _doeff_Continuation = _doeff_k" in static, static
    # 欄 k の節は、欄を `k` に束ねる(続きを上書きしない)— `_doeff_k` を読む Pass は残る
    assert "k = _doeff_effect.k" in static, static
    assert "_doeff_Pass(_doeff_effect, _doeff_k)" in static, static


def _first_if(statements: list[ast.stmt]) -> ast.If | None:
    return next((s for s in statements if isinstance(s, ast.If)), None)


def _if_chain(clause: ast.If | None) -> list[ast.expr]:
    """この `if` の条件と、その `else` の中に続く `if` の条件の連なり。"""
    if clause is None:
        return []
    return [clause.test, *_if_chain(_first_if(clause.orelse))]


def _clause_tests(static: str) -> list[ast.expr]:
    """型検査のための展開の、節を回す関数(第 1 引数が `_doeff_effect`)が並べる節の判定の式の list。節の判定は関数の直下の
    `if` と、その `else` の中に続く `if` の連なり(書き手の本体の中の `if` は数えない)。"""
    return [
        test
        for function in ast.walk(ast.parse(static))
        if isinstance(function, ast.FunctionDef)
        and function.args.args
        and function.args.args[0].arg == "_doeff_effect"
        for test in _if_chain(_first_if(function.body))
    ]


def _isinstance_subjects(test: ast.expr) -> list[str]:
    """判定の式の中の `isinstance` が絞る値(第 1 引数)の綴りの list。"""
    return [
        ast.unparse(node.args[0])
        for node in ast.walk(test)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "isinstance"
    ]


# 節の判定が同じ値を 2 度絞らない事(card ki-a06b0fbce50d)。前は `effect` を書いた節の判定が
# `isinstance((effect := _doeff_effect), T) and isinstance(_doeff_effect, T)` で、walrus の右辺も絞る pyright(1.1.414。1.1.411 は
# 左辺だけを絞る)では 2 つ目が「いつも真」(reportUnnecessaryIsInstance)になった — `effect` を読む節ごとに 1 件・書き手は直せない
# (agora-controllers の cli_host の検 3 file で 25 件)。walrus の絞りは pyright の版で変わるので、判定は walrus を使わず、
# 別の名を 1 度ずつ絞る。この検は pyright を撃たずに展開の形を読む(この repo の lock の pyright の版では赤が出ない)。
@pytest.mark.parametrize(
    ("label", "clauses"),
    [("field-named", 6), ("generic", 5), ("probe", 6), ("named", 3)],
)
def test_static_clause_test_narrows_each_name_once(label: str, clauses: int) -> None:
    from doeff_hy.static_view import static_view

    sources = {
        "field-named": FIELD_NAMED_EFFECTS
        + FIELD_NAMED_PROBE.replace("(import probe_effects [Kv Note Bump])", ""),
        "generic": _generic({}),
        "probe": EFFECTS + _render({}).replace("(import probe_effects [Tick Name Pair])", ""),
        "named": NAMED_EFFECTS
        + NAMED_PROBE.replace("(import probe_effects [Transfer Pass Put])", ""),
    }
    with static_view():
        static = _expand(sources[label])
    tests = _clause_tests(static)
    assert len(tests) == clauses, static
    for test in tests:
        shown = ast.unparse(test)
        assert not [node for node in ast.walk(test) if isinstance(node, ast.NamedExpr)], shown
        subjects = _isinstance_subjects(test)
        assert "_doeff_effect" in subjects, shown
        assert len(subjects) == len(set(subjects)), shown


@needs_pyright
def test_writers_effect_keeps_the_clause_effect_type(tmp_path: Path) -> None:
    # 型は逃げていない: 書き手の `effect` は節の effect の型のまま読め、無い欄の読みは赤になる(Any・object に落ちていない)。
    from doeff_hy.static_check import main

    text = FIELD_NAMED_PROBE.replace("(resume (+ n effect.n))", "(resume (+ n effect.missing))")
    (tmp_path / "probe_effects.hy").write_text(FIELD_NAMED_EFFECTS, encoding="utf-8")
    (tmp_path / "probe.hy").write_text(text, encoding="utf-8")
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        main(
            [
                "--root",
                str(tmp_path),
                "--json",
                "--no-cache",
                "--strict",
                str(tmp_path / "probe.hy"),
            ]
        )
    printed = out.getvalue()
    found: list[dict[str, object]] = json.loads(printed) if printed.strip() else []
    errors = [d for d in found if d["severity"] == "error"]
    assert ("reportAttributeAccessIssue", _line_of(text, "effect.missing")) in [
        (d["rule"], d["line"]) for d in errors
    ], errors


# 総称の effect(Spawn[_T, _E]・Race[_T]・Wait[_T])を head にした節(card ki-fede97890f70)。pyright は節の `isinstance` が
# `object` の effect を絞る型の型引数を Unknown にする(`Race[Unknown]`・`Spawn[Unknown, Unknown]`)ので、前は欄の束ね
# (`tasks = _doeff_effect.tasks`)と typed_resume / typed_transfer の effect の引数が一部 Unknown の赤になり、書き手は直せなかった
# (agora-controllers の agora_sim の検 2 file・9 件 — 欄の名は effect の定義で決まる)。展開は節の effect を構成子の呼びで束ね直し、
# 型引数を Any で解く(`Race[Any]`)ので、欄の型は effect の定義の型のままになる。
GENERIC_PROBE = """\
(require doeff-hy.macros [defk defhandler <- val])
(import doeff_core_effects._scheduler [Spawn Race Wait Task])

(defn takes-int [#^ int n]
  n)

(defhandler reads-generic-fields [#^ (get Task int) made]
  {:tags {:context "probe" :role "protocol"}}
  (Spawn [program]
    {SPAWN_USE}
    (resume {SPAWN_ANSWER}))
  (Race [tasks]
    {RACE_USE}
    (<- first object (Wait (get tasks 0)))
    (resume first))
  (Wait [task]
    (resume (takes-int (len (repr task))))))

(defhandler race-answers-at-once
  (Race []
    (resume "")))

(defhandler spawn-is-only-counted [#^ (get list int) seen]
  (Spawn []
    (.append seen 1)
    (reperform effect)))
"""

GENERIC_BASE = {
    "SPAWN_USE": "(takes-int (len (repr program)))",
    "SPAWN_ANSWER": "made",
    "RACE_USE": "(takes-int (len tasks))",
}


def _generic(change: dict[str, str]) -> str:
    text = GENERIC_PROBE
    for key, value in (GENERIC_BASE | change).items():
        text = text.replace("{" + key + "}", value)
    return text


def _errors_at(errors: list[dict[str, object]], line: int) -> list[dict[str, object]]:
    return [d for d in errors if d["line"] == line]


@needs_pyright
def test_generic_effect_clauses_have_no_unknown_type_error(tmp_path: Path) -> None:
    # 欄(program・tasks・task)を束ねる節、欄を持たず resume だけの節、何も読まない節のどれも赤が 0 件。
    assert _check(tmp_path, _generic({})) == []


@needs_pyright
def test_generic_effect_field_keeps_the_type_of_its_definition(tmp_path: Path) -> None:
    # 欄は Any に逃げない: Race の tasks は定義の型 `tuple[Task[object] | Future[object], ...]`(Task・Future を共変にした
    # 25f1dcd02 から、Race の実装の __init__ はこの型で受ける)、Spawn の program は `Program[Any, Any]` なので、int の引数へ
    # 渡せば赤(入れ物の形は残る)。
    race = _generic({"RACE_USE": "(takes-int tasks)"})
    race_errors = _errors_at(_check(tmp_path, race), _line_of(race, "(takes-int tasks)"))
    assert [d["rule"] for d in race_errors] == ["reportArgumentType"], race_errors
    assert "Task[object] | Future[object]" in str(race_errors[0]["message"]), race_errors

    spawn = _generic({"SPAWN_USE": "(takes-int program)"})
    spawn_errors = _errors_at(_check(tmp_path, spawn), _line_of(spawn, "(takes-int program)"))
    assert [d["rule"] for d in spawn_errors] == ["reportArgumentType"], spawn_errors
    assert "Program[Any, Any]" in str(spawn_errors[0]["message"]), spawn_errors


@needs_pyright
def test_wrong_answer_to_a_generic_effect_is_still_caught(tmp_path: Path) -> None:
    # Spawn は Task で答える effect(`EffectBase[Task[_T]]`)— 節が Task で再開すれば赤は 0 件(test_generic_effect_clauses_have_no_
    # unknown_type_error)、int で再開すれば typed_transfer の引数の型違い(末尾の resume は transfer に替わる)。
    text = _generic({"SPAWN_ANSWER": "1"})
    errors = _errors_at(_check(tmp_path, text), _line_of(text, "(resume 1)"))
    assert [d["rule"] for d in errors] == ["reportArgumentType"], errors


def test_static_expansion_rebinds_the_effect_only_where_the_clause_reads_it() -> None:
    """節が欄を束ねるか typed_resume で effect を読む時だけ、絞った `_doeff_effect` を型引数がすべて Any の型に束ね直す。
    何も読まない節と実行時の展開は変わらない。"""
    from doeff_hy.static_view import static_view

    runtime = _expand(_generic({}))
    with static_view():
        static = _expand(_generic({}))
    rebind = "_doeff_effect = _doeff_any_specialized({}, *_doeff_any_args, **_doeff_any_kwargs)"
    for effect in ("Spawn", "Race", "Wait"):
        assert rebind.format(effect) in static, static
    # 欄を持たず resume だけの節(typed_resume が effect を読む)も束ね直す。何も読まない `(Spawn [] … (reperform effect))` は束ね直さない
    assert static.count("_doeff_any_specialized(") == 4, static
    assert "any_specialized" not in runtime, runtime
    # 束ね直しは欄の束ねより前(欄が定義の型で読まれる)
    assert static.index(rebind.format("Race")) < static.index("tasks = _doeff_effect.tasks"), static
