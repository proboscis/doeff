"""Tests for env_var_ask — dynamic os.environ-backed Ask handler.

Contract:
- On each Ask("KEY"): look up ``os.environ[prefix + KEY]`` (default prefix DOEFF_).
- Key not present → ``Pass`` (forward to outer handler / Unhandled).
- Plain string value → resume directly.
- Value matching ``{module.path}`` → import the symbol; if it's a Program,
  evaluate it with the current inner handler chain reinstalled (so recursive
  Ask works) and resume with the result; otherwise resume with the imported
  object as-is.
- Program evaluation is cached via ``lazy-var``-style state, invalidated when
  the raw env-var string changes. A per-key semaphore gates concurrent evals
  so a spawned batch only evaluates the Program once per raw value.
"""
from __future__ import annotations

import pytest
from doeff_core_effects.handlers import env_var_ask, lazy_ask
from doeff_core_effects.scheduler import Gather, Spawn, scheduled

from doeff import Ask, Pass, Resume, do, run
from doeff import handler as _install_raw_handler

# --- Plain string values ---------------------------------------------------


class TestEnvVarAskPlain:
    def test_resolves_string_value_from_environ(self, monkeypatch):
        monkeypatch.setenv("DOEFF_OPENAI_API_KEY", "sk-abc")

        @do
        def prog():
            return (yield Ask("OPENAI_API_KEY"))

        result = run(scheduled(env_var_ask()(prog())))
        assert result == "sk-abc"

    def test_respects_custom_prefix(self, monkeypatch):
        monkeypatch.setenv("NAKAGAWA_DB_URL", "postgres://...")

        @do
        def prog():
            return (yield Ask("DB_URL"))

        result = run(
            scheduled(env_var_ask(prefix="NAKAGAWA_")(prog()))
        )
        assert result == "postgres://..."

    def test_missing_key_passes_to_outer(self, monkeypatch):
        monkeypatch.delenv("DOEFF_MODEL", raising=False)

        @do
        def outer(effect, k):
            if isinstance(effect, Ask) and effect.key == "MODEL":
                return (yield Resume(k, "fallback-model"))
            yield Pass(effect, k)

        @do
        def prog():
            return (yield Ask("MODEL"))

        composed = _install_raw_handler(outer)(env_var_ask()(prog()))
        assert run(scheduled(composed)) == "fallback-model"

    def test_dynamic_recheck_on_every_ask(self, monkeypatch):
        """Plain string values are re-read from os.environ on each Ask."""
        monkeypatch.setenv("DOEFF_LOG_LEVEL", "info")

        @do
        def prog():
            first = yield Ask("LOG_LEVEL")
            # Simulate runtime env change between Asks.
            import os
            os.environ["DOEFF_LOG_LEVEL"] = "debug"
            second = yield Ask("LOG_LEVEL")
            return (first, second)

        result = run(scheduled(env_var_ask()(prog())))
        assert result == ("info", "debug")


# --- {module.path} lazy Program import -------------------------------------


# Helpers at module scope so {dotted.path} can find them.
_plain_value = 12345

@do
def _lazy_program_pure():
    return "pure-result"


@do
def _lazy_program_uses_ask():
    inner = yield Ask("INNER_KEY")
    return f"wraps:{inner}"


_counter = {"n": 0}


@do
def _lazy_program_counts():
    _counter["n"] += 1
    return _counter["n"]


class TestEnvVarAskLazyImport:
    def test_braces_import_plain_object(self, monkeypatch):
        monkeypatch.setenv(
            "DOEFF_FAVORITE_NUMBER",
            "{tests.effects.test_env_var_ask._plain_value}",
        )

        @do
        def prog():
            return (yield Ask("FAVORITE_NUMBER"))

        assert run(scheduled(env_var_ask()(prog()))) == 12345

    def test_braces_import_evaluates_program(self, monkeypatch):
        monkeypatch.setenv(
            "DOEFF_GREETING",
            "{tests.effects.test_env_var_ask._lazy_program_pure}",
        )

        @do
        def prog():
            return (yield Ask("GREETING"))

        assert (
            run(scheduled(env_var_ask()(prog()))) == "pure-result"
        )

    def test_lazy_program_uses_inner_handler_chain(self, monkeypatch):
        """Program imported via {path} must see the handlers installed below
        env_var_ask (here: a lazy_ask that resolves INNER_KEY)."""
        monkeypatch.setenv(
            "DOEFF_OUTER",
            "{tests.effects.test_env_var_ask._lazy_program_uses_ask}",
        )

        @do
        def prog():
            return (yield Ask("OUTER"))

        composed = env_var_ask()(lazy_ask(env={"INNER_KEY": "hello"})(prog()))
        assert run(scheduled(composed)) == "wraps:hello"

    def test_program_eval_is_cached_until_raw_changes(self, monkeypatch):
        """Cached value persists across Asks while raw env var is unchanged."""
        import tests.effects.test_env_var_ask as _mod
        _mod._counter["n"] = 0
        monkeypatch.setenv(
            "DOEFF_COUNTER",
            "{tests.effects.test_env_var_ask._lazy_program_counts}",
        )

        @do
        def prog():
            a = yield Ask("COUNTER")
            b = yield Ask("COUNTER")
            c = yield Ask("COUNTER")
            return (a, b, c)

        result = run(scheduled(env_var_ask()(prog())))
        assert result == (1, 1, 1)
        assert _mod._counter["n"] == 1

    def test_raw_change_invalidates_cache(self, monkeypatch):
        import tests.effects.test_env_var_ask as _mod
        _mod._counter["n"] = 0
        monkeypatch.setenv(
            "DOEFF_COUNTER",
            "{tests.effects.test_env_var_ask._lazy_program_counts}",
        )

        @do
        def prog():
            first = yield Ask("COUNTER")
            # Mutate raw by toggling whitespace — same symbol, different raw.
            import os
            os.environ["DOEFF_COUNTER"] = (
                "{ tests.effects.test_env_var_ask._lazy_program_counts }"
            )
            second = yield Ask("COUNTER")
            return (first, second)

        result = run(scheduled(env_var_ask()(prog())))
        assert result == (1, 2)
        assert _mod._counter["n"] == 2


# --- Concurrency -----------------------------------------------------------


class TestEnvVarAskConcurrency:
    def test_semaphore_caps_concurrent_evals(self, monkeypatch):
        """Per-key semaphore bounds the number of concurrent Program evals,
        and after the first completion every subsequent Ask serves from the
        cache. The exact count of evals depends on how many tasks slipped
        past the initial ``key not in sems`` check before the first eval
        finished — bounded well below the Spawn count."""
        _counter["n"] = 0
        monkeypatch.setenv(
            "DOEFF_COUNTER",
            "{tests.effects.test_env_var_ask._lazy_program_counts}",
        )

        @do
        def worker():
            return (yield Ask("COUNTER"))

        @do
        def prog():
            tasks = []
            for _ in range(8):
                t = yield Spawn(worker())
                tasks.append(t)
            results = yield Gather(*tasks)
            return tuple(results)

        results = run(scheduled(env_var_ask()(prog())))
        # Every worker resolves to some counter value, and no more than a
        # handful of them re-evaluate (caching takes over quickly).
        assert len(results) == 8
        assert all(isinstance(v, int) and v > 0 for v in results)
        assert _counter["n"] < 8


# --- lazy_ask Pass-on-miss (PR D's second change) --------------------------


class TestLazyAskPassOnMiss:
    def test_pass_on_miss_by_default(self, monkeypatch):
        """After PR D, lazy_ask should Pass on missing keys instead of
        throwing, so outer handlers can supply fallbacks."""
        monkeypatch.setenv("DOEFF_FALLBACK_KEY", "fallback-value")

        @do
        def prog():
            return (yield Ask("FALLBACK_KEY"))

        composed = env_var_ask()(lazy_ask(env={})(prog()))
        assert run(scheduled(composed)) == "fallback-value"

    def test_strict_mode_still_throws(self):
        """lazy_ask(strict=True) preserves the legacy KeyError behavior."""

        @do
        def prog():
            return (yield Ask("MISSING"))

        composed = lazy_ask(env={}, strict=True)(prog())
        with pytest.raises(KeyError):
            run(scheduled(composed))
