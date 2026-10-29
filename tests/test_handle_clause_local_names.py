"""handle・defhandler の節の展開の局所名 `effect`・`k` は、書き手の節の欄の名と衝突しない事の検(card ki-6af9862aed32 —
card ki-16180480129e の続き)。

展開は節を回す関数の引数と節の操作のために、局所名 `effect`(受けた effect)と `k`(続き)を束ねて読む。書き手が節の欄を同じ名にすると
(KV の `(Kv [k v] …)`)、欄の束ね `(setv k (. effect k))` が続きを上書きし、`(resume …)` が `TypeError: argument 'k': 'int' object is
not an instance of 'K'` で落ちた。欄を `effect` にした節は、続く欄の束ね `(. effect 次の欄)` が上書き後の値を読み、`:when` の外れた
節や `(pass)` が外へ渡す effect も欄の値になっていた。

展開が自分のために束ねて読む局所名は `_doeff_effect`・`_doeff_k`(`_doeff_` の頭の別名)で、書き手の欄・session の名と分け合わない。
書き手の本体が `effect`・`k` を書いた時(`(reperform effect)`・`:when (isinstance effect X)`)は、同じ名を節の effect・続きとして
読めるまま — 欄や session の名が同じ名なら、書き手の名が優先する。

- 欄・session の名を `k`・`effect` にした節が、module の段・defk の本体・素の defn の本体・class の本体の handle・defhandler で、
  resume・`:when` の外れ・`(pass)` を書き手の意味どおりに通す。
- 書き手が `effect`・`k` を書かない節では、展開は素の `effect`・`k` を束ねない(実行時と型検査のための展開の両方)。
- 節を回す関数の引数と、外へ渡す `Pass` の引数は `_doeff_effect`・`_doeff_k`。
"""

import ast
import contextlib
import sys
import types
import warnings
from collections.abc import Callable
from typing import NamedTuple

import doeff_hy  # noqa: F401 — registers extensions
import hy
import pytest
from doeff_core_effects import scheduled, state
from doeff_hy.static_view import static_view

from doeff import run

_HEADER = """
(require doeff-hy.macros [defk <- handle defhandler])
(import dataclasses [dataclass])
(import doeff [EffectBase])
(defclass [(dataclass :frozen True)] Kv [EffectBase] #^ int k #^ int v)
(defclass [(dataclass :frozen True)] Note [EffectBase] #^ int effect #^ int extra)
(defclass [(dataclass :frozen True)] Both [EffectBase] #^ int k #^ int effect #^ int extra)
(defclass [(dataclass :frozen True)] Bump [EffectBase] #^ int n)
"""

# handle の式。どれも答えは 101。
# - field-*: 欄の名を k・effect にした節(展開の局所名と同じ名)が resume する
# - *-guard-pass: その節の :when が外れ、内側の handler が元の effect と元の続きを外の handler へ渡す
# - both-pass: 欄が k・effect・extra の節の (pass)(旧い形・警告つき)が外へ渡す
# - bare-*: 欄でなく本体が書いた effect・k を、節の effect・続きとして読む(今までどおり)
_EXPRESSIONS = {
    "field-k": "(handle (Kv 1 100) (Kv [k v] (resume (+ k v))))",
    "field-effect": "(handle (Note 1 100) (Note [effect extra] (resume (+ effect extra))))",
    "field-both": "(handle (Both 1 2 98) (Both [k effect extra] (resume (+ k effect extra))))",
    "k-guard-pass": """
        (handle
          (handle (Kv 1 100) (Kv [k v] :when (> k 5) (resume 0)))
          (Kv [k v] (resume (+ k v))))
        """,
    "effect-guard-pass": """
        (handle
          (handle (Note 1 100) (Note [effect extra] :when (> effect 5) (resume 0)))
          (Note [effect extra] (resume (+ effect extra))))
        """,
    "both-pass": """
        (handle
          (handle (Both 1 2 98) (Both [k effect extra] (pass)))
          (Both [k effect extra] (resume (+ k effect extra))))
        """,
    "bare-names": "(handle (Bump 1) (Bump [n] (resume (+ (. effect n) (if (is-not k None) 100 0)))))",
    "bare-dotted": "(handle (Bump 1) (Bump [n] (resume (+ effect.n 100))))",
    "bare-reperform": """
        (handle
          (handle (Bump 1) (Bump [n] (reperform effect)))
          (Bump [n] (resume (+ n 100))))
        """,
}

# defhandler(session の名は var)。答えは 101。
# - *-session: 欄の名が k・effect の節が session var の := を通す
# - session-named-*: session の名そのものが k・effect(節の局所名と同じ名)
_DEFHANDLERS = {
    "field-k-session": (
        "(defhandler h (session var calls 99) (Kv [k v] (:= calls (+ calls 1)) (resume (+ k calls))))",
        "(h (Kv 1 0))",
    ),
    "field-effect-session": (
        "(defhandler h (session var calls 99) "
        "(Note [effect extra] (:= calls (+ calls 1)) (resume (+ effect calls))))",
        "(h (Note 1 0))",
    ),
    "session-named-k": (
        "(defhandler h (session var k 99) (Bump [n] (:= k (+ k 1)) (resume (+ n k))))",
        "(h (Bump 1))",
    ),
    "session-named-effect": (
        "(defhandler h (session var effect 99) (Bump [n] (:= effect (+ effect 1)) (resume (+ n effect))))",
        "(h (Bump 1))",
    ),
}

_HOSTS = ("module", "defk", "defn", "class")


def _eval_module(*sources: str) -> types.ModuleType:
    """Hy の source を順に新しい module に読み込む。旧い形 `(pass)` の警告は検の関心の外。"""
    mod = types.ModuleType("handle_clause_local_names")
    sys.modules["handle_clause_local_names"] = mod
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        for source in sources:
            for form in hy.read_many(source):
                hy.eval(form, mod.__dict__, module=mod)
    return mod


def _run_with_state(program: object) -> object:
    """session var の Get・Put を state の handler が受ける。"""
    return run(scheduled(state(initial={})(program)))


class _Scenario(NamedTuple):
    """host に書いた Hy の source と、それを読み込んだ module から Program を取る関数。"""

    source: str
    program: Callable[[types.ModuleType], object]


def _host(host: str, definition: str, expression: str) -> _Scenario:
    """definition(handler の定義。無ければ空)と、答えを出す式 expression を host に書いた source と、Program の取り方。"""
    match host:
        case "module":
            return _Scenario(f"{definition}(setv program {expression})", lambda mod: mod.program)
        case "defk":
            return _Scenario(
                f"(defk subject [] {{:pre [] :post [(: % int)]}} {definition} (<- r {expression}) r)",
                lambda mod: mod.subject(),
            )
        case "defn":
            return _Scenario(f"(defn host [] {definition} {expression})", lambda mod: mod.host())
        case "class" if not definition:
            return _Scenario(f"(defclass C [] (setv program {expression}))", lambda mod: mod.C.program)
        case _:
            raise AssertionError(host)


def _scenarios() -> dict[str, _Scenario]:
    found: dict[str, _Scenario] = {}
    for kind, expression in _EXPRESSIONS.items():
        for host in _HOSTS:
            found[f"{kind}-{host}-handle"] = _host(host, "", expression)
    for kind, (definition, program) in _DEFHANDLERS.items():
        for host in ("module", "defk", "defn"):
            found[f"{kind}-{host}-defhandler"] = _host(host, definition, program)
    return found


_SCENARIOS = _scenarios()


@pytest.mark.parametrize(("source", "program"), _SCENARIOS.values(), ids=_SCENARIOS.keys())
def test_clause_fields_named_like_the_expansions_local_names_are_the_writers(
    source: str, program: Callable[[types.ModuleType], object]
) -> None:
    """欄・session の名を `k`・`effect` にした節は、書き手が書いた意味で答える — 展開の局所名と 1 つの名を分け合うと、欄の束ねが
    続きを上書きして resume が TypeError になり、次の欄の読みが欄の値の属性になり、外へ渡す effect が欄の値になる。"""
    mod = _eval_module(_HEADER, source)
    assert _run_with_state(program(mod)) == 101


# ---------------------------------------------------------------------------
# 展開の構造 — 実行時と型検査のための展開が、書き手の書かない素の effect・k を束ねない
# ---------------------------------------------------------------------------

_STRUCTURE_SOURCES = {
    "handle": "(setv program (handle (Bump 1) (Bump [n] (resume (+ n 100)))))",
    "handle-guard": (
        "(setv program (handle (Bump 1) (Bump [n] :when (> n 5) (resume 0)) (Bump [n] (resume n))))"
    ),
    "defhandler-session": "(defhandler h (session var calls 99) (Bump [n] (:= calls (+ calls 1)) (resume (+ n calls))))",
    "defhandler-pass": "(defhandler h (Bump [n] :when (> n 5) (resume n)))",
}

_FIELD_SOURCES = {
    "field-k": "(setv program (handle (Kv 1 100) (Kv [k v] :when (> k 5) (resume (+ k v))) (Kv [k v] (resume v))))",
    "field-effect": (
        "(setv program (handle (Note 1 100) (Note [effect extra] :when (> effect 5) (resume extra))))"
    ),
}


def _expand(source: str, *, static: bool) -> ast.Module:
    """Hy の source を展開して Python の AST にする(評価しない)。static なら型検査のための展開。"""
    mod = types.ModuleType("handle_clause_local_names_expand")
    sys.modules["handle_clause_local_names_expand"] = mod
    with static_view() if static else contextlib.nullcontext():
        return hy.compiler.hy_compile(
            hy.read_many(_HEADER + source), mod, filename="<clause-local-names>"
        )


def _function_parameters(tree: ast.Module) -> set[str]:
    return {
        arg.arg
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef)
        for arg in node.args.args
    }


def _stored_names(tree: ast.Module) -> set[str]:
    return {
        node.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store)
    }


@pytest.mark.parametrize("static", [False, True], ids=["runtime", "static"])
@pytest.mark.parametrize("source", _STRUCTURE_SOURCES.values(), ids=_STRUCTURE_SOURCES.keys())
def test_expansion_binds_no_bare_effect_or_k_the_writer_did_not_write(
    source: str, static: bool
) -> None:
    """書き手が `effect`・`k` を書いていない節の展開は、素の `effect`・`k` を引数にも代入先にも束ねない — 節を回す関数の引数は
    `_doeff_effect`・`_doeff_k`(header の定義 Kv・Note・Both は欄の名として書くが、header は展開の対象でなく差し引く)。"""
    tree = _expand(source, static=static)
    header_tree = _expand("", static=static)
    bound = (_function_parameters(tree) | _stored_names(tree)) - (
        _function_parameters(header_tree) | _stored_names(header_tree)
    )
    assert bound & {"effect", "k"} == set()
    assert {"_doeff_effect", "_doeff_k"} <= _function_parameters(tree)


@pytest.mark.parametrize("static", [False, True], ids=["runtime", "static"])
@pytest.mark.parametrize("source", _FIELD_SOURCES.values(), ids=_FIELD_SOURCES.keys())
def test_expansion_passes_the_clauses_own_effect_and_continuation_on(
    source: str, static: bool
) -> None:
    """`:when` の外れた節が外へ渡す `Pass` の引数は、欄の名が `k`・`effect` でも `_doeff_effect`・`_doeff_k`(欄の値でない)。"""
    tree = _expand(source, static=static)
    passes = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "_doeff_Pass"
    ]
    assert passes, "展開が Pass を出していない"
    assert [
        [arg.id if isinstance(arg, ast.Name) else None for arg in call.args] for call in passes
    ] == [["_doeff_effect", "_doeff_k"]] * len(passes)
