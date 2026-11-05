"""Check that the local names `effect` and `k` in the expansion of handle / defhandler clauses do not collide with the writer's clause field names (continuation of tests/test_handle_writer_names.py).

The expansion binds and reads the local names `effect` (the received effect) and `k` (the continuation) for the arguments of the function that runs the clauses and for clause operations. When a writer gives a clause field the same name
(`(Kv [k v] ...)` of KV), the field binding `(setv k (. effect k))` overwrote the continuation, and `(resume ...)` failed with `TypeError: argument 'k': 'int' object is
not an instance of 'K'`. For a clause whose field is `effect`, the following field binding `(. effect next-field)` read the overwritten value, and the effect that a clause whose `:when` did not match, or `(pass)`, forwards outward
also became the field value.

The local names the expansion binds and reads for itself are `_doeff_effect` and `_doeff_k` (aliases with the `_doeff_` prefix), which are not shared with the writer's field and session names.
When the writer's body writes `effect` or `k` (`(reperform effect)`, `:when (isinstance effect X)`), the same names stay readable as the clause's effect and continuation --
if a field or session name is the same name, the writer's name takes precedence.

- Clauses whose field or session name is `k` or `effect` pass resume, a `:when` that does not match, and `(pass)` with the writer's meaning, in handle at module level, in a defk body, in a plain defn body, in a class body, and in defhandler.

- For clauses where the writer does not write `effect` or `k`, the expansion does not bind bare `effect` or `k` (both the runtime expansion and the expansion for type checking).
- The arguments of the function that runs the clauses and of the `Pass` forwarded outward are `_doeff_effect` and `_doeff_k`.
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
from doeff_hy._static_view import static_view

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

# handle expressions. All of them answer 101.
# - field-*: a clause whose field name is k or effect (the same name as the expansion's local name) resumes
# - *-guard-pass: the clause's :when does not match, and the inner handler passes the original effect and original continuation to the outer handler
# - both-pass: the (pass) (old form, with a warning) of a clause whose fields are k, effect, and extra forwards outward
# - bare-*: effect and k written by the body, not as fields, are read as the clause's effect and continuation (as before)
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

# defhandler (the session name is var). Answers 101.
# - *-session: a clause whose field name is k or effect passes the := of the session var
# - session-named-*: the session name itself is k or effect (the same name as the clause's local name)
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
    """Load Hy sources into fresh modules in order. The warning for the old form `(pass)` is outside what the test cares about."""
    mod = types.ModuleType("handle_clause_local_names")
    sys.modules["handle_clause_local_names"] = mod
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        for source in sources:
            for form in hy.read_many(source):
                hy.eval(form, mod.__dict__, module=mod)
    return mod


def _run_with_state(program: object) -> object:
    """The state handler receives Get and Put of the session var."""
    return run(scheduled(state(initial={})(program)))


class _Scenario(NamedTuple):
    """The Hy source written to the host, and a function that obtains a Program from the module that loaded it."""

    source: str
    program: Callable[[types.ModuleType], object]


def _host(host: str, definition: str, expression: str) -> _Scenario:
    """The source with the definition (the handler definition; empty if none) and the answer-producing expression written to the host, and how to obtain the Program."""
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
    """A clause whose field or session name is `k` or `effect` answers with the meaning the writer wrote -- if the expansion's local name shared one name, the field binding would overwrite
    the continuation so that resume raises TypeError, the next field read would become an attribute of the field value, and the effect forwarded outward would become the field value."""
    mod = _eval_module(_HEADER, source)
    assert _run_with_state(program(mod)) == 101


# ---------------------------------------------------------------------------
# Expansion structure -- the runtime expansion and the expansion for type checking do not bind a bare effect or k that the writer did not write
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
    """Expand the Hy source into a Python AST (without evaluating). For static, the expansion for type checking."""
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
    """The expansion of a clause where the writer does not write `effect` or `k` binds neither a bare `effect` nor `k` as arguments or assignment targets -- the arguments of the function that runs the clauses are
    `_doeff_effect` and `_doeff_k` (the header definitions Kv, Note, and Both write them as field names, but the header is not a target of expansion and is subtracted)."""
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
    """The `Pass` arguments forwarded outward by a clause whose `:when` did not match are `_doeff_effect` and `_doeff_k` even when the field names are `k` and `effect` (not the field values)."""
    tree = _expand(source, static=static)
    passes = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "_doeff_Pass"
    ]
    assert passes, "the expansion produced no Pass"
    assert [
        [arg.id if isinstance(arg, ast.Name) else None for arg in call.args] for call in passes
    ] == [["_doeff_effect", "_doeff_k"]] * len(passes)
