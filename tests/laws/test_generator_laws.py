"""Equational law tests for the effect algebra generators (G1-G5).

Each test encodes one law from docs/crystallization/algebra-draft.md §3 as an
executable LHS ≡ RHS check against the canonical handlers. Laws are handler
contracts (D18): these tests verify that the canonical doeff handler stack
qualifies as a model of the theory.

Law IDs map to algebra-draft.md. Negative tests (assert_not_equiv) lock in
refutations discovered in the 2026-06-12 adversarial-attack session — they are
executable documentation that a tempting rewrite is ILLEGAL.

S6 (state×continuation sharing) is covered by the existing
tests/effects/test_effect_combinations.py::TestSafeNonRollbackLaw.
"""

import asyncio

import pytest

from doeff import (
    Ask,
    Await,
    CompletePromise,
    CreatePromise,
    Gather,
    Get,
    Listen,
    Local,
    Put,
    Spawn,
    Tell,
    Wait,
    do,
)
from tests._run_helpers import run_with_defaults


# ============================================================================
# Harness
# ============================================================================


def run1(thunk, *, env=None, store=None):
    """Run a fresh program instance under the default handler stack."""
    return run_with_defaults(thunk(), env=env, store=store)


def assert_equiv(lhs_thunk, rhs_thunk, *, env=None, store=None):
    """p ≡ q : same Ok/Err status, same value (or same exception type).

    Thunks (not programs) because generators are one-shot: each side needs a
    fresh program instance under a fresh handler stack.
    """
    lhs = run1(lhs_thunk, env=env, store=store)
    rhs = run1(rhs_thunk, env=env, store=store)
    assert lhs.is_ok() == rhs.is_ok(), f"status differs: LHS={lhs!r} RHS={rhs!r}"
    if lhs.is_ok():
        assert lhs.value == rhs.value, f"LHS={lhs.value!r} RHS={rhs.value!r}"
    else:
        assert type(lhs.error) is type(rhs.error), (
            f"LHS={type(lhs.error)} RHS={type(rhs.error)}"
        )


def assert_not_equiv(lhs_thunk, rhs_thunk, *, env=None, store=None):
    """Refutation lock: the two sides MUST differ (both Ok, different values)."""
    lhs = run1(lhs_thunk, env=env, store=store)
    rhs = run1(rhs_thunk, env=env, store=store)
    assert lhs.is_ok() and rhs.is_ok(), f"expected Ok/Ok: LHS={lhs!r} RHS={rhs!r}"
    assert lhs.value != rhs.value, (
        f"refutation vanished — sides now agree on {lhs.value!r}; "
        "either the law became unconditional (update algebra-draft) or "
        "the test no longer exercises the distinguishing schedule"
    )


# ============================================================================
# G1 Ask (A1-A4)
# ============================================================================


class TestAskLaws:
    def test_a1_ask_duplication(self):
        @do
        def lhs():
            x = yield Ask("k")
            y = yield Ask("k")
            return (x, y)

        @do
        def rhs():
            x = yield Ask("k")
            return (x, x)

        assert_equiv(lhs, rhs, env={"k": 41})

    def test_a2_local_override(self):
        @do
        def lhs():
            return (yield Local({"k": "v"}, _ask_k()))

        @do
        def rhs():
            return "v"

        assert_equiv(lhs, rhs, env={"k": "outer"})

    def test_a3_local_composition(self):
        e1 = {"a": 1, "b": 1}
        e2 = {"b": 2, "c": 2}

        @do
        def probe():
            a = yield Ask("a")
            b = yield Ask("b")
            c = yield Ask("c")
            return (a, b, c)

        @do
        def lhs():
            return (yield Local(e1, Local(e2, probe())))

        @do
        def rhs():
            return (yield Local({**e1, **e2}, probe()))

        assert_equiv(lhs, rhs, env={"a": 0, "b": 0, "c": 0})

    def test_a4_distinct_key_commutativity(self):
        @do
        def lhs():
            x = yield Ask("k1")
            y = yield Ask("k2")
            return (x, y)

        @do
        def rhs():
            y = yield Ask("k2")
            x = yield Ask("k1")
            return (x, y)

        assert_equiv(lhs, rhs, env={"k1": 1, "k2": 2})


@do
def _ask_k():
    return (yield Ask("k"))


# ============================================================================
# G2 Get/Put (S1-S5; S6 covered by TestSafeNonRollbackLaw)
# ============================================================================


class TestStateLaws:
    def test_s1_put_put(self):
        @do
        def lhs():
            yield Put("k", "v")
            yield Put("k", "w")
            return (yield Get("k"))

        @do
        def rhs():
            yield Put("k", "w")
            return (yield Get("k"))

        assert_equiv(lhs, rhs)

    def test_s2_put_get(self):
        @do
        def lhs():
            yield Put("k", "v")
            return (yield Get("k"))

        @do
        def rhs():
            yield Put("k", "v")
            return "v"

        assert_equiv(lhs, rhs)

    def test_s3_get_put(self):
        @do
        def lhs():
            x = yield Get("k")
            yield Put("k", x)
            return (yield Get("k"))

        @do
        def rhs():
            return (yield Get("k"))

        assert_equiv(lhs, rhs, store={"k": "initial"})

    def test_s4_get_get(self):
        @do
        def lhs():
            x = yield Get("k")
            y = yield Get("k")
            return (x, y)

        @do
        def rhs():
            x = yield Get("k")
            return (x, x)

        assert_equiv(lhs, rhs, store={"k": 7})

    def test_s5_distinct_key_commutativity(self):
        @do
        def lhs():
            yield Put("k1", "a")
            yield Put("k2", "b")
            return ((yield Get("k1")), (yield Get("k2")))

        @do
        def rhs():
            yield Put("k2", "b")
            yield Put("k1", "a")
            return ((yield Get("k1")), (yield Get("k2")))

        assert_equiv(lhs, rhs)


# ============================================================================
# G3 Tell (W1-W3) — observed through Listen
# ============================================================================


def _msgs(collected):
    return [getattr(e, "msg", None) for e in collected]


class TestWriterLaws:
    def test_w1_append_homomorphism(self):
        """log(tell a; tell b) = log(tell a) ++ log(tell b), order preserved."""

        @do
        def both():
            _, collected = yield Listen(_tell_seq(["a", "b"]))
            return _msgs(collected)

        @do
        def split():
            _, ca = yield Listen(_tell_seq(["a"]))
            _, cb = yield Listen(_tell_seq(["b"]))
            return _msgs(ca) + _msgs(cb)

        assert_equiv(both, split)

    def test_w2_listen_extraction(self):
        """log(p; tell a) = log(p) ++ [a]."""

        @do
        def lhs():
            _, collected = yield Listen(_tell_seq(["x", "y", "a"]))
            return _msgs(collected)

        @do
        def rhs():
            _, collected = yield Listen(_tell_seq(["x", "y"]))
            return _msgs(collected) + ["a"]

        assert_equiv(lhs, rhs)

    def test_w3_noncommutativity_refutation(self):
        """W3: tell does NOT commute — reordering tells is an illegal rewrite."""

        @do
        def ab():
            _, collected = yield Listen(_tell_seq(["a", "b"]))
            return _msgs(collected)

        @do
        def ba():
            _, collected = yield Listen(_tell_seq(["b", "a"]))
            return _msgs(collected)

        assert_not_equiv(ab, ba)


@do
def _tell_seq(messages):
    for m in messages:
        yield Tell(m)
    return None


# ============================================================================
# G4 Await (AW1-AW2)
# ============================================================================


async def _pure(x):
    return x


class TestAwaitLaws:
    def test_aw1_unit(self):
        @do
        def lhs():
            return (yield Await(_pure(42)))

        @do
        def rhs():
            return 42

        assert_equiv(lhs, rhs)

    def test_aw2_sequencing_single_task(self):
        """AW2 holds in single-task contexts (D17): fusing awaits is legal."""

        async def c1():
            await asyncio.sleep(0)
            return 10

        async def c2(x):
            await asyncio.sleep(0)
            return x + 1

        async def fused():
            return await c2(await c1())

        @do
        def lhs():
            x = yield Await(c1())
            return (yield Await(c2(x)))

        @do
        def rhs():
            return (yield Await(fused()))

        assert_equiv(lhs, rhs)

    @pytest.mark.skip(
        reason="AW2 counterexample (async × multi-task: yield-point count is "
        "observable) needs the deterministic sim driver to be reproducible — "
        "real-clock schedules are flaky. Blocked on strategy priority 3 "
        "(deterministic simulator). See algebra-draft.md §3 G4 / D17."
    )
    def test_aw2_multi_task_refutation(self):
        pass


# ============================================================================
# G5 Spawn/Wait (CC1-CC5) + CS1 cooperative atomicity
# ============================================================================


class TestConcurrencyLaws:
    def test_cc1_roundtrip(self):
        """CC1: Wait(Spawn(p)) ≡ p for interference-free p (D16-conformant)."""

        @do
        def p():
            base = yield Ask("base")
            return base * 2

        @do
        def lhs():
            t = yield Spawn(p())
            return (yield Wait(t))

        assert_equiv(lhs, p, env={"base": 21})

    def test_cc2_gather_is_traverse_wait(self):
        @do
        def task(i):
            return i * 10

        @do
        def lhs():
            ts = []
            for i in range(3):
                ts.append((yield Spawn(task(i))))
            return (yield Gather(*ts))

        @do
        def rhs():
            ts = []
            for i in range(3):
                ts.append((yield Spawn(task(i))))
            out = []
            for t in ts:
                out.append((yield Wait(t)))
            return out

        assert_equiv(lhs, rhs)

    def test_cc4_complete_wait_commute(self):
        """CC4: CompletePromise and Wait(future) commute (waiters held)."""

        @do
        def complete_then_wait():
            promise = yield CreatePromise()
            yield CompletePromise(promise, "done")
            return (yield Wait(promise.future))

        @do
        def wait_then_complete():
            promise = yield CreatePromise()
            t = yield Spawn(_completer(promise))
            value = yield Wait(promise.future)
            yield Wait(t)
            return value

        @do
        def pure():
            return "done"

        assert_equiv(complete_then_wait, pure)
        assert_equiv(wait_then_complete, pure)

    def test_spawn_is_yield_point_child_runs_first(self):
        """Scheduling microstructure (CC5 corollary): Spawn is itself a yield
        point, and the child runs BEFORE the spawner resumes — both are
        enqueued at NORMAL and the child is enqueued first (FIFO tie-break,
        scheduler.py:466-468). The observer therefore reads the store before
        any of the spawner's subsequent puts."""

        @do
        def main():
            observer = yield Spawn(_read_pair())
            yield Put("a", 1)
            yield Put("b", 1)
            return (yield Wait(observer))

        result = run1(main, store={"a": 0, "b": 0})
        assert result.is_ok()
        assert result.value == (0, 0)

    def test_cs1_no_yield_point_no_interleave(self):
        """CS1: no scheduler effect between the two puts → the gated observer
        cannot run between them; it observes both writes (1, 1).

        Gate protocol: observer blocks on a promise; CompletePromise wakes it
        at NORMAL while the completer re-enqueues at IDLE (scheduler.py:593-597),
        so the observer deterministically runs before main continues."""

        @do
        def main():
            gate = yield CreatePromise()
            observer = yield Spawn(_gated_read_pair(gate))
            yield Put("a", 1)
            yield Put("b", 1)  # no yield point since the put above
            yield CompletePromise(gate, None)
            return (yield Wait(observer))

        result = run1(main, store={"a": 0, "b": 0})
        assert result.is_ok()
        assert result.value == (1, 1)

    def test_cs1_yield_point_admits_interleave(self):
        """CS1 converse: a scheduler effect BETWEEN the puts is exactly where
        the observer runs — it observes the intermediate state (1, 0).

        The exact value also exercises CC5: with the deterministic scheduler
        this schedule is reproducible, not merely possible."""

        @do
        def main():
            gate = yield CreatePromise()
            observer = yield Spawn(_gated_read_pair(gate))
            yield Put("a", 1)
            yield CompletePromise(gate, None)  # yield point — observer runs
            yield Put("b", 1)
            return (yield Wait(observer))

        result = run1(main, store={"a": 0, "b": 0})
        assert result.is_ok()
        assert result.value == (1, 0)

    def test_cc5_determinism(self):
        """CC5: without external promises, scheduling is a deterministic
        function of the input — two runs produce identical interleavings.

        Cross-task Get/Put here intentionally violates the D16 sharing norm:
        observing the schedule itself requires shared observation. This is a
        meta-law test, not an example to imitate."""

        @do
        def worker(tag, n):
            for i in range(n):
                trace = yield Get("trace")
                yield Put("trace", trace + [f"{tag}{i}"])
                t = yield Spawn(_noop())
                yield Wait(t)  # force a yield point each iteration
            return tag

        @do
        def main():
            yield Put("trace", [])
            ts = []
            for tag in ("x", "y", "z"):
                ts.append((yield Spawn(worker(tag, 3))))
            done = yield Gather(*ts)
            trace = yield Get("trace")
            return (done, trace)

        first = run1(main, store={})
        second = run1(main, store={})
        assert first.is_ok() and second.is_ok()
        assert first.value == second.value, (
            f"scheduler nondeterminism detected:\n{first.value}\n{second.value}"
        )
        # The interleaved trace must contain every marker exactly once.
        assert sorted(first.value[1]) == sorted(
            f"{tag}{i}" for tag in ("x", "y", "z") for i in range(3)
        )


@do
def _read_pair():
    return ((yield Get("a")), (yield Get("b")))


@do
def _gated_read_pair(gate):
    yield Wait(gate.future)
    return ((yield Get("a")), (yield Get("b")))


@do
def _noop():
    return None


@do
def _completer(promise):
    yield CompletePromise(promise, "done")
    return None
