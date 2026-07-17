"""CacheDeleteEffect handling: delete-then-get must recompute.

Issue doeff-core-effects-delete-effects-drift (maintainer 裁定 A, 2026-07-18):
CacheDeleteEffect は語彙として定義済みだが cache_handler が処理していなかった。

cache_handler は単層意味論: cache effect を必ず自分で処理し re-perform しない
(cache_handlers.py の handler 本体)ため、Get と Delete は常に同一 storage に
到達する — 単層削除で「削除後の Get は必ず再計算」が成立する。
再計算は呼び出し回数カウンタで、実削除は InMemoryStorage.keys() で直接観測する。
"""

from doeff_core_effects.cache_effects import CacheDelete, CacheExists, CacheGet, CachePut
from doeff_core_effects.cache_handlers import cache_handler, make_memo_rewriter
from doeff_core_effects.storage import InMemoryStorage

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

    # Delete 後の Get は miss — cache_handler の miss は KeyError で確定
    assert result.is_err(), f"expected Err(KeyError) after delete, got {result!r}"
    assert isinstance(result.error, KeyError), f"expected KeyError, got {result.error!r}"
    # 実削除の直接観測: storage から鍵が消えている
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
    # 再計算 probe: Delete 後の 2 回目は cache hit せず再計算される。
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
    # 冪等性: 存在しない鍵の Delete は no-op で成功する。
    # 返り値は既存 Put との対称性で None。
    storage = InMemoryStorage()

    @do
    def body():
        result = yield CacheDelete("never-put")
        return ("ok", result)

    result = run_with_defaults(cache_handler(storage)(body()))
    assert result.is_ok(), f"missing-key delete must succeed, got {result!r}"
    assert result.value == ("ok", None)


def test_cache_delete_returns_none_like_put():
    # 返り値規約の固定: Put が None を resume するのと対称に Delete も None。
    storage = InMemoryStorage()

    @do
    def body():
        put_result = yield CachePut("k-sym", "v")
        delete_result = yield CacheDelete("k-sym")
        return (put_result, delete_result)

    result = run_with_defaults(cache_handler(storage)(body()))
    assert result.is_ok(), f"program failed: {result.error!r}"
    assert result.value == (None, None)
