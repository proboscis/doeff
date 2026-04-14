"""Tail-position resume → Transfer optimization (TCO) — #386.

defhandler/handle macros should rewrite tail-position (resume expr) to
(transfer expr) so handler gen frames don't accumulate on the parent segment.

Test levels:
  1. Macro-level: _build_clause output contains Transfer, not Resume
  2. Integration: handler with many Resume-loop effects doesn't accumulate frames
"""
import pytest

import hy  # noqa: F401
import doeff_hy  # noqa: F401 — registers macros/extensions
from hy.models import Expression, Symbol, List as HyList, Integer as HyInt

from doeff_hy.handle import _build_clause


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _clause(*forms):
    """Build a Hy clause Expression from forms."""
    return Expression(list(forms))


def _sym(name):
    return Symbol(name)


def _expr(*forms):
    return Expression(list(forms))


def _list(*forms):
    return HyList(list(forms))


def _body_contains(body, target_sym):
    """Recursively check if body AST contains a Symbol with given name."""
    if isinstance(body, Symbol):
        return str(body) == target_sym
    if isinstance(body, (Expression, HyList)):
        return any(_body_contains(child, target_sym) for child in body)
    return False


# ---------------------------------------------------------------------------
# Macro-level tests: _build_clause should emit Transfer for tail resume
# ---------------------------------------------------------------------------

class TestTailResumeTCO:
    """Tail-position (resume expr) should become (transfer expr) in macro output."""

    def test_simple_tail_resume(self):
        """(MyEffect [x] (print x) (resume None)) → Transfer"""
        clause = _clause(
            _sym("MyEffect"), _list(_sym("x")),
            _expr(_sym("print"), _sym("x")),
            _expr(_sym("resume"), _sym("None")),
        )
        _etype, body = _build_clause(clause)
        assert _body_contains(body, "Transfer"), f"expected Transfer in: {body}"
        assert not _body_contains(body, "Resume"), f"unexpected Resume in: {body}"

    def test_if_both_branches_tail_resume(self):
        """(MyEffect [x] (if (pred x) (resume a) (resume b))) → both Transfer"""
        clause = _clause(
            _sym("MyEffect"), _list(_sym("x")),
            _expr(
                _sym("if"), _expr(_sym("pred"), _sym("x")),
                _expr(_sym("resume"), _sym("a")),
                _expr(_sym("resume"), _sym("b")),
            ),
        )
        _etype, body = _build_clause(clause)
        assert _body_contains(body, "Transfer"), f"expected Transfer in: {body}"
        assert not _body_contains(body, "Resume"), f"unexpected Resume in: {body}"

    def test_cond_all_branches_tail_resume(self):
        """(MyEffect [x] (cond (p1 x) (resume a) (p2 x) (resume b))) → both Transfer"""
        clause = _clause(
            _sym("MyEffect"), _list(_sym("x")),
            _expr(
                _sym("cond"),
                _expr(_sym("p1"), _sym("x")), _expr(_sym("resume"), _sym("a")),
                _expr(_sym("p2"), _sym("x")), _expr(_sym("resume"), _sym("b")),
            ),
        )
        _etype, body = _build_clause(clause)
        assert _body_contains(body, "Transfer"), f"expected Transfer in: {body}"
        assert not _body_contains(body, "Resume"), f"unexpected Resume in: {body}"

    def test_explicit_transfer_unchanged(self):
        """(MyEffect [x] (transfer None)) → still Transfer"""
        clause = _clause(
            _sym("MyEffect"), _list(_sym("x")),
            _expr(_sym("transfer"), _sym("None")),
        )
        _etype, body = _build_clause(clause)
        assert _body_contains(body, "Transfer")
        assert not _body_contains(body, "Resume")


class TestNonTailResumePreserved:
    """Non-tail-position (resume expr) must remain Resume — NOT optimized."""

    def test_resume_with_post_processing(self):
        """(<- result (resume x)) followed by (transfer result) → Resume kept"""
        clause = _clause(
            _sym("MyEffect"), _list(_sym("x")),
            _expr(_sym("<-"), _sym("result"), _expr(_sym("resume"), _sym("x"))),
            _expr(_sym("transfer"), _sym("result")),
        )
        _etype, body = _build_clause(clause)
        # Must have BOTH: Resume (for the bind) and Transfer (for the tail)
        assert _body_contains(body, "Resume"), f"expected Resume in: {body}"
        assert _body_contains(body, "Transfer"), f"expected Transfer in: {body}"

    def test_resume_inside_try_not_optimized(self):
        """(try (resume x) (except ...)) → Resume preserved (need frame for except)"""
        clause = _clause(
            _sym("MyEffect"), _list(_sym("x")),
            _expr(
                _sym("try"),
                _expr(_sym("resume"), _sym("x")),
                _expr(_sym("except"), _list(_sym("e"), _sym("Exception")),
                       _expr(_sym("resume"), _sym("default"))),
            ),
        )
        _etype, body = _build_clause(clause)
        # Inside try → Resume must be preserved
        assert _body_contains(body, "Resume"), f"expected Resume in: {body}"
