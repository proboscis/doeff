"""MemoDeleteEffect handling: delete-then-get must recompute.

Issue doeff-core-effects-delete-effects-drift (maintainer 裁定 A, 2026-07-18):
MemoDeleteEffect は語彙として定義済みだが handler が処理していなかった。
この invariant probe 群は、Put(k) → Delete(k) → Get(k) がいかなる handler
スタック構成でも stale 値を返さないことを固定する。

層状 memo では同一鍵が複数層に存在しうる(MemoPut の _broadcast-put と
MemoGet MISS 時の write-through — _memo_handlers_impl.hy)。したがって
Delete は Get が到達しうる全層(同じ cost ガードを通過する全層)から
削除しなければならない — broadcast delete。再計算は呼び出し回数カウンタで
直接観測し、実削除は InMemoryStorage.keys() で直接観測する。
"""

from doeff import EffectBase, do
from doeff import handler as program_handler
from doeff.program import Pass, Resume
from doeff_core_effects.memo_effects import MemoDelete, MemoGet, MemoPut
from doeff_core_effects.memo_handlers import make_memo_rewriter, memo_handler
from doeff_core_effects.memo_policy import MemoPolicy, RecomputeCost
from doeff_core_effects.storage import InMemoryStorage

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

    # Delete 後の Get は miss — 単層では外側 storage が無いので KeyError で確定
    assert result.is_err(), f"expected Err(KeyError) after delete, got {result!r}"
    assert isinstance(result.error, KeyError), f"expected KeyError, got {result.error!r}"
    # 実削除の直接観測: storage から鍵が消えている
    assert "k1" not in list(storage.keys())


def test_memo_put_broadcasts_to_all_layers_precondition():
    # 前提の実証: MemoPut は broadcast され、同一鍵が両層に存在する。
    # (これが Delete も broadcast でなければならない理由。既存動作の固定。)
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
    # 不変量: Delete は Get が到達しうる全層から削除する(単一層のみの削除は不可)。
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
    # 実削除の直接観測: broadcast put で両層に入った鍵が、両層から消えている
    assert "k-layers" not in list(inner.keys())
    assert "k-layers" not in list(outer.keys())


def test_memo_put_delete_get_recomputes_across_two_layer_stack():
    # 必須 probe: 2 層 memo スタックで Put→Delete→Get が再計算になることを
    # 呼び出し回数カウンタで直接観測する。外側層に stale が残る実装だと
    # 2 回目は outer HIT になり count が 1 のまま fail する。
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
    # 冪等性: 存在しない鍵の Delete は no-op で成功する。
    # 返り値は既存 Put との対称性で None。
    storage = InMemoryStorage()

    @do
    def body():
        result = yield MemoDelete("never-put")
        return ("ok", result)

    result = run_with_defaults(memo_handler(storage, name="L1")(body()))
    assert result.is_ok(), f"missing-key delete must succeed, got {result!r}"
    assert result.value == ("ok", None)


def test_memo_delete_returns_none_like_put():
    # 返り値規約の固定: Put が None を resume するのと対称に Delete も None。
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
    # cost ガードの整合: Delete は Get と同じ :when ガードを通過する層にだけ
    # 到達する。CHEAP の Delete は EXPENSIVE 層に触れないので、
    # Get(EXPENSIVE) が読む値は保持される。
    cheap, expensive = InMemoryStorage(), InMemoryStorage()

    @do
    def body():
        yield MemoPut(
            "k-exp", "v-exp", policy=MemoPolicy(recompute_cost=RecomputeCost.EXPENSIVE)
        )
        yield MemoDelete("k-exp")  # default cost=CHEAP — EXPENSIVE 層は素通り
        got = yield MemoGet("k-exp", recompute_cost=RecomputeCost.EXPENSIVE)
        return got

    composed = memo_handler(expensive, cost=RecomputeCost.EXPENSIVE, name="exp")(
        memo_handler(cheap, cost=RecomputeCost.CHEAP, name="cheap")(body())
    )
    result = run_with_defaults(composed)
    assert result.is_ok(), f"program failed: {result.error!r}"
    assert result.value == "v-exp"
    assert "k-exp" in list(expensive.keys())
