"""The five inference gaps of agora-redesign #837, each with a counter-example.

Found while writing doeff-cluster's "is the job closed under the production
foundation" check (#833, foundation_check.hy), whose fixture had to fall back to
``defn`` builders and two-form handler clauses:

1. handlers a Program installs itself (``with-handlers`` / ``with-handler`` /
   ``handle``) were not subtracted — the body was a carried Program;
2. an env builder was read only as a plain function returning a list literal,
   not as ``defk`` / ``deff`` (whose contract wraps the returned value);
3. Python handler factories built around an object's method
   (``handler(runtime.handle)`` — doeff-time's clocks) were unknown, and an
   unreadable factory had no way to declare what it handles;
4. a foundation taken as a parameter (``(defk job [foundation] …)``) could not be
   followed;
5. a ``defhandler`` value whose only clause is ``(resume v)`` compiles to a lambda
   and was "not found in source".
"""

import pytest
from doeff_effect_analyzer.cli import main
from doeff_effect_analyzer.handler_effects import (
    Basis,
    HandlerEffects,
    analyze_env,
    analyze_handler,
    check_coverage,
    residual,
)
from doeff_effect_analyzer.program_effects import analyze_program

pytest.importorskip("hy")
pytest.importorskip("doeff_hy")
pytest.importorskip("doeff_time")

EFFECTS_PY = """\
from dataclasses import dataclass

from doeff_vm import EffectBase


class Ping(EffectBase):
    pass


class Raw(EffectBase):
    pass


class Tick(EffectBase):
    pass


@dataclass(frozen=True)
class Nap(EffectBase):
    seconds: float


class Unseen(EffectBase):
    pass
"""

HANDLERS_HY = """\
(require doeff-hy.macros [defhandler <-])
(import doeff_core_effects.effects [Ask])
(import {pkg}.effects [Ping Raw Tick Nap])

(defhandler translate
  {:tags {:context "analyzer-test" :role "protocol"}}
  (Ping [] (<- r (Raw)) (resume r)))

;; The only clause is (resume v): defhandler compiles the dispatcher to a lambda.
(defhandler raw-world
  {:tags {:context "analyzer-test" :role "foundation"}}
  (Raw [] (resume 1)))

(defhandler ticker
  {:tags {:context "analyzer-test" :role "foundation"}}
  (Tick [] (resume None)))

(defhandler nap-clock
  {:tags {:context "analyzer-test" :role "foundation"}}
  (Nap [seconds] (<- (Tick)) (resume None)))

(defhandler fixed-settings [value]
  {:tags {:context "analyzer-test" :role "foundation"}}
  (Ask [key] (resume value)))
"""

RUNTIMES_PY = """\
from doeff import Pass, Transfer, do
from doeff import handler as _program_handler

from {pkg}.effects import Nap, Tick, Unseen


class TickRuntime:
    def __init__(self, step):
        self._step = step
        self._handler = self.handle  # the doeff_time.sim_time_handler shape

    @do
    def handle(self, effect, k):
        if isinstance(effect, Tick):
            yield self._advance()
            return (yield Transfer(k, None))
        yield Pass(effect, k)

    @do
    def _advance(self):
        yield Nap(self._step)


def method_tick_handler(step=1.0):
    runtime = TickRuntime(step)
    return _program_handler(runtime.handle)


def attribute_tick_handler(step=1.0):
    runtime = TickRuntime(step)
    return _program_handler(runtime._handler)


_TABLE = {Unseen: None}


def _table_dispatch(effect, k):
    if type(effect) not in _TABLE:
        return (yield Pass(effect, k))
    return (yield Transfer(k, _TABLE[type(effect)]))


def table_handler():
    return _program_handler(do(_table_dispatch))


def declared_table_handler():
    return _program_handler(do(_table_dispatch))


declared_table_handler.__doeff_handles__ = (Unseen,)
declared_table_handler.__doeff_effects__ = ()


def half_declared_table_handler():
    return _program_handler(do(_table_dispatch))


half_declared_table_handler.__doeff_handles__ = (Unseen,)


def mislabelled_tick_handler(step=1.0):
    runtime = TickRuntime(step)
    return _program_handler(runtime.handle)


mislabelled_tick_handler.__doeff_handles__ = (Unseen,)
mislabelled_tick_handler.__doeff_effects__ = ()
"""

ENVS_HY = """\
(require doeff-hy.macros [defk deff <- val])
(import doeff [DoExpr with-handlers])
(import doeff_core_effects.handlers [state])
(import doeff_core_effects.scheduler [scheduled])
(import doeff_time [sync-time-handler])
(import {pkg}.handlers [translate raw-world ticker nap-clock fixed-settings])
(import {pkg}.runtimes [table-handler])

(defk translation-handlers []
  {:pre [] :post [(: % list)] :tags {:context "analyzer-test" :role "foundation"}}
  [translate])

(defk production-handlers []
  {:pre [] :post [(: % list)] :tags {:context "analyzer-test" :role "foundation"}}
  (val clock (sync-time-handler))
  [(state) clock (fixed-settings "1") raw-world])

(defk forgetful-handlers []
  {:pre [] :post [(: % list)] :tags {:context "analyzer-test" :role "foundation"}}
  [(state) (fixed-settings "1") raw-world])

(deff plain-handlers []
  {:pre [] :post [(: % list)] :tags {:context "analyzer-test" :role "foundation"}}
  [(state) (sync-time-handler) (fixed-settings "1") raw-world])

(defk layered-handlers []
  {:pre [] :post [(: % list)] :tags {:context "analyzer-test" :role "foundation"}}
  (<- base list (translation-handlers))
  [#* base ticker])

(defk opaque-handlers []
  {:pre [] :post [(: % list)] :tags {:context "analyzer-test" :role "foundation"}}
  [(table-handler)])

(defk no-handlers []
  {:pre [] :post [(: % list)] :tags {:context "analyzer-test" :role "foundation"}}
  [])

(deff production-foundation [program]
  {:pre [(: program DoExpr)] :post [(: % DoExpr)] :tags {:context "analyzer-test" :role "foundation"}}
  (with-handlers [(state) (sync-time-handler) (fixed-settings "1") raw-world] program))

;; The doeff-cluster foundation shape (#833, e69d08a2): a defk that runs the body it is
;; given under its own handlers and the scheduler.
(defk scheduled-foundation [program]
  {:pre [(: program DoExpr)] :post [(: % int)] :tags {:context "analyzer-test" :role "foundation"}}
  (<- r (scheduled (with-handlers [(state) (sync-time-handler) (fixed-settings "1") raw-world] program)))
  r)
"""

PROGRAMS_HY = """\
(require doeff-hy.macros [defk <-])
(require doeff-hy.handle [with-handler handle])
(import collections.abc [Callable])
(import doeff [with-handlers])
(import doeff_core_effects.effects [Ask])
(import doeff_core_effects.scheduler [Spawn Wait])
(import doeff_time [Delay])
(import {pkg}.effects [Ping Tick Nap])
(import {pkg}.handlers [translate ticker nap-clock])
(import {pkg}.runtimes [table-handler])
(import {pkg}.envs [production-handlers])

(defk child []
  {:pre [] :post [(: % int)] :tags {:context "analyzer-test" :role "program"}}
  (<- a (Ping))
  a)

(defk business []
  {:pre [] :post [(: % int)] :tags {:context "analyzer-test" :role "program"}}
  (<- a (Ping))
  (<- (Delay 1.0))
  (<- b (Ask "ROUNDS"))
  (<- t (Spawn (child)))
  (<- c (Wait t))
  (+ a c))

(defk napping []
  {:pre [] :post [(: % int)] :tags {:context "analyzer-test" :role "program"}}
  (<- (Nap 1.0))
  (<- (Tick))
  1)

(defk translated []
  {:pre [] :post [(: % int)] :tags {:context "analyzer-test" :role "program"}}
  (<- r (with-handlers [translate] (business)))
  r)

(defk clock-outside []
  {:pre [] :post [(: % int)] :tags {:context "analyzer-test" :role "program"}}
  (<- r (with-handlers [ticker nap-clock] (napping)))
  r)

(defk clock-inside []
  {:pre [] :post [(: % int)] :tags {:context "analyzer-test" :role "program"}}
  (<- r (with-handlers [nap-clock ticker] (napping)))
  r)

(defk macro-scoped []
  {:pre [] :post [(: % int)] :tags {:context "analyzer-test" :role "program"}}
  (<- r (with-handler [ticker nap-clock] (napping)))
  r)

(defk inline-scoped []
  {:pre [] :post [(: % int)] :tags {:context "analyzer-test" :role "program"}}
  (<- r (handle (napping) (Nap [seconds] (resume None))))
  r)

(defk inline-performing []
  {:pre [] :post [(: % int)] :tags {:context "analyzer-test" :role "program"}}
  (<- r (handle (napping) (Tick [] (<- (Ping)) (resume None))))
  r)

(defk unreadable-scoped []
  {:pre [] :post [(: % int)] :tags {:context "analyzer-test" :role "program"}}
  (<- r (with-handlers [(table-handler)] (napping)))
  r)

(defk looping [n]
  {:pre [(: n int)] :post [(: % int)] :tags {:context "analyzer-test" :role "program"}}
  (<- (Tick))
  (<- r (with-handlers [ticker] (looping (- n 1))))
  r)

(defk recursive-inline []
  {:pre [] :post [(: % int)] :tags {:context "analyzer-test" :role "program"}}
  (<- r (handle (napping) (Tick [] (<- (recursive-inline)) (resume None))))
  r)

(defk job [foundation]
  {:pre [(: foundation Callable)] :post [(: % int)] :tags {:context "analyzer-test" :role "entry"}}
  (<- base list (foundation))
  (<- r (with-handlers base (translated)))
  r)

(defk launcher []
  {:pre [] :post [(: % int)] :tags {:context "analyzer-test" :role "entry"}}
  (<- r (job production-handlers))
  r)

(defk wrapped-job [foundation]
  {:pre [(: foundation Callable)] :post [(: % int)] :tags {:context "analyzer-test" :role "entry"}}
  (<- r (foundation (translated)))
  r)
"""


OUTCOMES_HY = """\
(require doeff-hy.macros [defk do! <- absent-as])
(import doeff_core_effects.effects [Absent])
(import {pkg}.effects [Tick Ping])

(defk missing []
  {:pre [] :post [(: % int)] :tags {:context "analyzer-test" :role "program"}}
  (<- (Tick))
  (<- (Absent "no row"))
  1)

;; absent-as wraps its body in direct_bind(token, body): the body still runs.
(defk defaulted []
  {:pre [] :post [(: % int)] :tags {:context "analyzer-test" :role "program"}}
  (<- r (absent-as 0 (missing)))
  r)

;; Each <- written inside the do! is wrapped too: open_bind(direct_bind(token, e)).
(defk defaulted-inline []
  {:pre [] :post [(: % int)] :tags {:context "analyzer-test" :role "program"}}
  (<- r (absent-as 0 (do! (<- (Ping)) (<- n (missing)) n)))
  r)
"""


@pytest.fixture
def pkg(make_package) -> str:
    return make_package(
        {
            "effects.py": EFFECTS_PY,
            "handlers.hy": HANDLERS_HY,
            "runtimes.py": RUNTIMES_PY,
            "envs.hy": ENVS_HY,
            "programs.hy": PROGRAMS_HY,
            "outcomes.hy": OUTCOMES_HY,
        }
    )


def _short(names) -> set[str]:
    return {name.rsplit(".", 1)[-1] for name in names}


def _types(types_) -> set[str]:
    return {t.__name__ for t in types_}


def _spawn(carrier) -> bool:
    return getattr(carrier, "__name__", None) == "Spawn"


def _scheduler() -> HandlerEffects:
    return analyze_handler("doeff_core_effects.scheduler:scheduled", name="scheduled")


# --------------------------------------------------------------------------- 1. handlers installed inside


def test_handlers_installed_inside_are_subtracted(pkg: str) -> None:
    report = analyze_program(f"{pkg}.programs:translated")

    [scope] = report.handled
    assert [h.name for h in scope.handlers] == ["translate"]
    assert "Ping" in _short(scope.program.effect_names)
    # Ping is answered inside; what translate performs for it (Raw) leaves instead.
    assert "Ping" not in _short(report.effect_names)
    assert {"Raw", "DelayEffect", "Ask", "Spawn", "Wait"} <= _short(report.effect_names)
    assert report.carried == ()  # the body is not a carried Program any more


def test_the_handler_list_runs_from_the_innermost_out(pkg: str) -> None:
    # [outer inner]: nap-clock (inner) answers Nap by performing Tick, which the
    # outer ticker answers.  Reversed, nap-clock is outside ticker and its Tick leaves.
    outside = analyze_program(f"{pkg}.programs:clock_outside")
    inside = analyze_program(f"{pkg}.programs:clock_inside")

    assert outside.effect_types == frozenset()
    assert _types(inside.effect_types) == {"Tick"}
    [escape] = inside.residual.escapes
    assert escape.by == "nap_clock"


def test_with_handler_and_handle_expansions_are_read(pkg: str) -> None:
    macro = analyze_program(f"{pkg}.programs:macro_scoped")
    inline = analyze_program(f"{pkg}.programs:inline_scoped")
    performing = analyze_program(f"{pkg}.programs:inline_performing")

    assert macro.effect_types == frozenset()  # ticker(nap_clock(napping()))
    assert [len(s.handlers) for s in macro.handled] == [1]
    assert _types(inline.effect_types) == {"Tick"}  # handle answers Nap only
    [inline_handler] = inline.handled[0].handlers
    assert inline_handler.basis is Basis.CLAUSES
    # handle's clause performs Ping (a local def, not a lambda): Ping leaves, Tick does not.
    assert _types(performing.effect_types) == {"Nap", "Ping"}


def test_an_unreadable_handler_inside_is_not_counted_as_handling(pkg: str) -> None:
    report = analyze_program(f"{pkg}.programs:unreadable_scoped")

    assert _types(report.effect_types) == {"Nap", "Tick"}  # passes everything through
    assert report.residual.unknown_handlers == ("table_handler()",)
    coverage = check_coverage(report, analyze_env(f"{pkg}.envs:production_handlers"))
    assert not coverage.complete
    assert "table_handler()" in coverage.unknown_handlers


def test_residual_through_an_env_and_recursion_through_a_scope(pkg: str) -> None:
    report = analyze_program(f"{pkg}.programs:translated")
    env = [_scheduler(), *analyze_env(f"{pkg}.envs:production_handlers")]

    assert residual(report, env, include=_spawn).escapes == ()
    left = residual(report, analyze_env(f"{pkg}.envs:forgetful_handlers"), include=_spawn)
    assert {"DelayEffect", "Spawn", "Wait"} <= _types(left.effect_types)

    looping = analyze_program(f"{pkg}.programs:looping")  # terminates
    assert _types(looping.effect_types) == {"Tick"}
    # A `handle` clause that runs the Program installing it again: read once, then
    # the inner visit is unread (named, so coverage stays incomplete) — not a hang.
    recursive = analyze_program(f"{pkg}.programs:recursive_inline")
    assert "Nap" in _types(recursive.effect_types)
    assert recursive.residual.unknown_handlers != ()


# --------------------------------------------------------------------------- 2. defk / deff builders


def test_defk_and_deff_builders_are_read(pkg: str) -> None:
    # production-handlers binds the clock with val first; plain-handlers is a deff.
    for builder in ("production_handlers", "plain_handlers"):
        env = analyze_env(f"{pkg}.envs:{builder}")
        assert [h.name for h in env] == [
            "state()",
            "sync_time_handler()",
            "fixed_settings('1')",
            "raw_world",
        ], builder
        assert all(h.known for h in env), [h.to_dict() for h in env]
        assert "DelayEffect" in _types(env[1].handled)


def test_a_builder_spreading_another_builder(pkg: str) -> None:
    env = analyze_env(f"{pkg}.envs:layered_handlers")

    assert [h.name for h in env] == ["translate", "ticker"]
    assert all(h.known for h in env)


def test_a_defk_builder_that_forgets_the_clock_leaves_a_gap(pkg: str) -> None:
    program = analyze_program(f"{pkg}.programs:translated")
    env = [_scheduler(), *analyze_env(f"{pkg}.envs:forgetful_handlers")]

    coverage = check_coverage(program, env, include=_spawn)

    assert [gap.effect.__name__ for gap in coverage.gaps] == ["DelayEffect"]


# --------------------------------------------------------------------------- 3. Python handler factories


def test_doeff_time_clock_factories_are_read_from_their_clauses() -> None:
    for factory in ("sync_time_handler", "async_time_handler", "sim_time_handler"):
        handler = analyze_handler(f"doeff_time:{factory}")
        assert handler.basis is Basis.CLAUSES, handler.to_dict()
        assert {"DelayEffect", "GetTimeEffect", "ScheduleAtEffect"} <= _types(handler.handled)


def test_a_factory_around_an_object_method_and_an_init_attribute(pkg: str) -> None:
    for factory in ("method_tick_handler", "attribute_tick_handler"):
        handler = analyze_handler(f"{pkg}.runtimes:{factory}")
        [clause] = handler.clauses
        assert clause.handles.__name__ == "Tick"
        # self._advance() is followed as the instance's method.
        assert _short(clause.emits.effect_names) == {"Nap"}, factory


def test_a_factory_whose_clauses_cannot_be_read_uses_its_declaration(pkg: str) -> None:
    unread = analyze_handler(f"{pkg}.runtimes:table_handler")
    declared = analyze_handler(f"{pkg}.runtimes:declared_table_handler")
    half = analyze_handler(f"{pkg}.runtimes:half_declared_table_handler")

    assert unread.basis is Basis.UNREAD
    assert not unread.known
    assert declared.basis is Basis.DECLARED
    assert _types(declared.handled) == {"Unseen"}
    assert declared.clauses[0].emits.effect_names == []
    # Declaring what it answers without what it performs is not enough.
    assert half.basis is Basis.UNREAD
    assert any("__doeff_effects__" in u.reason for u in half.unresolved)


def test_a_declaration_that_disagrees_with_the_clauses_is_reported(pkg: str) -> None:
    handler = analyze_handler(f"{pkg}.runtimes:mislabelled_tick_handler")

    assert handler.basis is Basis.CLAUSES
    assert _types(handler.handled) == {"Tick"}
    assert any("differs from the clauses" in u.reason for u in handler.unresolved)


# --------------------------------------------------------------------------- 4. a foundation taken as a parameter


def test_an_unbound_foundation_is_unresolved_not_closed(pkg: str) -> None:
    report = analyze_program(f"{pkg}.programs:job")

    assert any(u.text == "foundation" for u in report.unresolved)
    assert report.residual.unknown_handlers == ("foundation()",)
    assert not check_coverage(report, [_scheduler()], include=_spawn).complete
    # (foundation (translated)) unbound: the body is not followed, so not complete either.
    wrapped = check_coverage(
        analyze_program(f"{pkg}.programs:wrapped_job"), [_scheduler()], include=_spawn
    )
    assert wrapped.gaps == ()
    assert [u.text for u in wrapped.unresolved] == ["foundation"]
    assert not wrapped.complete


def test_a_bound_foundation_is_folded_in(pkg: str) -> None:
    from importlib import import_module

    envs = import_module(f"{pkg}.envs")
    closed = analyze_program(
        f"{pkg}.programs:job", bindings={"foundation": envs.production_handlers}
    )
    forgetful = analyze_program(
        f"{pkg}.programs:job", bindings={"foundation": envs.forgetful_handlers}
    )

    assert closed.unresolved == ()
    coverage = check_coverage(closed, [_scheduler()], include=_spawn)
    assert coverage.complete, (coverage.gaps, coverage.unknown_handlers)
    assert [
        g.effect.__name__ for g in check_coverage(forgetful, [_scheduler()], include=_spawn).gaps
    ] == ["DelayEffect"]
    with pytest.raises(ValueError, match="no parameter"):
        analyze_program(f"{pkg}.programs:job", bindings={"fundation": envs.production_handlers})


def test_the_callers_argument_binds_the_foundation(pkg: str) -> None:
    # (job production-handlers) inside launcher: followed with foundation bound.
    report = analyze_program(f"{pkg}.programs:launcher")

    assert report.unresolved == ()
    assert check_coverage(report, [_scheduler()], include=_spawn).complete


def test_a_foundation_that_wraps_the_program_it_is_given(pkg: str) -> None:
    from importlib import import_module

    envs = import_module(f"{pkg}.envs")
    handlers = import_module(f"{pkg}.handlers")
    wrapped = analyze_program(
        f"{pkg}.programs:wrapped_job", bindings={"foundation": envs.production_foundation}
    )
    installer = analyze_program(
        f"{pkg}.programs:wrapped_job", bindings={"foundation": handlers.raw_world}
    )

    # (foundation (translated)) with a function that returns (with-handlers [...] program):
    # the Program is read inside the foundation's handlers, not also carried.
    assert check_coverage(wrapped, [_scheduler()], include=_spawn).complete
    assert wrapped.carried == ()
    # ... and with a handler value: only raw-world answers.
    assert "Raw" not in _types(installer.effect_types)
    assert {"DelayEffect", "Ask"} <= _types(installer.effect_types)


def test_a_foundation_that_runs_the_program_under_its_handlers_and_the_scheduler(
    pkg: str,
) -> None:
    from importlib import import_module

    envs = import_module(f"{pkg}.envs")
    report = analyze_program(
        f"{pkg}.programs:wrapped_job", bindings={"foundation": envs.scheduled_foundation}
    )

    # scheduled(...) is read as the scheduler's handler around the with-handlers scope;
    # nothing is left for an outer env, and the body is not carried.
    coverage = check_coverage(report, [], include=_spawn)
    assert coverage.complete, (coverage.gaps, coverage.unknown_handlers, coverage.unresolved)
    assert report.carried == ()
    [outer] = report.handled
    assert [h.name for h in outer.handlers] == ["scheduled"]


def test_cli_binds_a_foundation(pkg: str, capsys: pytest.CaptureFixture[str]) -> None:
    target = f"{pkg}.programs:job"
    common = [
        "--env",
        f"{pkg}.envs:no_handlers",
        "--outer",
        "doeff_core_effects.scheduler:scheduled",
    ]
    fold = ["--fold-carried", "Spawn"]

    assert main(["coverage", target, *common, *fold]) == 1
    bind = ["--bind", f"foundation={pkg}.envs:production_handlers"]
    assert main(["coverage", target, *common, *fold, *bind]) == 0
    assert main(["program", target, *bind]) == 0
    assert "under [state()" in capsys.readouterr().out


# --------------------------------------------------------------------------- 5. a lone (resume v) clause


def test_a_handler_value_whose_only_clause_is_a_resume(pkg: str) -> None:
    for name in ("raw_world", "ticker"):
        handler = analyze_handler(f"{pkg}.handlers:{name}")
        assert handler.basis is Basis.CLAUSES, handler.to_dict()
        assert len(handler.clauses) == 1


def test_a_lone_resume_handler_closes_the_foundation(pkg: str) -> None:
    program = analyze_program(f"{pkg}.programs:translated")
    env = [_scheduler(), *analyze_env(f"{pkg}.envs:production_handlers")]

    coverage = check_coverage(program, env, include=_spawn)

    assert coverage.gaps == ()
    assert coverage.unknown_handlers == ()


# --------------------------------------------------------------------------- left after the five
# Found after the five were closed (agora-redesign #837, 2026-09-28): a reading that
# drops what it cannot follow answers "closed" for a Program that is not.


def test_the_body_of_absent_as_still_runs(pkg: str) -> None:
    # absent-as answers Absent only; the body's Tick leaves (it used to vanish, and
    # coverage answered complete).
    report = analyze_program(f"{pkg}.outcomes:defaulted")

    assert _types(report.effect_types) == {"Tick"}
    [scope] = report.handled
    assert _types(scope.program.effect_types) == {"Tick", "Absent"}
    assert not check_coverage(report, []).complete
    assert check_coverage(report, [analyze_handler(f"{pkg}.handlers:ticker")]).complete
