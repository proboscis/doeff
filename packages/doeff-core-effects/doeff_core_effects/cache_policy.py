"""Cache policy types shared between cache effects and handlers."""


from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class CacheLifecycle(str, Enum):
    """Lifecycle hint describing how long cached data should live."""

    TRANSIENT = "transient"
    SESSION = "session"
    PERSISTENT = "persistent"


class CacheStorage(str, Enum):
    """Preferred storage target for cached entries."""

    MEMORY = "memory"
    DISK = "disk"


@dataclass(frozen=True)
class CachePolicy:
    """Policy metadata attached to cache entries."""

    ttl: float | None = None
    lifecycle: CacheLifecycle = CacheLifecycle.TRANSIENT
    storage: CacheStorage | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def resolved_storage(self) -> CacheStorage:
        """Determine the effective storage based on hints."""
        if self.storage is not None:
            return self.storage
        if self.lifecycle is CacheLifecycle.PERSISTENT:
            return CacheStorage.DISK
        return CacheStorage.MEMORY


def ensure_cache_policy(
    *,
    ttl: float | None = None,
    lifecycle: CacheLifecycle | str | None = None,
    storage: CacheStorage | str | None = None,
    metadata: Mapping[str, Any] | None = None,
    policy: CachePolicy | Mapping[str, Any] | None = None,
) -> CachePolicy:
    """Normalize parameters into a CachePolicy instance."""

    if policy is not None:
        if any(param is not None for param in (ttl, lifecycle, storage, metadata)):
            raise ValueError("When policy is provided, other cache policy parameters must be omitted")
        if isinstance(policy, CachePolicy):
            return policy
        if isinstance(policy, Mapping):
            return ensure_cache_policy(**policy)  # type: ignore[misc]
        raise TypeError("policy must be CachePolicy or mapping")

    lifecycle_value = _normalize_lifecycle(lifecycle)
    storage_value = _normalize_storage(storage)
    metadata_dict: Mapping[str, Any]
    if metadata is None:
        metadata_dict = {}
    elif isinstance(metadata, Mapping):
        metadata_dict = dict(metadata)
    else:
        raise TypeError("metadata must be mapping if provided")

    return CachePolicy(
        ttl=ttl,
        lifecycle=lifecycle_value,
        storage=storage_value,
        metadata=metadata_dict,
    )


def _normalize_lifecycle(value: CacheLifecycle | str | None) -> CacheLifecycle:
    if value is None:
        return CacheLifecycle.TRANSIENT
    if isinstance(value, CacheLifecycle):
        return value
    return CacheLifecycle(value)


def _normalize_storage(value: CacheStorage | str | None) -> CacheStorage | None:
    if value is None:
        return None
    if isinstance(value, CacheStorage):
        return value
    return CacheStorage(value)



__all__ = [
    "CacheLifecycle",
    "CachePolicy",
    "CacheStorage",
    "ensure_cache_policy",
]
