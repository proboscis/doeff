"""Pure replay identity and cache-key helpers for conductor workflows."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, kw_only=True)
class ResolvedIdentity:
    """Result-distribution-affecting identity resolved from a profile."""

    adapter: str
    model: str
    identity: str | None = None


def _canonical_payload(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            str(key): _canonical_payload(value[key])
            for key in sorted(value, key=str)
        }
    if isinstance(value, (list, tuple)):
        return [_canonical_payload(item) for item in value]
    if isinstance(value, set):
        return sorted(_canonical_payload(item) for item in value)
    if isinstance(value, type):
        return {"python_type": f"{value.__module__}.{value.__qualname__}"}
    if hasattr(value, "model_json_schema"):
        model_schema = value.model_json_schema()
        return _canonical_payload(model_schema)
    return value


def _canonical_json(value: Any) -> str:
    return json.dumps(
        _canonical_payload(value),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    )


def _sha256_payload(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def resolved_identity_fingerprint(identity: ResolvedIdentity) -> str:
    """Fingerprint the resolved profile, not the profile name."""

    return _sha256_payload(
        {
            "adapter": identity.adapter,
            "model": identity.model,
            "identity": identity.identity,
        }
    )


def agent_cache_key(
    *,
    prompt: Any,
    schema: Any,
    resolved_identity: ResolvedIdentity,
    substrate: str | None = None,
) -> str:
    """Return the L3 agent cache key.

    ``substrate`` is accepted to make exclusion explicit at call sites. It is
    intentionally not included in the hashed payload.
    """

    _ = substrate
    return _sha256_payload(
        {
            "prompt": prompt,
            "schema": schema,
            "resolved_identity": resolved_identity_fingerprint(resolved_identity),
        }
    )


def node_identity_fingerprint(
    *,
    workflow_name: str,
    node_path: tuple[str, ...],
    loop_indices: tuple[int, ...] = (),
) -> str:
    """Fingerprint a node's static path plus bounded-loop iteration index."""

    return _sha256_payload(
        {
            "workflow_name": workflow_name,
            "node_path": list(node_path),
            "loop_indices": list(loop_indices),
        }
    )


def longest_valid_prefix(previous_keys: list[str], current_keys: list[str]) -> int:
    """Return how many journal keys can be replayed before the first edit."""

    prefix_length = 0
    for previous_key, current_key in zip(previous_keys, current_keys, strict=False):
        if previous_key != current_key:
            return prefix_length
        prefix_length += 1
    return prefix_length
