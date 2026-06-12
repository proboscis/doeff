"""memo_handler lazy storage: a Program[DurableStorage] resolves on first use.

The storage argument may be a Program constructing the backend. It must not
be resolved by handler installation alone — only by the first handled memo
effect — and it must be resolved exactly once per handler instance.
"""

from doeff import do
from doeff_core_effects.memo_effects import MemoGet, MemoPut
from doeff_core_effects.memo_handlers import memo_handler
from doeff_core_effects.memo_policy import MemoPolicy, RecomputeCost
from doeff_core_effects.storage import InMemoryStorage

from tests._run_helpers import run_with_defaults


def _lazy_storage(calls: list) -> tuple:
    storage = InMemoryStorage()

    @do
    def build():
        calls.append(1)
        return storage

    return build(), storage


@do
def _no_memo_program():
    return "untouched"


@do
def _put_then_get(key: str, value: str):
    yield MemoPut(key, value, policy=MemoPolicy(recompute_cost=RecomputeCost.CHEAP))
    got = yield MemoGet(key)
    return got


def _unwrap(result):
    assert result.is_ok(), f"program failed: {getattr(result, 'error', result)!r}"
    return result.value


def test_lazy_storage_not_resolved_without_memo_effects():
    calls: list = []
    storage_program, _ = _lazy_storage(calls)

    value = _unwrap(
        run_with_defaults(memo_handler(storage_program, name="lazy")(_no_memo_program()))
    )

    assert value == "untouched"
    assert calls == [], "storage Program must not resolve when no memo effect is handled"


def test_lazy_storage_resolves_once_on_first_memo_effect():
    calls: list = []
    storage_program, _ = _lazy_storage(calls)

    value = _unwrap(
        run_with_defaults(memo_handler(storage_program, name="lazy")(_put_then_get("k1", "v1")))
    )

    assert value == "v1"
    assert calls == [1], f"storage Program must resolve exactly once, resolved {len(calls)}x"


def test_lazy_storage_shared_nothing_between_instances():
    calls_a: list = []
    calls_b: list = []
    program_a, _ = _lazy_storage(calls_a)
    program_b, _ = _lazy_storage(calls_b)

    @do
    def body():
        yield MemoPut(
            "k-cheap",
            "cheap-value",
            policy=MemoPolicy(recompute_cost=RecomputeCost.CHEAP),
        )
        got = yield MemoGet("k-cheap", recompute_cost=RecomputeCost.CHEAP)
        return got

    # Two stacked lazy tiers: only the cheap tier should resolve its storage.
    composed = memo_handler(program_a, cost=RecomputeCost.EXPENSIVE, name="expensive")(
        memo_handler(program_b, cost=RecomputeCost.CHEAP, name="cheap")(body())
    )
    value = _unwrap(run_with_defaults(composed))

    assert value == "cheap-value"
    assert calls_b == [1], "cheap tier must resolve its own storage once"
    assert calls_a == [], (
        "expensive tier must NOT resolve: no expensive-cost effect was performed"
    )


def test_eager_storage_still_works():
    storage = InMemoryStorage()
    value = _unwrap(
        run_with_defaults(memo_handler(storage, name="eager")(_put_then_get("k2", "v2")))
    )
    assert value == "v2"
