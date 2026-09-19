"""Shell command helpers for tmux-launched agent processes."""

import functools
import shlex
from types import ModuleType
from typing import Any


def _policy() -> ModuleType:
    """sessionhost/policy.hy — the single home of the agent-boundary env vocabulary.

    Imported lazily inside the functions that need it, never at module import:
    the Hy module costs ~0.3s to compile and ``doeff_agents.shell`` is on the
    import path of every handler.  Same rule as
    ``assert_session_env_is_non_auth_overlay`` below.
    """
    import hy  # noqa: F401 -- installs the .hy import hook

    from doeff_agents.sessionhost import policy

    return policy


@functools.cache
def _forbidden_agent_env_keys() -> frozenset[str]:
    """The env names this layer refuses, named (not copied) from policy.

    The tmux launch layer is the widest of the three boundaries: on top of the
    provider keys it also refuses the routing spellings (which swap the
    provider without being a key) and the per-turn OAuth token — a turn
    credential rides the host's typed send path, never a shell ``export``.

    Do NOT flatten the three sets into one list here: admission and spawn
    deliberately carry TURN_AUTH (ADR-DOE-AGENTS-012 R5/R30), so the union
    belongs to this layer only (card acp:kanban-issue:ki-2a061da56ca9).
    """
    policy = _policy()
    return frozenset(
        set(policy.PROVIDER_AUTH_ENV_KEYS)
        | set(policy.PROVIDER_ROUTING_ENV_KEYS)
        | set(policy.TURN_AUTH_ENV_KEYS)
    )


def __getattr__(name: str) -> Any:
    """PEP 562: keep ``FORBIDDEN_AGENT_ENV_KEYS`` readable without paying the import.

    The spelling is quoted as evidence by ADR-DOE-AGENTS-004 R7, so the name
    stays even though the set now lives in policy.hy.
    """
    if name == "FORBIDDEN_AGENT_ENV_KEYS":
        return _forbidden_agent_env_keys()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def forbidden_agent_env_keys(env: dict[str, str] | None) -> list[str]:
    if not env:
        return []
    return _policy().env_offenders_against(dict(env), _forbidden_agent_env_keys())


def assert_no_forbidden_agent_env(
    env: dict[str, str] | None,
    *,
    context: str,
) -> None:
    forbidden = forbidden_agent_env_keys(env)
    if forbidden:
        joined = ", ".join(forbidden)
        raise ValueError(
            "doeff-agents must never pass provider API keys or OAuth-token "
            "environment auth to agent processes. API-key-backed calls are "
            "allowed only through memoized "
            "LLMStructuredQuery / StructuredLLMQuery handlers, never agent "
            f"session environments. Forbidden key(s) in {context}: {joined}"
        )


def assert_session_env_is_non_auth_overlay(
    env: dict[str, str] | None,
    *,
    context: str,
) -> None:
    """ADR-DOE-AGENTS-004 R9: session_env is a non-auth overlay.

    Binding-owned auth keys (CODEX_HOME / CLAUDE_CONFIG_DIR) may not ride
    the per-launch env dict — auth belongs to the handler binder
    (runtime policy locally, the typed ``binding`` field on the wire).
    The ownership set lives in ONE place, sessionhost/policy.hy, so the
    local guard and the host admission can never drift.
    """
    if not env:
        return
    offenders = _policy().overlay_env_offenders(dict(env))
    if offenders:
        joined = ", ".join(offenders)
        raise ValueError(
            "session_env is a non-auth overlay and may not carry "
            f"binding-owned auth env (offending: {joined}) in {context}. "
            "Inject auth through the handler binder instead — runtime "
            "policy (ClaudeRuntimePolicy / CodexRuntimePolicy) for local "
            "bindings, the typed `binding` field on the wire "
            "(ADR-DOE-AGENTS-004 R9)."
        )


def wrap_with_shell_exports(command: str, env: dict[str, str] | None) -> str:
    if not env:
        return command
    assert_no_forbidden_agent_env(env, context="shell exports")
    exports = " ".join(f"export {key}={shlex.quote(value)};" for key, value in env.items())
    return f"{exports} {command}"
