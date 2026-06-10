"""Author-facing conductor environment description and profile resolution."""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from doeff_conductor.replay_keying import ResolvedIdentity, resolved_identity_fingerprint

DEFAULT_SITE_CAPABILITIES: tuple[str, ...] = (
    "durable-sessions",
    "interactive",
    "schema-validation",
)

DEFAULT_ROUTER_POLICY: Mapping[str, str] = {
    "mechanical": "cheap-coder",
    "test-verifiable": "cheap-coder",
    "semantic": "frontier-reviewer",
}

DEFAULT_ROLE_CONVENTIONS: Mapping[str, str] = {
    "implementer": "writes scoped implementation artifacts; usually cheap-coder",
    "fixer": "repairs deterministic gate failures; usually cheap-coder",
    "test-writer": "adds focused verification; usually cheap-coder",
    "reviewer": "emits structured verdicts; usually cheap-reviewer or frontier-reviewer",
}

# House policy: every default profile runs at xhigh reasoning effort.
DEFAULT_PROFILE_DATA: Mapping[str, Mapping[str, Any]] = {
    "cheap-coder": {
        "adapter": "codex",
        "model": None,
        "effort": "xhigh",
        "capabilities": ("durable-sessions", "schema-validation"),
        "budget_units": 1,
    },
    "cheap-reviewer": {
        "adapter": "codex",
        "model": None,
        "effort": "xhigh",
        "capabilities": ("durable-sessions", "schema-validation"),
        "budget_units": 1,
    },
    "frontier-reviewer": {
        "adapter": "claude",
        "model": None,
        "effort": "xhigh",
        "capabilities": ("durable-sessions", "interactive", "schema-validation"),
        "budget_units": 3,
    },
    "frontier-author": {
        "adapter": "claude",
        "model": None,
        "effort": "xhigh",
        "capabilities": ("durable-sessions", "interactive", "schema-validation"),
        "budget_units": 3,
    },
}


@dataclass(frozen=True)
class ProfileBinding:
    """Semantic profile binding without user auth or account contents."""

    name: str
    adapter: str
    model: str | None
    capabilities: tuple[str, ...]
    budget_units: int
    effort: str | None = None

    @classmethod
    def from_mapping(cls, name: str, data: Mapping[str, Any]) -> ProfileBinding:
        adapter: object | None = data.get("adapter")
        model: object | None = data.get("model")
        if not isinstance(adapter, str) or not adapter:
            raise ValueError(f"profile {name!r} requires non-empty adapter")
        # model=None means "the agent CLI's own default model". Inventing a
        # placeholder name here is worse than omitting the flag: an unknown
        # model reaches the worker CLI and every turn fails (observed live:
        # `codex --model default-cheap-coder` exhausted its retries in 14s).
        if model is not None and (not isinstance(model, str) or not model):
            raise ValueError(f"profile {name!r} model must be a non-empty string or None")

        # effort=None means "the agent CLI's own default effort"; an empty
        # string would silently produce a broken CLI flag downstream.
        effort: object | None = data.get("effort")
        if effort is not None and (not isinstance(effort, str) or not effort):
            raise ValueError(f"profile {name!r} effort must be a non-empty string or None")

        raw_capabilities: object = data.get("capabilities", ())
        if not isinstance(raw_capabilities, (list, tuple)):
            raise ValueError(f"profile {name!r} capabilities must be a list")
        capabilities: tuple[str, ...] = tuple(str(item) for item in raw_capabilities)

        raw_budget_units: object = data.get("budget_units", 1)
        if not isinstance(raw_budget_units, int) or isinstance(raw_budget_units, bool):
            raise ValueError(f"profile {name!r} budget_units must be an integer")
        if raw_budget_units < 0:
            raise ValueError(f"profile {name!r} budget_units must be non-negative")

        return cls(
            name=name,
            adapter=adapter,
            model=model,
            capabilities=capabilities,
            budget_units=raw_budget_units,
            effort=effort,
        )

    @property
    def resolved_identity(self) -> ResolvedIdentity:
        return ResolvedIdentity(
            adapter=self.adapter,
            model=self.model,
            identity=None,
            effort=self.effort,
        )

    @property
    def identity_fingerprint(self) -> str:
        return resolved_identity_fingerprint(self.resolved_identity)

    def to_public_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "capabilities": list(self.capabilities),
            "budget_units": self.budget_units,
        }


@dataclass(frozen=True)
class ProfileRegistry:
    """Resolved project profile registry with an environment default."""

    profiles: Mapping[str, ProfileBinding]
    default_profile: str = "cheap-coder"

    def resolve(self, profile_name: str) -> ProfileBinding:
        profile: ProfileBinding | None = self.profiles.get(profile_name)
        if profile is None:
            raise ValueError(f"profile {profile_name!r} is not defined")
        return profile

    def to_public_dict(self) -> dict[str, Any]:
        return {
            "profiles": [
                profile.to_public_dict()
                for profile in sorted(self.profiles.values(), key=lambda item: item.name)
            ],
            "default_profile": self.default_profile,
        }


def load_profile_registry_from_env() -> ProfileRegistry:
    """Load minimal semantic profile bindings from env/config, or defaults."""

    raw_json: str | None = os.environ.get("CONDUCTOR_PROFILES_JSON")
    config_path_text: str | None = os.environ.get("CONDUCTOR_PROFILE_CONFIG")
    raw_default_profile: str | None = os.environ.get("CONDUCTOR_DEFAULT_PROFILE")

    profile_data: Mapping[str, Any]
    if raw_json is not None:
        loaded_json: object = json.loads(raw_json)
        if not isinstance(loaded_json, dict):
            raise ValueError("CONDUCTOR_PROFILES_JSON must be a JSON object")
        profile_data = loaded_json
    elif config_path_text is not None:
        config_path: Path = Path(config_path_text)
        loaded_config: object = json.loads(config_path.read_text(encoding="utf-8"))
        if not isinstance(loaded_config, dict):
            raise ValueError("CONDUCTOR_PROFILE_CONFIG must contain a JSON object")
        profile_data = loaded_config
    else:
        profile_data = DEFAULT_PROFILE_DATA

    profiles: dict[str, ProfileBinding] = {}
    for raw_name, raw_profile in profile_data.items():
        profile_name: str = str(raw_name)
        if not isinstance(raw_profile, Mapping):
            raise ValueError(f"profile {profile_name!r} must be an object")
        profiles[profile_name] = ProfileBinding.from_mapping(profile_name, raw_profile)

    default_profile: str = raw_default_profile or "cheap-coder"
    if default_profile not in profiles:
        raise ValueError(f"default profile {default_profile!r} is not defined")

    return ProfileRegistry(profiles=profiles, default_profile=default_profile)


def describe_author_environment(
    *,
    registry: ProfileRegistry | None = None,
    site_capabilities: tuple[str, ...] = DEFAULT_SITE_CAPABILITIES,
) -> dict[str, Any]:
    """Return machine-readable author-facing vocabulary without auth details."""

    active_registry: ProfileRegistry = registry or load_profile_registry_from_env()
    return {
        "profiles": [
            {
                "name": profile.name,
                "capabilities": list(profile.capabilities),
                "budget_units": profile.budget_units,
            }
            for profile in sorted(active_registry.profiles.values(), key=lambda item: item.name)
        ],
        "roles": dict(DEFAULT_ROLE_CONVENTIONS),
        "router_default_policy": dict(DEFAULT_ROUTER_POLICY),
        "interpreter_env_default": active_registry.default_profile,
        "available_capabilities": sorted(site_capabilities),
    }
