"""Tail-position resume → Transfer optimization (TCO) — #386.

defhandler/handle macros should rewrite tail-position (resume expr) to
(transfer expr) so handler gen frames don't accumulate on the parent segment.

Test levels:
  1. Macro-level: _build_clause output contains Transfer, not Resume
  2. Integration: handler with many Resume-loop effects doesn't accumulate frames
"""

import doeff_hy  # noqa: F401 — registers macros/extensions
import hy  # noqa: F401 - activates Hy reader for macro tests
from doeff_hy.handle import _build_clause
from hy.models import Expression, Keyword, Symbol
from hy.models import List as HyList

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
        """(MyEffect [x] (cond (p1 x) (resume a) True (resume b))) → both Transfer

        A cond clause ends with a True branch — a cond whose tests can all be false falls
        through without resuming, which defhandler refuses (ADR-DOE-CORE-EFFECTS-003 R15)."""
        clause = _clause(
            _sym("MyEffect"), _list(_sym("x")),
            _expr(
                _sym("cond"),
                _expr(_sym("p1"), _sym("x")), _expr(_sym("resume"), _sym("a")),
                _sym("True"), _expr(_sym("resume"), _sym("b")),
            ),
        )
        _etype, body = _build_clause(clause)
        assert _body_contains(body, "Transfer"), f"expected Transfer in: {body}"
        assert not _body_contains(body, "Resume"), f"unexpected Resume in: {body}"

    def test_match_all_cases_tail_resume(self):
        """(MyEffect [x] (match x (int) (resume a) _ (resume b))) → both Transfer (agora-redesign #3530)"""
        clause = _clause(
            _sym("MyEffect"), _list(_sym("x")),
            _expr(
                _sym("match"), _sym("x"),
                _expr(_sym("int")), _expr(_sym("resume"), _sym("a")),
                _sym("_"), _expr(_sym("resume"), _sym("b")),
            ),
        )
        _etype, body = _build_clause(clause)
        assert _body_contains(body, "Transfer"), f"expected Transfer in: {body}"
        assert not _body_contains(body, "Resume"), f"unexpected Resume in: {body}"

    def test_match_arm_with_guard_tail_resume(self):
        """An arm's :if guard is kept as written — only its body is rewritten.

        (match x n :if (pos? n) (do (log n) (resume n)) _ (resume b))"""
        clause = _clause(
            _sym("MyEffect"), _list(_sym("x")),
            _expr(
                _sym("match"), _sym("x"),
                _sym("n"), Keyword("if"), _expr(_sym("pos?"), _sym("n")),
                _expr(_sym("do"), _expr(_sym("log"), _sym("n")), _expr(_sym("resume"), _sym("n"))),
                _sym("_"), _expr(_sym("resume"), _sym("b")),
            ),
        )
        _etype, body = _build_clause(clause)
        assert _body_contains(body, "Transfer"), f"expected Transfer in: {body}"
        assert not _body_contains(body, "Resume"), f"unexpected Resume in: {body}"
        assert _body_contains(body, "pos?"), f"the guard must stay: {body}"

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

    def test_match_not_at_the_end_keeps_resume(self):
        """A match that is not the clause's last form is not in tail position: its resume stays."""
        clause = _clause(
            _sym("MyEffect"), _list(_sym("x")),
            _expr(
                _sym("<-"), _sym("r"),
                _expr(_sym("match"), _sym("x"), _sym("_"), _expr(_sym("resume"), _sym("x"))),
            ),
            _expr(_sym("transfer"), _sym("r")),
        )
        _etype, body = _build_clause(clause)
        assert _body_contains(body, "Resume"), f"expected Resume in: {body}"
        assert _body_contains(body, "Transfer"), f"expected Transfer in: {body}"


# ---------------------------------------------------------------------------
# Integration: a match-branch resume does not keep a handler frame per effect
# ---------------------------------------------------------------------------

_ANSWERS_PRELUDE = """
(require doeff-hy.macros [defk defhandler with-handler <- var])
(import doeff [EffectBase run :as doeff-run])
(import dataclasses [dataclass])
(import gc weakref)

(defclass [(dataclass :frozen True)] Ask [EffectBase]
  #^ int value)

(defclass Token [])

;; The answer is a local of the clause, as in the intake reader the leak was found in
;; (agora-redesign #3530): a kept handler frame keeps its answer.
(defhandler tokens
  (Ask [value]
    (match value
      (int) (let [token (Token)] (resume token))
      _ (resume None))))

(defk alive-answers [n]
  {:pre [(: n int)] :post [(: % int)]}
  (var refs #())
  (for [_ (range n)]
    (<- answer (Ask :value 1))
    (:= refs (+ refs #((weakref.ref answer)))))
  (gc.collect)
  (sum (gfor ref refs :if (is-not (ref) None) 1)))

(setv program (with-handler [tokens] (alive-answers 200)))
"""


def test_match_branch_resume_does_not_keep_answers_alive():
    """200 effects answered from a match branch: the handler frames are gone, so the answers are
    freed as the program drops them (only the last answer is still bound). Before #3530 every
    answer was kept by its suspended handler frame (200 alive)."""
    import hy

    namespace: dict[str, object] = {}
    hy.eval(hy.read_many(_ANSWERS_PRELUDE), namespace)
    alive = namespace["doeff_run"](namespace["program"])
    assert alive <= 2, f"{alive} of 200 answers still alive — handler frames kept"
