"""CacheDeleteEffect handling: delete-then-get must recompute.

Issue doeff-core-effects-delete-effects-drift:
CacheDeleteEffect was defined as vocabulary, but cache_handler did not handle it.

cache_handler has single-layer semantics: it always handles cache effects itself and does not re-perform
(the handler body in cache_handlers.py), so Get and Delete always reach the same storage --
with single-layer deletion, "a Get after delete always recomputes" holds.
Recomputation is observed with a call counter, and the actual deletion directly with InMemoryStorage.keys().
"""

from doeff_core_effects import CacheDelete, CacheExists, CacheGet, CachePut
from doeff_core_effects import cache_handler
from doeff_core_effects._cache_handlers import make_memo_rewriter
from doeff_core_effects import InMemoryStorage

from doeff import EffectBase, do
from doeff import handler as program_handler
from doeff.program import Pass, Resume
from tests._run_helpers import run_with_defaults


class _ProbeEffect(EffectBase):
    """Effect memoized through make_memo_rewriter in the recompute probe."""

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


def test_cache_put_delete_get_raises_keyerror():
    storage = InMemoryStorage()

    @do
    def body():
        yield CachePut("k1", "v1")
        yield CacheDelete("k1")
        got = yield CacheGet("k1")
        return got

    result = run_with_defaults(cache_handler(storage)(body()))

    # A Get after Delete is a miss -- cache_handler's miss is determined by KeyError
    assert result.is_err(), f"expected Err(KeyError) after delete, got {result!r}"
    assert isinstance(result.error, KeyError), f"expected KeyError, got {result.error!r}"
    # Direct observation of actual deletion: the key is gone from storage
    assert "k1" not in list(storage.keys())


def test_cache_delete_then_exists_false():
    storage = InMemoryStorage()

    @do
    def body():
        yield CachePut("k2", "v2")
        before = yield CacheExists("k2")
        yield CacheDelete("k2")
        after = yield CacheExists("k2")
        return (before, after)

    result = run_with_defaults(cache_handler(storage)(body()))
    assert result.is_ok(), f"program failed: {result.error!r}"
    assert result.value == (True, False)


def test_cache_put_delete_get_recompute_probe():
    # Recompute probe: the 2nd Get after Delete does not hit the cache and recomputes.
    calls = {"count": 0}
    storage = InMemoryStorage()
    rewriter = make_memo_rewriter(_ProbeEffect, key_fn=lambda _e: "probe-key")

    @do
    def body():
        first = yield _ProbeEffect("x")
        yield CacheDelete("probe-key")
        second = yield _ProbeEffect("x")
        return (first, second)

    composed = _probe_compute_handler(calls)(cache_handler(storage)(rewriter(body())))
    result = run_with_defaults(composed)

    assert result.is_ok(), f"program failed: {result.error!r}"
    assert result.value == ("computed:x", "computed:x")
    assert calls["count"] == 2, (
        f"expected recompute after delete (count=2), got count={calls['count']} — "
        "the deleted key was served stale"
    )


def test_cache_delete_missing_key_is_noop_and_returns_none():
    # Idempotence: Delete of a nonexistent key succeeds as a no-op.
    # The return value is None, symmetric with the existing Put.
    storage = InMemoryStorage()

    @do
    def body():
        result = yield CacheDelete("never-put")
        return ("ok", result)

    result = run_with_defaults(cache_handler(storage)(body()))
    assert result.is_ok(), f"missing-key delete must succeed, got {result!r}"
    assert result.value == ("ok", None)


def test_cache_delete_returns_none_like_put():
    # Pin the return-value convention: Delete also resumes None, symmetric with Put resuming None.
    storage = InMemoryStorage()

    @do
    def body():
        put_result = yield CachePut("k-sym", "v")
        delete_result = yield CacheDelete("k-sym")
        return (put_result, delete_result)

    result = run_with_defaults(cache_handler(storage)(body()))
    assert result.is_ok(), f"program failed: {result.error!r}"
    assert result.value == (None, None)
