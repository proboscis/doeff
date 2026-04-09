"""Memo policy types for effect memoization routing."""

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class Lifecycle(str, Enum):
    """How long memoized data should be retained."""

    TRANSIENT = "transient"
    SESSION = "session"
    PERSISTENT = "persistent"


class RecomputeCost(str, Enum):
    """How expensive it is to recompute a memoized value.

    Drives storage routing:
    - CHEAP: disposable, can be recomputed at near-zero cost (e.g. free API, fast computation)
    - EXPENSIVE: paid API calls, long-running computations ($)
    - IRREPRODUCIBLE: source may be gone, non-deterministic, or unrepeatable
    """

    CHEAP = "cheap"
    EXPENSIVE = "expensive"
    IRREPRODUCIBLE = "irreproducible"


@dataclass(frozen=True)
class MemoPolicy:
    """Policy metadata attached to memoized entries.

    Used by memo_handler to route storage:
    - recompute_cost determines WHICH storage backend (cheap→Redis, expensive→MinIO)
    - lifecycle is a retention hint
    - ttl is an explicit time-to-live
    """

    recompute_cost: RecomputeCost = RecomputeCost.CHEAP
    lifecycle: Lifecycle = Lifecycle.TRANSIENT
    ttl: float | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)


def ensure_memo_policy(
    *,
    recompute_cost: RecomputeCost | str | None = None,
    ttl: float | None = None,
    lifecycle: Lifecycle | str | None = None,
    metadata: Mapping[str, Any] | None = None,
    policy: MemoPolicy | Mapping[str, Any] | None = None,
) -> MemoPolicy:
    """Normalize parameters into a MemoPolicy instance."""

    if policy is not None:
        if any(param is not None for param in (recompute_cost, ttl, lifecycle, metadata)):
            raise ValueError("When policy is provided, other memo policy parameters must be omitted")
        if isinstance(policy, MemoPolicy):
            return policy
        if isinstance(policy, Mapping):
            return ensure_memo_policy(**policy)  # type: ignore[misc]
        raise TypeError("policy must be MemoPolicy or mapping")

    recompute_cost_value = _normalize_recompute_cost(recompute_cost)
    lifecycle_value = _normalize_lifecycle(lifecycle)
    metadata_dict: Mapping[str, Any]
    if metadata is None:
        metadata_dict = {}
    elif isinstance(metadata, Mapping):
        metadata_dict = dict(metadata)
    else:
        raise TypeError("metadata must be mapping if provided")

    return MemoPolicy(
        recompute_cost=recompute_cost_value,
        ttl=ttl,
        lifecycle=lifecycle_value,
        metadata=metadata_dict,
    )


def _normalize_recompute_cost(value: RecomputeCost | str | None) -> RecomputeCost:
    if value is None:
        return RecomputeCost.CHEAP
    if isinstance(value, RecomputeCost):
        return value
    return RecomputeCost(value)


def _normalize_lifecycle(value: Lifecycle | str | None) -> Lifecycle:
    if value is None:
        return Lifecycle.TRANSIENT
    if isinstance(value, Lifecycle):
        return value
    return Lifecycle(value)
