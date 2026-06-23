"""Shell command helpers for tmux-launched agent processes."""

import shlex

FORBIDDEN_AGENT_ENV_KEYS = frozenset(
    {
        "ANTHROPIC_API_KEY",
        "ANTHROPIC_API_KEY_PERSONAL",
        "ANTHROPIC_API_KEY__PERSONAL",
    }
)


def _normalized_env_key(key: str) -> str:
    return key.replace("-", "_").upper()


def forbidden_agent_env_keys(env: dict[str, str] | None) -> list[str]:
    if not env:
        return []
    return sorted(key for key in env if _normalized_env_key(key) in FORBIDDEN_AGENT_ENV_KEYS)


def assert_no_forbidden_agent_env(
    env: dict[str, str] | None,
    *,
    context: str,
) -> None:
    forbidden = forbidden_agent_env_keys(env)
    if forbidden:
        joined = ", ".join(forbidden)
        raise ValueError(
            "doeff-agents must never pass Anthropic API keys to agent processes. "
            "API-key-backed calls are allowed only through memoized "
            "LLMStructuredQuery / StructuredLLMQuery handlers, never agent "
            f"session environments. Forbidden key(s) in {context}: {joined}"
        )


def wrap_with_shell_exports(command: str, env: dict[str, str] | None) -> str:
    if not env:
        return command
    assert_no_forbidden_agent_env(env, context="shell exports")
    exports = " ".join(f"export {key}={shlex.quote(value)};" for key, value in env.items())
    return f"{exports} {command}"
