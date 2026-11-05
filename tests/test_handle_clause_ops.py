"""Test that the names and properties of handler clause operations (resume, transfer, finish, reperform, pass, raise) live in exactly one table (CLAUSE-OPS in doeff_hy/_clause_ops.hy)
(blind A of the design review).

Previously the list of names existed as copies in `_terminates` in handle.hy and `ENDING-OPS` / `RESUMING-OPS` in clause_endings.hy. Adding a new operation
(e.g. `(throw exception)` -> TransferThrow) only to `_terminates` and the rewrite let `(Raise [reason] (throw ...))` pass every check -- expansion, handle
evaluation, and execution -- so the Raise continuation ran and the answer 42 was returned. Even in the expansion for type checking, TransferThrow was read without being
imported.

- Adding a row to the table makes the check for whether a clause terminates, the rewrite, the node import (in the expansion for runtime and for type checking), and the check for
  resuming Raise / Absent clauses (at expansion time and at handle evaluation time) all follow the added operation. An operation treated like resume (returns to the clause after the
  continuation; replaced by another operation in tail position) can also be added with just table rows (returns, tail). Removing a row stops every reader from treating it as an operation.
- Every row of the table is checked in Raise / Absent / plain effect clauses according to how the row ends (Ending).
- Of the names written in handle.hy and clause_endings.hy, those not in the table are not treated as clause operations by any reader. No module of doeff_hy
  writes an operation name outside the table -- adding a name to a reader without going through the table is red (blind A and B of the design review).
"""

import ast
import contextlib
import re
import sys
import types
from collections.abc import Callable
from pathlib import Path

import doeff_hy  # registers the extension (_READERS reads the location's __file__)
import hy
import pytest
from doeff_hy import _clause_endings as clause_endings
from doeff_hy import _clause_ops as clause_ops
from doeff_hy import _expansion_names as expansion_names
from doeff_hy import handle
from doeff_hy._clause_ops import ClauseOp, Ending
from doeff_hy._expansion_names import alias, alias_symbol, continuation_symbol
from doeff_hy._static_view import static_view
from hy.models import Expression, Object, Sequence, String, Symbol

from doeff import run

_HEADER = """
(require doeff-hy.macros [defk <- handle])
(import dataclasses [dataclass])
(import doeff [EffectBase])
(import doeff_core_effects._effects [Raise Absent])
(defclass [(dataclass :frozen True)] Bump [EffectBase] #^ int n)
(defk guarded [n] {:pre [(: n int)] :post [(: % int)]}
  (try (do (<- y (Bump n)) y) (except [e ValueError] -1)))
(defk raises [] {:pre [] :post [(: % int)]}
  (try (do (<- (Raise "x")) 0) (except [e ValueError] 42)))
"""


def _module(name: str) -> types.ModuleType:
    mod = types.ModuleType(name)
    sys.modules[name] = mod
    return mod


def _eval_module(code: str) -> types.ModuleType:
    """Load Hy source into a new module -- to keep each test's module globals separate."""
    mod = _module("handle_clause_ops")
    for form in hy.read_many(_HEADER + code):
        hy.eval(form, mod.__dict__, module=mod)
    return mod


def _expand(code: str, *, static: bool = False) -> ast.Module:
    """Expand Hy source into a Python AST (without evaluating). If static, the expansion for type checking."""
    mod = _module("handle_clause_ops_expand")
    view = static_view() if static else contextlib.nullcontext()
    with view:
        return hy.compiler.hy_compile(hy.read_many(_HEADER + code), mod, filename="<clause-ops>")


def _expansion_refusal(code: str) -> str:
    """The error message from expansion (an empty string if it passes)."""
    try:
        _expand(code)
    except hy.errors.HyMacroExpansionError as error:
        return str(error)
    return ""


def _doeff_imports(tree: ast.Module) -> frozenset[str]:
    """The names the expansion imports from doeff."""
    return frozenset(
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module == "doeff"
        for alias in node.names
    )


def _read_names(tree: ast.Module) -> frozenset[str]:
    """The names the expansion reads."""
    return frozenset(
        node.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load)
    )


# ---------------------------------------------------------------------------
# Adding a row to the table makes every reader follow
# ---------------------------------------------------------------------------


def _rewrite_throw(args: list[Object], recur: Callable[[Object], Object]) -> Object:
    """(throw exception) -> throw the exception into the continuation and detach the handler (tail position) -- the same shape as the operation blind A added."""
    return Expression(
        [
            Symbol("yield"),
            Expression([alias_symbol("TransferThrow"), continuation_symbol(), recur(args[0])]),
        ]
    )


_THROW = ClauseOp(
    name="throw",
    ending=Ending.RESUMES,
    arity=1,
    returns=False,
    tail=None,
    nodes=("TransferThrow",),
    static_nodes=("TransferThrow",),
    rewrite=_rewrite_throw,
)


@pytest.fixture
def throw_op(monkeypatch: pytest.MonkeyPatch) -> ClauseOp:
    """Add the row throw to the table (add only the table row; do not fix the readers)."""
    monkeypatch.setattr(clause_ops, "CLAUSE_OPS", (*clause_ops.CLAUSE_OPS, _THROW))
    # The table of expansion names is closed (names not in the table are refused by home) -- also add the added row's node location to the table
    monkeypatch.setitem(expansion_names._HOMES, "TransferThrow", "doeff")
    return _THROW


def test_added_operation_ends_a_clause_and_runs(throw_op: ClauseOp) -> None:
    """The check for whether a clause terminates, the rewrite, and the runtime node import follow the operation added to the table (the try around the continuation receives the exception)."""
    mod = _eval_module(
        """
        (defk subject [] {:pre [] :post [(: % int)]}
          (<- r (handle (guarded 1) (Bump [n] (throw (ValueError "boom")))))
          r)
        """
    )
    assert run(mod.subject()) == -1


def test_added_resuming_operation_is_refused_in_a_raise_clause_at_expansion(
    throw_op: ClauseOp,
) -> None:
    """Shape of blind A: a throw in a Raise clause is refused at expansion time (previously the check was silently skipped, the Raise continuation ran, and 42 was returned)."""
    refusal = _expansion_refusal(
        """
        (defk subject [] {:pre [] :post [(: % int)]}
          (<- r (handle (raises) (Raise [reason] (throw (ValueError "into-k")))))
          r)
        """
    )
    assert "A Raise clause cannot throw" in refusal
    assert "An Absent clause cannot throw" in _expansion_refusal(
        '(setv h (handle (raises) (Absent [why] (throw (ValueError "into-k")))))'
    )


def test_added_resuming_operation_is_refused_for_an_aliased_raise_when_handle_is_evaluated(
    throw_op: ClauseOp,
) -> None:
    """A throw in a clause aliased so that it is not recognizable as Raise by name is refused by the effect type declaration (NEVER) when the handle is evaluated."""
    with pytest.raises(clause_endings.ClauseEndingError, match=r"Resumption\.NEVER.*throw"):
        _eval_module(
            """
            (import doeff_core_effects._effects [Raise :as Failed])
            (setv h (handle (raises) (Failed [reason] (throw (ValueError "into-k")))))
            """
        )


def test_added_operation_node_is_imported_in_both_expansions(throw_op: ClauseOp) -> None:
    """The expansion for type checking also reads after importing the node from the table's static-nodes (previously it read TransferThrow without importing it).
    The names it reads are `_doeff_` aliases and do not shadow the same names written by the author."""
    source = """
    (defk subject [] {:pre [] :post [(: % int)]}
      (<- r (handle (guarded 1) (Bump [n] (throw (ValueError "boom")))))
      r)
    """
    for static in (False, True):
        tree = _expand(source, static=static)
        assert alias("TransferThrow") in _read_names(tree)
        assert "TransferThrow" not in _read_names(tree)
        assert "TransferThrow" in _doeff_imports(tree)


def _rewrite_resume_throw(args: list[Object], recur: Callable[[Object], Object]) -> Object:
    """(throw exception) -> throw the exception into the continuation and, when the continuation ends, return to the clause (the handler remains) -- treated like resume (requirement of blind A)."""
    return Expression(
        [
            Symbol("yield"),
            Expression([alias_symbol("ResumeThrow"), continuation_symbol(), recur(args[0])]),
        ]
    )


# A pair shaped like the resume and transfer pair: a non-tail throw returns to the clause (returns), and a tail throw is replaced by transfer-throw (tail)
_RESUME_THROW = ClauseOp(
    name="throw",
    ending=Ending.RESUMES,
    arity=1,
    returns=True,
    tail="transfer-throw",
    nodes=("ResumeThrow",),
    static_nodes=("ResumeThrow",),
    rewrite=_rewrite_resume_throw,
)
_TRANSFER_THROW = ClauseOp(
    name="transfer-throw",
    ending=Ending.RESUMES,
    arity=1,
    returns=False,
    tail=None,
    nodes=("TransferThrow",),
    static_nodes=("TransferThrow",),
    rewrite=_rewrite_throw,
)


@pytest.fixture
def resume_throw_ops(monkeypatch: pytest.MonkeyPatch) -> None:
    """Add the row throw (returns to the clause; in tail position replaced by transfer-throw) and transfer-throw to the table (do not fix the readers)."""
    monkeypatch.setattr(
        clause_ops, "CLAUSE_OPS", (*clause_ops.CLAUSE_OPS, _RESUME_THROW, _TRANSFER_THROW)
    )
    # The table of expansion names is closed (names not in the table are refused by home) -- also add the added row's node location to the table
    monkeypatch.setitem(expansion_names._HOMES, "ResumeThrow", "doeff")
    monkeypatch.setitem(expansion_names._HOMES, "TransferThrow", "doeff")


def test_added_returning_operation_marks_the_clause_resumed(resume_throw_ops: None) -> None:
    """The "resumed" mark after an operation that returns to the clause is set by handle.hy, which reads the row's returns -- a row's rewrite need not know about the mark
    (previously the promise to set the mark lived inside resume's rewrite, and a row that did not copy it resumed the continuation but stopped with an error at run time)."""
    mod = _eval_module(
        """
        (defk subject [] {:pre [] :post [(: % int)]}
          (<- r (handle (guarded 1) (Bump [n] (setv answer (throw (ValueError "boom"))) answer)))
          r)
        """
    )
    assert run(mod.subject()) == -1


def test_added_operation_with_a_tail_is_replaced_at_the_tail(resume_throw_ops: None) -> None:
    """A tail throw is replaced by the row's tail (transfer-throw) and leaves no handler frame, while a non-tail throw stays ResumeThrow
    (previously the tail replacement lived directly in handle.hy under resume's name, so the tail of an operation added to the table was not replaced)."""
    tail = """
    (defk subject [] {:pre [] :post [(: % int)]}
      (<- r (handle (guarded 1) (Bump [n] (throw (ValueError "boom")))))
      r)
    """
    inner = """
    (defk subject [] {:pre [] :post [(: % int)]}
      (<- r (handle (guarded 1) (Bump [n] (setv answer (throw (ValueError "boom"))) answer)))
      r)
    """
    for static in (False, True):
        tail_names = _read_names(_expand(tail, static=static))
        inner_names = _read_names(_expand(inner, static=static))
        assert alias("TransferThrow") in tail_names
        assert alias("ResumeThrow") not in tail_names
        assert alias("ResumeThrow") in inner_names
        assert alias("TransferThrow") not in inner_names
    assert run(_eval_module(tail).subject()) == -1
    assert "A Raise clause cannot throw" in _expansion_refusal(
        '(setv h (handle (raises) (Raise [reason] (throw (ValueError "into-k")))))'
    )


def test_a_removed_operation_is_no_longer_an_operation_anywhere(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Removing a row from the table (retiring pass) makes none of the clause-termination check, the set of operations the clause uses, or the rewrite treat that name as an operation."""
    monkeypatch.setattr(
        clause_ops, "CLAUSE_OPS", tuple(op for op in clause_ops.CLAUSE_OPS if op.name != "pass")
    )
    form = hy.read("(pass)")
    assert not handle._terminates(form)
    assert clause_endings.clause_ops([form]) == frozenset()
    assert handle._rewrite_ops(form) == form
    assert "missing resume" in _expansion_refusal("(setv h (handle (raises) (Bump [n] (pass))))")


# ---------------------------------------------------------------------------
# Every row of the table is checked according to how the row ends
# ---------------------------------------------------------------------------


def _written(op: ClauseOp) -> str:
    """The table row's operation written in a clause (a raise with no rewrite is a single exception)."""
    if op.arity is None:
        return f'({op.name} (ValueError "x"))'
    return "(" + " ".join([op.name, *["0"] * op.arity]) + ")"


_OPS = {op.name: op for op in clause_ops.CLAUSE_OPS}


@pytest.mark.parametrize("op", _OPS.values(), ids=_OPS.keys())
def test_every_operation_in_the_table_is_checked_by_its_ending(op: ClauseOp) -> None:
    form = hy.read(_written(op))
    assert handle._terminates(form)
    assert clause_endings.clause_ops([form]) == frozenset([op.name])
    raise_clause = _expansion_refusal(f"(setv h (handle (raises) (Raise [reason] {_written(op)})))")
    absent_clause = _expansion_refusal(f"(setv h (handle (raises) (Absent [why] {_written(op)})))")
    ordinary_clause = _expansion_refusal(f"(setv h (handle (raises) (Bump [n] {_written(op)})))")
    if op.ending is Ending.RESUMES:
        assert f"A Raise clause cannot {op.name}" in raise_clause
        assert f"An Absent clause cannot {op.name}" in absent_clause
    else:
        assert raise_clause == absent_clause == ""
    if op.ending is Ending.FINISHES:
        assert ":finish-reason" in ordinary_clause
    else:
        assert ordinary_clause == ""


def test_table_names_are_unique() -> None:
    names = [op.name for op in clause_ops.CLAUSE_OPS]
    assert len(names) == len(set(names))


@pytest.mark.parametrize("op", _OPS.values(), ids=_OPS.keys())
def test_table_rows_are_consistent(op: ClauseOp) -> None:
    """The tail replacement target is a row with the same number of arguments that does not return to the clause. A row with no rewrite does not return to the clause and is not replaced in tail position either."""
    if op.tail is not None:
        target = clause_ops.named_op(op.tail)
        assert target is not None
        assert target.arity == op.arity
        assert not target.returns
    if op.rewrite is None:
        assert not op.returns
        assert op.tail is None


# ---------------------------------------------------------------------------
# Names written in readers that are not in the table are not treated as clause operations
# ---------------------------------------------------------------------------

_PACKAGE = Path(doeff_hy.__file__).parent
_READERS = tuple(_PACKAGE / name for name in ("handle.hy", "_clause_endings.hy"))
_NAME = re.compile(r"[a-z][a-z0-9_!?-]*")


def _written_names(path: Path) -> frozenset[str]:
    """Spellings that can be a head name, written in source as a string or quoted symbol -- candidates for places that hold an operation name without going through the table."""
    if path.suffix == ".py":
        return frozenset(
            node.value
            for node in ast.walk(ast.parse(path.read_text(encoding="utf-8")))
            if isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and _NAME.fullmatch(node.value)
        )
    found: set[str] = set()

    def walk(form: object) -> None:
        match form:
            case String() if _NAME.fullmatch(str(form)):
                found.add(str(form))
            case Expression() if (
                len(form) == 2 and form[0] == Symbol("quote") and isinstance(form[1], Symbol)
            ):
                found.add(str(form[1]))
            case Sequence():
                for child in form:
                    walk(child)

    for form in hy.read_many(path.read_text(encoding="utf-8"), filename=str(path)):
        walk(form)
    return frozenset(found)


_CANDIDATES = sorted(frozenset().union(*(_written_names(path) for path in _READERS)))

# Spellings the same as table names but unrelated to clause operations (module names, spellings):
# - "raise" in macros.hy: a list of Python statement heads (distinguishing forms that cannot be placed in expression position -- unrelated to handler clauses)
_UNRELATED_SPELLINGS = frozenset({("macros.hy", "raise")})


def test_no_module_writes_an_operation_name_outside_the_table() -> None:
    """No module of doeff_hy writes an operation name outside the table (clause_ops.hy) as a string or quoted symbol. Readers that copied the set of names
    (the "use the continuation twice" check of blind B and the form of blind A that writes the tail replacement by name) passed other checks without following additions to or retirements from the table."""
    names = {op.name for op in clause_ops.CLAUSE_OPS}
    written = {
        (path.name, name)
        for path in sorted([*_PACKAGE.glob("*.hy"), *_PACKAGE.glob("*.py")])
        if path.name != "_clause_ops.hy"
        for name in _written_names(path) & names
    }
    assert written - _UNRELATED_SPELLINGS == set()
    assert written >= _UNRELATED_SPELLINGS, "a spelling excluded with a reason is no longer written -- remove the row"


def test_readers_name_operations_only_through_the_table() -> None:
    """Of the spellings written in handle.hy and clause_endings.hy, names not in the table are not treated as clause operations by any reader, and table names are
    treated according to the table row (writing "throw" directly into `_terminates` / `_rewrite-ops` as in the blind A diff goes red here)."""
    assert {"if", "cond", "quote"} <= set(_CANDIDATES), "the way spellings are picked up does not pick up the readers' spellings"
    for name in _CANDIDATES:
        op = clause_ops.named_op(name)
        for count in (0, 1, 2):
            form = Expression([Symbol(name), *[Symbol("x")] * count])
            assert handle._terminates(form) == (op is not None), (name, count)
            assert clause_endings.clause_ops([form]) == (
                frozenset([name]) if op else frozenset()
            ), (name, count)
            rewrites = op is not None and op.rewrite is not None and op.arity == count
            for view in (contextlib.nullcontext(), static_view()):
                with view:
                    rewritten = handle._rewrite_ops(form)
                assert (rewritten != form) == rewrites, (name, count)
