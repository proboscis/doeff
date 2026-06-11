"""Pure replay identity and cache-key helpers for conductor workflows."""

import hashlib
import json
from dataclasses import asdict, dataclass, is_dataclass
from typing import Any


@dataclass(frozen=True, kw_only=True)
class ResolvedIdentity:
    """Result-distribution-affecting identity resolved from a profile."""

    adapter: str
    model: str | None
    identity: str | None = None
    effort: str | None = None


def _canonical_payload(value: Any) -> Any:
    canonical_source = (
        asdict(value) if is_dataclass(value) and not isinstance(value, type) else value
    )
    if isinstance(canonical_source, dict):
        return {
            str(key): _canonical_payload(canonical_source[key])
            for key in sorted(canonical_source, key=str)
        }
    if isinstance(canonical_source, (list, tuple)):
        return [_canonical_payload(item) for item in canonical_source]
    if isinstance(canonical_source, set):
        return sorted(_canonical_payload(item) for item in canonical_source)
    if isinstance(canonical_source, type):
        return {"python_type": f"{canonical_source.__module__}.{canonical_source.__qualname__}"}
    if hasattr(canonical_source, "model_json_schema"):
        model_schema = canonical_source.model_json_schema()
        return _canonical_payload(model_schema)
    return canonical_source


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
            "model": identity.model if identity.model is not None else "adapter-default",
            "identity": identity.identity,
            # Reasoning effort changes the result distribution (ADR D7), so it
            # is part of the identity fingerprint, never a run parameter.
            "effort": identity.effort,
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


def workflow_effect_cache_key(
    *,
    effect_kind: str,
    node_id: str,
    label: str | None,
    spec: Any,
) -> str:
    """Return the replay cache key for journaled workflow effects."""

    return _sha256_payload(
        {
            "effect_kind": effect_kind,
            "node_id": node_id,
            "label": label,
            "spec": spec,
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
