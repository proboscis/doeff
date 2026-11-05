"""MemoDeleteEffect handling: delete-then-get must recompute.

Issue doeff-core-effects-delete-effects-drift:
MemoDeleteEffect was defined as vocabulary but no handler processed it.
This group of invariant probes pins that Put(k) -> Delete(k) -> Get(k) never returns a stale value under any handler
stack configuration.

In a layered memo the same key can exist in several layers (MemoPut's _broadcast-put and
the write-through on a MemoGet MISS -- _memo_handlers_impl.hy). Therefore
Delete must remove the key from every layer Get can reach (every layer passing the same cost guard)
-- a broadcast delete. Recomputation is observed directly via a call counter,
and the actual deletion is observed directly via InMemoryStorage.keys().
"""

from doeff_core_effects import MemoDelete, MemoGet, MemoPut
from doeff_core_effects import memo_handler
from doeff_core_effects import make_memo_rewriter
from doeff_core_effects import MemoPolicy, RecomputeCost
from doeff_core_effects import InMemoryStorage

from doeff import EffectBase, do
from doeff import handler as program_handler
from doeff.program import Pass, Resume
from tests._run_helpers import run_with_defaults

_CHEAP = MemoPolicy(recompute_cost=RecomputeCost.CHEAP)


class _ProbeEffect(EffectBase):
    """Effect memoized through make_memo_rewriter in the recompute probes."""

    def __init__(self, tag: str):
        super().__init__()
        self.tag = tag


def _probe_compute_handler(calls: dict):
    """Terminal handler for _ProbeEffect — counts real computations."""

    @do
    def handler(effect, k):
        if not isinstance(effect, _ProbeEffect):
            yield Pass(effect, k)
            return None
        calls["count"] += 1
        result = yield Resume(k, f"computed:{effect.tag}")
        return result

    return program_handler(handler)


def test_memo_delete_single_layer_get_misses_after_delete():
    storage = InMemoryStorage()

    @do
    def body():
        yield MemoPut("k1", "v1", policy=_CHEAP)
        yield MemoDelete("k1")
        got = yield MemoGet("k1")
        return got

    result = run_with_defaults(memo_handler(storage, name="L1")(body()))

    # Get after Delete is a miss -- with a single layer there is no outer storage, so KeyError is certain
    assert result.is_err(), f"expected Err(KeyError) after delete, got {result!r}"
    assert isinstance(result.error, KeyError), f"expected KeyError, got {result.error!r}"
    # Direct observation of the actual deletion: the key is gone from storage
    assert "k1" not in list(storage.keys())


def test_memo_put_broadcasts_to_all_layers_precondition():
    # Premise check: MemoPut is broadcast, and the same key exists in both layers.
    # (This is why Delete must also be a broadcast. Pins existing behavior.)
    inner, outer = InMemoryStorage(), InMemoryStorage()

    @do
    def body():
        yield MemoPut("k-pre", "v", policy=_CHEAP)
        return "done"

    result = run_with_defaults(
        memo_handler(outer, name="outer")(memo_handler(inner, name="inner")(body()))
    )
    assert result.is_ok(), f"program failed: {result.error!r}"
    assert "k-pre" in list(inner.keys())
    assert "k-pre" in list(outer.keys())


def test_memo_delete_removes_key_from_all_layers():
    # Invariant: Delete removes the key from every layer Get can reach (deleting from a single layer only is not allowed).
    inner, outer = InMemoryStorage(), InMemoryStorage()

    @do
    def body():
        yield MemoPut("k-layers", "v", policy=_CHEAP)
        yield MemoDelete("k-layers")
        return "done"

    result = run_with_defaults(
        memo_handler(outer, name="outer")(memo_handler(inner, name="inner")(body()))
    )
    assert result.is_ok(), f"program failed: {result.error!r}"
    # Direct observation of the actual deletion: the key put into both layers by the broadcast put is gone from both layers
    assert "k-layers" not in list(inner.keys())
    assert "k-layers" not in list(outer.keys())


def test_memo_put_delete_get_recomputes_across_two_layer_stack():
    # Required probe: with a 2-layer memo stack, observe directly via a call counter
    # that Put->Delete->Get causes a recomputation. An implementation that leaves a stale value in the outer layer
    # would get an outer HIT on the second call and fail with count staying at 1.
    calls = {"count": 0}
    inner, outer = InMemoryStorage(), InMemoryStorage()
    rewriter = make_memo_rewriter(_ProbeEffect, key_fn=lambda _e: "probe-key")

    @do
    def body():
        first = yield _ProbeEffect("x")
        yield MemoDelete("probe-key")
        second = yield _ProbeEffect("x")
        return (first, second)

    composed = _probe_compute_handler(calls)(
        memo_handler(outer, name="outer")(
            memo_handler(inner, name="inner")(rewriter(body()))
        )
    )
    result = run_with_defaults(composed)

    assert result.is_ok(), f"program failed: {result.error!r}"
    assert result.value == ("computed:x", "computed:x")
    assert calls["count"] == 2, (
        f"expected recompute after delete (count=2), got count={calls['count']} — "
        "a stale layer served the deleted key"
    )


def test_memo_delete_missing_key_is_noop_and_returns_none():
    # Idempotence: Delete of a nonexistent key is a no-op and succeeds.
    # The return value is None, symmetric with the existing Put.
    storage = InMemoryStorage()

    @do
    def body():
        result = yield MemoDelete("never-put")
        return ("ok", result)

    result = run_with_defaults(memo_handler(storage, name="L1")(body()))
    assert result.is_ok(), f"missing-key delete must succeed, got {result!r}"
    assert result.value == ("ok", None)


def test_memo_delete_returns_none_like_put():
    # Pin the return value convention: just as Put resumes with None, Delete also resumes with None.
    storage = InMemoryStorage()

    @do
    def body():
        put_result = yield MemoPut("k-sym", "v", policy=_CHEAP)
        delete_result = yield MemoDelete("k-sym")
        return (put_result, delete_result)

    result = run_with_defaults(memo_handler(storage, name="L1")(body()))
    assert result.is_ok(), f"program failed: {result.error!r}"
    assert result.value == (None, None)


def test_memo_delete_respects_cost_guard_like_get():
    # Cost guard consistency: Delete reaches only the layers that pass the same :when guard as Get.
    # A CHEAP Delete does not touch the EXPENSIVE layer,
    # so the value Get(EXPENSIVE) reads is retained.
    cheap, expensive = InMemoryStorage(), InMemoryStorage()

    @do
    def body():
        yield MemoPut(
            "k-exp", "v-exp", policy=MemoPolicy(recompute_cost=RecomputeCost.EXPENSIVE)
        )
        yield MemoDelete("k-exp")  # default cost=CHEAP -- the EXPENSIVE layer is passed through
        got = yield MemoGet("k-exp", recompute_cost=RecomputeCost.EXPENSIVE)
        return got

    composed = memo_handler(expensive, cost=RecomputeCost.EXPENSIVE, name="exp")(
        memo_handler(cheap, cost=RecomputeCost.CHEAP, name="cheap")(body())
    )
    result = run_with_defaults(composed)
    assert result.is_ok(), f"program failed: {result.error!r}"
    assert result.value == "v-exp"
    assert "k-exp" in list(expensive.keys())
