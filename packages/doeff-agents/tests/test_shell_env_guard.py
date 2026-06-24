from __future__ import annotations

import pytest
from doeff_agents.shell import assert_no_forbidden_agent_env, forbidden_agent_env_keys


def test_forbidden_agent_env_rejects_provider_and_oauth_auth() -> None:
    env = {
        "CLAUDE_CODE_OAUTH_TOKEN": "token",
        "OPENAI_API_KEY": "provider-key",
        "safe_hint": "ok",
    }

    assert forbidden_agent_env_keys(env) == [
        "CLAUDE_CODE_OAUTH_TOKEN",
        "OPENAI_API_KEY",
    ]

    with pytest.raises(ValueError, match="OAuth-token environment auth"):
        assert_no_forbidden_agent_env(env, context="test")


def test_forbidden_agent_env_normalizes_legacy_anthropic_key_names() -> None:
    assert forbidden_agent_env_keys({"anthropic-api-key-personal": "secret"}) == [
        "anthropic-api-key-personal"
    ]

