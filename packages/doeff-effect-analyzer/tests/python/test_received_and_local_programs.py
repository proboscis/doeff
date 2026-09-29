"""Programs a handler receives in an effect, and functions written where they are passed.

Regression target (agora-redesign #1432, from #1265): once the reader followed a
foundation's ports, three places inside the doeff handlers were left it could not
follow — ``try_handler`` runs the Program ``Try`` carried (``prog = effect.program``,
rewrapped in the inner handlers, run by the nested generator ``attempt``) and
``run-in-transaction`` calls the ``begin`` / ``commit`` / ``rollback`` functions the SQL
handler writes as lambdas where it calls it.
"""

import pytest
from doeff_effect_analyzer.handler_effects import analyze_handler
from doeff_effect_analyzer.program_effects import analyze_program

pytest.importorskip("hy")
pytest.importorskip("doeff_hy")

EFFECTS_PY = """\
from dataclasses import dataclass

from doeff_vm import EffectBase


@dataclass(frozen=True)
class ReadBoard(EffectBase):
    prefix: str


@dataclass(frozen=True)
class Begin(EffectBase):
    database: str


@dataclass(frozen=True)
class Commit(EffectBase):
    database: str


@dataclass(frozen=True)
class Nap(EffectBase):
    seconds: float


class Carry(EffectBase):
    def __init__(self, program, wrappers):
        super().__init__()
        self.program = program
        self.wrappers = wrappers
"""

# The shape of ``doeff_core_effects.handlers._try_handler``.
HANDLERS_PY = """\
from doeff import do
from doeff.program import handler as _program_handler
from doeff_vm import Pass, Resume

from {pkg}.effects import Carry, Nap


@do
def _carry(effect, k):
    if isinstance(effect, Carry):
        prog = effect.program
        for wrap in effect.wrappers:
            prog = wrap(prog)
        prog = carry(prog)

        @do
        def attempt():
            value = yield prog
            yield Nap(0.0)
            return value

        return (yield Resume(k, (yield attempt())))
    yield Pass(effect, k)


carry = _program_handler(_carry)


@do
def _mapped(effect, k):
    if isinstance(effect, Carry):
        prog = effect.program
        prog = prog.map(str)
        return (yield Resume(k, (yield prog)))
    yield Pass(effect, k)


mapped = _program_handler(_mapped)
"""

# The shape of ``run-in-transaction`` and the SQL handlers that call it.
FOUNDATIONS_HY = """\
(require doeff-hy.macros [defk <-])
(import collections.abc [Callable])
(import doeff [Program])
(import {pkg}.effects [ReadBoard Begin Commit])

(defk serve-reads []
  {:pre [] :post [(: % dict)]}
  (<- rows dict (ReadBoard "turn/"))
  rows)

(defk begin-on [database]
  {:pre [(: database str)] :post [(: % "None")]}
  (<- (Begin database))
  None)

(defk in-transaction [program begin commit]
  {:pre [(: program Program) (: begin Callable) (: commit Callable)] :post [(: % "the program's answer")]}
  (<- (begin))
  (<- value program)
  (<- (commit))
  value)

(defk transaction-process [database]
  {:pre [(: database str)] :post [(: % "the program's answer")]}
  (<- answer (in-transaction (serve-reads) (fn [] (begin-on database)) (fn [] (Commit database))))
  answer)

(defk data-process [database]
  {:pre [(: database str)] :post [(: % "the program's answer")]}
  (<- answer (in-transaction (serve-reads) (fn [] "not-a-program") (fn [] (Commit database))))
  answer)
"""


@pytest.fixture
def pkg(make_package) -> str:
    return make_package(
        {"effects.py": EFFECTS_PY, "handlers.py": HANDLERS_PY, "foundations.hy": FOUNDATIONS_HY}
    )


def _short(names) -> set[str]:
    return {name.rsplit(".", 1)[-1] for name in names}


def _clause(pkg: str, handler: str):
    [clause] = analyze_handler(f"{pkg}.handlers:{handler}").clauses
    return clause.emits


def test_a_handler_running_the_program_it_received_is_followed(pkg: str) -> None:
    # effect.program — rewrapped in handlers, run by a nested generator — is the
    # Program Carry carried (read where Carry was performed): nothing is left unread,
    # and what the nested generator performs itself (Nap) is read.
    emits = _clause(pkg, "carry")

    assert emits.unresolved == ()
    assert _short(emits.effect_names) == {"Nap"}


def test_a_received_program_rebound_to_something_else_stays_unresolved(pkg: str) -> None:
    # The counter-case: ``prog.map(str)`` is not a handler put around ``prog``; what the
    # local holds afterwards is not the received Program.
    emits = _clause(pkg, "mapped")

    assert [(item.reason, item.text) for item in emits.unresolved] == [
        ("yielded a value that is not a call", "prog")
    ]


def test_functions_passed_to_the_callee_are_read_where_they_were_written(pkg: str) -> None:
    report = analyze_program(f"{pkg}.foundations:transaction_process")

    assert report.unresolved == ()
    assert _short(report.effect_names) == {"ReadBoard", "Begin", "Commit"}


def test_a_passed_function_that_returns_data_stays_unresolved(pkg: str) -> None:
    # The counter-case: the function the callee runs hands back a string, not a Program.
    report = analyze_program(f"{pkg}.foundations:data_process")

    assert [(item.reason, item.text) for item in report.unresolved] == [
        ("yielded a value that is not a call", "'not-a-program'")
    ]
    assert _short(report.effect_names) == {"ReadBoard", "Commit"}
