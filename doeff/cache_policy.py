"""Cache policy types shared between cache effects and handlers."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
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


@dataclass
class CacheEntry:
    """Concrete cached value with policy and bookkeeping."""

    value: Any | None
    expiry: float | None
    policy: CachePolicy
    artifact_path: str | None = None

    def is_expired(self, now: float) -> bool:
        """Check whether this entry has expired."""
        return self.expiry is not None and now > self.expiry


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


def disk_artifact_path(base_dir: Path, key_hash: str) -> Path:
    """Compute a deterministic disk path for a cache artifact."""

    safe_name = key_hash.replace(":", "_")
    return base_dir / f"{safe_name}.cache"


__all__ = [
    "CacheEntry",
    "CacheLifecycle",
    "CachePolicy",
    "CacheStorage",
    "disk_artifact_path",
    "ensure_cache_policy",
]
