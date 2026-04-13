"""Reproducer: lazy_ask does not reinstall inner handlers when evaluating lazy Programs.

When lazy_ask resolves an Ask whose env value is a Program, it evaluates
that Program by wrapping it with `WithHandler(handler, raw)` — reinstalling
only itself. The inner handlers (between lazy_ask and the Ask source) are
NOT reinstalled. Effects from the lazy Program that need those inner handlers
go unhandled.

Concrete scenario (nakagawa cllm_interpreter):

    lazy_ask(env)                    ← outer (workaround)
      writer / try / state
        _gcp_secret_handler          ← handles GetSecret
          lazy_ask(env)              ← inner (same env!)
            program                  ← Ask "token" → env value is Program[str] using GetSecret

Without the workaround (single lazy_ask):

    lazy_ask(env)
      _gcp_secret_handler
        program

1. program does Ask("token")
2. Ask propagates up through _gcp_secret_handler → lazy_ask catches it
3. lazy_ask evaluates the lazy Program (GetSecret-based)
4. GetSecret goes OUTSIDE lazy_ask — misses _gcp_secret_handler → unhandled!

Expected: lazy_ask should use GetHandlers(k) to capture and reinstall inner
handlers around the lazy Program evaluation, so GetSecret flows through
_gcp_secret_handler.
"""
from __future__ import annotations

from dataclasses import dataclass

import pytest

from doeff import (
    Ask,
    EffectBase,
    Pass,
    Resume,
    WithHandler,
    do,
    run,
)
from doeff_core_effects.handlers import lazy_ask, state, try_handler, writer
from doeff_core_effects.scheduler import scheduled


# --- Custom effects simulating GCP Secret Manager ---


@dataclass(frozen=True)
class GetSecret(EffectBase):
    """Effect: fetch a secret by name (simulates GCP Secret Manager)."""
    name: str


# --- Handlers ---


@do
def secret_handler(effect, k):
    """Handles GetSecret by returning a mock value. Simulates _gcp_secret_handler."""
    if not isinstance(effect, GetSecret):
        return (yield Pass(effect, k))
    return (yield Resume(k, f"secret:{effect.name}"))


# --- Lazy Program (simulates GetSecret-based env value) ---


@do
def _fetch_secret_program():
    """A Program that emits GetSecret — used as a lazy env value."""
    return (yield GetSecret("my-api-token"))


# --- Tests ---


class TestLazyAskInnerHandlerPropagation:
    """lazy_ask should reinstall inner handlers when evaluating lazy Programs."""

    def test_single_lazy_ask_with_inner_handler(self):
        """Single lazy_ask + inner secret_handler: lazy Program's GetSecret
        should be caught by secret_handler (between lazy_ask and program).

        Stack: lazy_ask → secret_handler → program
        Program Asks "token" → env value is Program[str] (GetSecret-based)
        GetSecret should propagate through secret_handler.
        """
        @do
        def program():
            return (yield Ask("token"))

        env = {"token": _fetch_secret_program()}

        composed = WithHandler(
            lazy_ask(env=env),
            WithHandler(
                secret_handler,
                program(),
            ),
        )
        result = run(scheduled(composed))
        assert result == "secret:my-api-token"

    def test_single_lazy_ask_inner_handler_with_full_stack(self):
        """Realistic stack: lazy_ask → writer → try → state → secret_handler → program.

        This mirrors the cllm_interpreter layout (minus the workaround dual lazy_ask).
        """
        @do
        def program():
            token = yield Ask("token")
            plain = yield Ask("plain_value")
            return f"{token}|{plain}"

        env = {
            "token": _fetch_secret_program(),
            "plain_value": "hello",
        }

        composed = WithHandler(
            lazy_ask(env=env),
            WithHandler(writer(),
                WithHandler(try_handler,
                    WithHandler(state(),
                        WithHandler(secret_handler,
                            program())))),
        )
        result = run(scheduled(composed))
        assert result == "secret:my-api-token|hello"

    def test_inner_handler_also_uses_ask(self):
        """Inner handler that itself uses Ask — its Ask should resolve via lazy_ask.

        Stack: lazy_ask → secret_handler_with_ask → program
        secret_handler_with_ask handles GetSecret but also Asks for "project_id".
        """
        @do
        def secret_handler_with_ask(effect, k):
            if not isinstance(effect, GetSecret):
                return (yield Pass(effect, k))
            project = yield Ask("project_id")
            return (yield Resume(k, f"secret:{project}:{effect.name}"))

        @do
        def program():
            return (yield Ask("token"))

        env = {
            "token": _fetch_secret_program(),
            "project_id": "my-project-123",
        }

        composed = WithHandler(
            lazy_ask(env=env),
            WithHandler(
                secret_handler_with_ask,
                program(),
            ),
        )
        result = run(scheduled(composed))
        assert result == "secret:my-project-123:my-api-token"

    def test_dual_lazy_ask_workaround_works(self):
        """Current workaround: dual lazy_ask with same env.

        This test documents the existing workaround and should pass.
        If the fix lands, both single and dual should pass.
        """
        @do
        def program():
            return (yield Ask("token"))

        env = {"token": _fetch_secret_program()}

        # Workaround: two lazy_asks with same env
        composed = WithHandler(
            lazy_ask(env=env),          # outer — catches secret_handler's Ask
            WithHandler(
                secret_handler,         # handles GetSecret from inner lazy_ask's Expand
                WithHandler(
                    lazy_ask(env=env),  # inner — catches program's Ask, Expands
                    program(),
                ),
            ),
        )
        result = run(scheduled(composed))
        assert result == "secret:my-api-token"
