"""Reproducer for #390: lazy_ask should include inner Ask-resolving handlers.

When lazy_ask resolves an env value that is a Program, the Program's effects
should propagate through inner handlers — including handlers that resolve Ask
from os.environ (or any other fallback source).

Key difference from test_lazy_ask_inner_handler_propagation.py:
  Those tests use inner handlers for DIFFERENT effect types (GetSecret, Transform).
  This file tests inner handlers that handle the SAME effect type (Ask) as lazy_ask.

Scenario from the issue:

    lazy_ask(env={"creds": some_program})    <- outermost
      env_var_fallback_handler               <- resolves Ask from os.environ
        program

    some_program = @do def(): yield Ask("path_key")  # expects os.environ

When program does Ask("creds"):
  1. env_var_fallback_handler passes (no "creds" in os.environ)
  2. lazy_ask catches Ask("creds"), finds Program in env
  3. lazy_ask evaluates the lazy Program with inner handlers reinstalled
  4. Inside the Program, Ask("path_key") should reach env_var_fallback_handler
  5. env_var_fallback_handler resolves from os.environ -> done
"""
from __future__ import annotations

import os

import pytest

from doeff import (
    Ask,
    Pass,
    Resume,
    WithHandler,
    do,
    run,
)
from doeff_core_effects.handlers import lazy_ask
from doeff_core_effects.scheduler import scheduled


# --- env_var_fallback_handler: resolves Ask from os.environ, passes otherwise ---


@do
def env_var_fallback_handler(effect, k):
    """Handles Ask by looking up os.environ; passes if key not found."""
    if isinstance(effect, Ask):
        val = os.environ.get(effect.key)
        if val is not None:
            return (yield Resume(k, val))
    yield Pass(effect, k)


# --- Tests ---


class TestLazyAskEnvVarFallback:
    """#390: lazy_ask should include inner Ask-resolving handlers during
    lazy Program evaluation."""

    def test_lazy_program_ask_resolved_by_inner_env_handler(self, monkeypatch):
        """Core scenario from #390: lazy Program does Ask("path_key"),
        env_var_fallback_handler resolves it from os.environ."""
        monkeypatch.setenv("path_key", "/etc/secrets/creds.json")

        @do
        def some_program():
            path = yield Ask("path_key")
            return f"Credentials({path})"

        @do
        def program():
            return (yield Ask("creds"))

        env = {"creds": some_program()}

        composed = WithHandler(
            lazy_ask(env=env),
            WithHandler(
                env_var_fallback_handler,
                program(),
            ),
        )
        result = run(scheduled(composed))
        assert result == "Credentials(/etc/secrets/creds.json)"

    def test_lazy_program_ask_falls_through_to_lazy_ask_env(self, monkeypatch):
        """Ask inside lazy Program: env_var_fallback doesn't have the key,
        but lazy_ask's own env does. Should resolve from lazy_ask."""
        monkeypatch.delenv("project_id", raising=False)

        @do
        def some_program():
            project = yield Ask("project_id")
            return f"project={project}"

        @do
        def program():
            return (yield Ask("creds"))

        env = {
            "creds": some_program(),
            "project_id": "my-project-123",
        }

        composed = WithHandler(
            lazy_ask(env=env),
            WithHandler(
                env_var_fallback_handler,
                program(),
            ),
        )
        result = run(scheduled(composed))
        assert result == "project=my-project-123"

    def test_lazy_program_ask_prefers_inner_handler_over_lazy_ask(self, monkeypatch):
        """When both env_var_fallback (os.environ) and lazy_ask's env have the
        key, the inner handler (env_var_fallback) should resolve it first
        because it's closer to the Ask source."""
        monkeypatch.setenv("api_url", "https://env.example.com")

        @do
        def some_program():
            url = yield Ask("api_url")
            return f"url={url}"

        @do
        def program():
            return (yield Ask("creds"))

        env = {
            "creds": some_program(),
            "api_url": "https://lazy-ask.example.com",
        }

        composed = WithHandler(
            lazy_ask(env=env),
            WithHandler(
                env_var_fallback_handler,
                program(),
            ),
        )
        result = run(scheduled(composed))
        # Inner handler (env_var_fallback) is closer -> resolves from os.environ
        assert result == "url=https://env.example.com"

    def test_recursive_lazy_with_env_fallback(self, monkeypatch):
        """Recursive lazy chain where intermediate Program uses Ask resolved
        by env_var_fallback_handler.

        env:
          "creds" -> Program that Asks "path_key"
          "path_key" -> Program that Asks "base_dir" (from os.environ)
        """
        monkeypatch.setenv("base_dir", "/opt/secrets")

        @do
        def lazy_path():
            base = yield Ask("base_dir")
            return f"{base}/creds.json"

        @do
        def lazy_creds():
            path = yield Ask("path_key")
            return f"Credentials({path})"

        @do
        def program():
            return (yield Ask("creds"))

        env = {
            "creds": lazy_creds(),
            "path_key": lazy_path(),
        }

        composed = WithHandler(
            lazy_ask(env=env),
            WithHandler(
                env_var_fallback_handler,
                program(),
            ),
        )
        result = run(scheduled(composed))
        assert result == "Credentials(/opt/secrets/creds.json)"

    def test_env_fallback_with_full_handler_stack(self, monkeypatch):
        """Full realistic stack: lazy_ask -> writer -> try -> state ->
        env_var_fallback -> program.

        Mirrors cllm_interpreter layout (minus the workaround dual lazy_ask).
        """
        from doeff_core_effects.handlers import state, try_handler, writer

        monkeypatch.setenv("secret_path", "/run/secrets/api-key")

        @do
        def some_program():
            path = yield Ask("secret_path")
            return f"loaded:{path}"

        @do
        def program():
            result = yield Ask("config")
            plain = yield Ask("name")
            return f"{result}|{plain}"

        env = {
            "config": some_program(),
            "name": "test-service",
        }

        composed = WithHandler(
            lazy_ask(env=env),
            WithHandler(writer(),
                WithHandler(try_handler,
                    WithHandler(state(),
                        WithHandler(env_var_fallback_handler,
                            program())))),
        )
        result = run(scheduled(composed))
        assert result == "loaded:/run/secrets/api-key|test-service"
