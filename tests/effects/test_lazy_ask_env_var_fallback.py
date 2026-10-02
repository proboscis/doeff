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

from collections.abc import Mapping

from doeff_core_effects.handlers import lazy_ask
from doeff_core_effects.scheduler import scheduled

from doeff import (
    Ask,
    Pass,
    Resume,
    do,
    run,
)
from doeff import handler as _install_raw_handler

# --- env_var_fallback_handler: resolves Ask from an outer source, passes otherwise ---
# The source stands in for os.environ: each test hands in the values it needs
# (injected, not read from the process environment — #2896).


def env_var_fallback_handler(source: Mapping[str, str]):
    """Build a handler that answers Ask from ``source`` and passes if the key is not there.

    The source names its values by string (as os.environ does), so an Ask whose key is not a
    string is passed on.
    """

    @do
    def handle(effect, k):
        if isinstance(effect, Ask) and isinstance(effect.key, str):
            val = source.get(effect.key)
            if val is not None:
                return (yield Resume(k, val))
        yield Pass(effect, k)

    return handle


# --- Tests ---


class TestLazyAskEnvVarFallback:
    """#390: lazy_ask should include inner Ask-resolving handlers during
    lazy Program evaluation."""

    def test_lazy_program_ask_resolved_by_inner_env_handler(self):
        """Core scenario from #390: lazy Program does Ask("path_key"),
        env_var_fallback_handler resolves it from os.environ."""
        source = {"path_key": "/etc/secrets/creds.json"}

        @do
        def some_program():
            path = yield Ask("path_key")
            return f"Credentials({path})"

        @do
        def program():
            return (yield Ask("creds"))

        env = {"creds": some_program()}

        composed = lazy_ask(env=env)(_install_raw_handler(env_var_fallback_handler(source))(program()))
        result = run(scheduled(composed))
        assert result == "Credentials(/etc/secrets/creds.json)"

    def test_lazy_program_ask_falls_through_to_lazy_ask_env(self):
        """Ask inside lazy Program: env_var_fallback doesn't have the key,
        but lazy_ask's own env does. Should resolve from lazy_ask."""
        source: dict[str, str] = {}  # the outer source does not have project_id

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

        composed = lazy_ask(env=env)(_install_raw_handler(env_var_fallback_handler(source))(program()))
        result = run(scheduled(composed))
        assert result == "project=my-project-123"

    def test_lazy_program_ask_prefers_inner_handler_over_lazy_ask(self):
        """When both env_var_fallback (os.environ) and lazy_ask's env have the
        key, the inner handler (env_var_fallback) should resolve it first
        because it's closer to the Ask source."""
        source = {"api_url": "https://env.example.com"}

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

        composed = lazy_ask(env=env)(_install_raw_handler(env_var_fallback_handler(source))(program()))
        result = run(scheduled(composed))
        # Inner handler (env_var_fallback) is closer -> resolves from os.environ
        assert result == "url=https://env.example.com"

    def test_recursive_lazy_with_env_fallback(self):
        """Recursive lazy chain where intermediate Program uses Ask resolved
        by env_var_fallback_handler.

        env:
          "creds" -> Program that Asks "path_key"
          "path_key" -> Program that Asks "base_dir" (from os.environ)
        """
        source = {"base_dir": "/opt/secrets"}

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

        composed = lazy_ask(env=env)(_install_raw_handler(env_var_fallback_handler(source))(program()))
        result = run(scheduled(composed))
        assert result == "Credentials(/opt/secrets/creds.json)"

    def test_env_fallback_with_full_handler_stack(self):
        """Full realistic stack: lazy_ask -> writer -> try -> state ->
        env_var_fallback -> program.

        Mirrors cllm_interpreter layout (minus the workaround dual lazy_ask).
        """
        from doeff_core_effects.handlers import state, try_handler, writer

        source = {"secret_path": "/run/secrets/api-key"}

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

        composed = lazy_ask(env=env)(writer(try_handler(state()(_install_raw_handler(env_var_fallback_handler(source))(program())))))
        result = run(scheduled(composed))
        assert result == "loaded:/run/secrets/api-key|test-service"
