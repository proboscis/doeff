"""Memo effects for memoization.

These effects represent storage operations for memoized computation results.
The recompute_cost field drives routing: cheap→ephemeral storage, expensive→durable storage.
"""

from collections.abc import Mapping
from typing import Any

from doeff_core_effects.memo_policy import (
    Lifecycle,
    MemoPolicy,
    RecomputeCost,
    ensure_memo_policy,
)
from doeff_vm import EffectBase


class MemoGetEffect(EffectBase):
    """Requests the memoized value for the key."""
    def __init__(self, key, recompute_cost=RecomputeCost.CHEAP):
        super().__init__()
        self.key = key
        self.recompute_cost = recompute_cost

    def __repr__(self):
        return f"MemoGet({self.key!r}, cost={self.recompute_cost.value})"


class MemoPutEffect(EffectBase):
    """Persists a memoized value under the key."""
    def __init__(self, key, value, policy):
        super().__init__()
        if not isinstance(policy, MemoPolicy):
            raise TypeError(f"policy must be MemoPolicy, got {type(policy).__name__}")
        self.key = key
        self.value = value
        self.policy = policy

    def __repr__(self):
        return f"MemoPut({self.key!r}, ..., cost={self.policy.recompute_cost.value})"


class MemoDeleteEffect(EffectBase):
    """Deletes the memoized value under the key."""
    def __init__(self, key, recompute_cost=RecomputeCost.CHEAP):
        super().__init__()
        self.key = key
        self.recompute_cost = recompute_cost

    def __repr__(self):
        return f"MemoDelete({self.key!r}, cost={self.recompute_cost.value})"


class MemoExistsEffect(EffectBase):
    """Checks if a key exists in memo storage."""
    def __init__(self, key, recompute_cost=RecomputeCost.CHEAP):
        super().__init__()
        self.key = key
        self.recompute_cost = recompute_cost

    def __repr__(self):
        return f"MemoExists({self.key!r}, cost={self.recompute_cost.value})"


# Convenience constructors

def MemoGet(key: Any, *, recompute_cost: RecomputeCost | str = RecomputeCost.CHEAP) -> MemoGetEffect:
    if isinstance(recompute_cost, str):
        recompute_cost = RecomputeCost(recompute_cost)
    return MemoGetEffect(key=key, recompute_cost=recompute_cost)


def MemoPut(
    key: Any,
    value: Any,
    ttl: float | None = None,
    *,
    recompute_cost: RecomputeCost | str | None = None,
    lifecycle: Lifecycle | str | None = None,
    metadata: Mapping[str, Any] | None = None,
    policy: MemoPolicy | Mapping[str, Any] | None = None,
) -> MemoPutEffect:
    memo_policy = ensure_memo_policy(
        recompute_cost=recompute_cost,
        ttl=ttl,
        lifecycle=lifecycle,
        metadata=metadata,
        policy=policy,
    )
    return MemoPutEffect(key=key, value=value, policy=memo_policy)


def MemoDelete(key: Any, *, recompute_cost: RecomputeCost | str = RecomputeCost.CHEAP) -> MemoDeleteEffect:
    if isinstance(recompute_cost, str):
        recompute_cost = RecomputeCost(recompute_cost)
    return MemoDeleteEffect(key=key, recompute_cost=recompute_cost)


def MemoExists(key: Any, *, recompute_cost: RecomputeCost | str = RecomputeCost.CHEAP) -> MemoExistsEffect:
    if isinstance(recompute_cost, str):
        recompute_cost = RecomputeCost(recompute_cost)
    return MemoExistsEffect(key=key, recompute_cost=recompute_cost)
