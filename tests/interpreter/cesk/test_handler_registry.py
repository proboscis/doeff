"""Tests for CESK effect handler extensibility.

Tests the handler registry, dispatch strategy, handler composition,
and MRO-based dispatch as specified in ISSUE-CORE-426.
"""

from dataclasses import dataclass
from typing import Any

import pytest

from doeff._types_internal import EffectBase
from doeff.cesk import (
    AsyncEffectHandler,
    EffectDispatcher,
    EffectfulHandlers,
    Environment,
    HandlerRegistryError,
    PureHandlers,
    Store,
    SyncEffectHandler,
    default_effectful_handlers,
    default_pure_handlers,
    run_sync,
    wrap_async_handler,
    wrap_sync_handler,
)
from doeff.do import do
from doeff.effects.state import StateGetEffect

# ============================================================================
# Custom Effect Types for Testing
# ============================================================================


@dataclass(frozen=True)
class CustomPureEffect(EffectBase):
    """Custom pure effect for testing user-defined effects."""
    value: int

    def intercept(self, transform):
        """Return self since this is a data-only effect."""
        return self


@dataclass(frozen=True)
class CustomEffectfulEffect(EffectBase):
    """Custom effectful effect for testing user-defined effects."""
    value: int

    def intercept(self, transform):
        """Return self since this is a data-only effect."""
        return self


@dataclass(frozen=True)
class BaseEffect(EffectBase):
    """Base effect for MRO dispatch testing."""
    value: int

    def intercept(self, transform):
        """Return self since this is a data-only effect."""
        return self


@dataclass(frozen=True)
class DerivedEffect(BaseEffect):
    """Derived effect to test MRO fallback dispatch."""


# ============================================================================
# Test Default Registries
# ============================================================================


class TestDefaultRegistries:
    """Tests for default_pure_handlers and default_effectful_handlers."""

    def test_default_pure_handlers_returns_dict(self):
        handlers = default_pure_handlers()
        assert isinstance(handlers, dict)
        assert len(handlers) > 0

    def test_default_pure_handlers_contains_state_effects(self):
        from doeff.effects import StateGetEffect, StateModifyEffect, StatePutEffect
        handlers = default_pure_handlers()
        assert StateGetEffect in handlers
        assert StatePutEffect in handlers
        assert StateModifyEffect in handlers

    def test_default_pure_handlers_contains_reader_effects(self):
        from doeff.effects import AskEffect
        handlers = default_pure_handlers()
        assert AskEffect in handlers

    def test_default_effectful_handlers_returns_dict(self):
        handlers = default_effectful_handlers()
        assert isinstance(handlers, dict)
        assert len(handlers) > 0

    def test_default_effectful_handlers_contains_io_effects(self):
        from doeff.effects import IOPerformEffect, IOPrintEffect
        handlers = default_effectful_handlers()
        assert IOPerformEffect in handlers
        assert IOPrintEffect in handlers


# ============================================================================
# Test Custom Pure Effect Handlers
# ============================================================================


class TestCustomPureEffectHandlers:
    """Tests for registering custom pure effect handlers."""

    def test_custom_pure_effect_without_cesk_modification(self):
        """User can add custom pure effect without modifying CESK core."""

        def handle_custom_pure(
            effect: CustomPureEffect, env: Environment, store: Store
        ) -> tuple[Any, Store]:
            return (effect.value * 2, store)

        @do
        def program():
            result = yield CustomPureEffect(21)
            return result

        pure_handlers: PureHandlers = {CustomPureEffect: handle_custom_pure}
        result = run_sync(program(), pure_handlers=pure_handlers)

        assert result.is_ok
        assert result.value == 42

    def test_custom_pure_effect_can_modify_store(self):
        """Custom pure handler can modify the store."""

        def handle_custom_pure(
            effect: CustomPureEffect, env: Environment, store: Store
        ) -> tuple[Any, Store]:
            new_store = {**store, "custom_key": effect.value}
            return (effect.value, new_store)

        @do
        def program():
            from doeff.effects.state import get
            yield CustomPureEffect(42)
            value = yield get("custom_key")
            return value

        pure_handlers: PureHandlers = {CustomPureEffect: handle_custom_pure}
        result = run_sync(program(), pure_handlers=pure_handlers)

        assert result.is_ok
        assert result.value == 42

    def test_custom_pure_effect_can_access_env(self):
        """Custom pure handler can read from environment."""

        def handle_custom_pure(
            effect: CustomPureEffect, env: Environment, store: Store
        ) -> tuple[Any, Store]:
            multiplier = env.get("multiplier", 1)
            return (effect.value * multiplier, store)

        @do
        def program():
            result = yield CustomPureEffect(21)
            return result

        pure_handlers: PureHandlers = {CustomPureEffect: handle_custom_pure}
        result = run_sync(
            program(),
            env={"multiplier": 2},
            pure_handlers=pure_handlers,
        )

        assert result.is_ok
        assert result.value == 42


# ============================================================================
# Test Custom Effectful Effect Handlers
# ============================================================================


class TestCustomEffectfulEffectHandlers:
    """Tests for registering custom effectful effect handlers."""

    def test_custom_effectful_effect_without_cesk_modification(self):
        """User can add custom effectful effect without modifying CESK core."""

        async def handle_custom_effectful(
            effect: CustomEffectfulEffect, env: Environment, store: Store
        ) -> tuple[Any, Store]:
            # Simulate async operation
            return (effect.value * 3, store)

        @do
        def program():
            result = yield CustomEffectfulEffect(14)
            return result

        effectful_handlers: EffectfulHandlers = {
            CustomEffectfulEffect: handle_custom_effectful
        }
        result = run_sync(program(), effectful_handlers=effectful_handlers)

        assert result.is_ok
        assert result.value == 42


# ============================================================================
# Test MRO-based Dispatch
# ============================================================================


class TestMRODispatch:
    """Tests for MRO-based fallback dispatch."""

    def test_exact_type_match_takes_priority(self):
        """Exact type match is used before MRO fallback."""
        call_log = []

        def handle_derived(
            effect: DerivedEffect, env: Environment, store: Store
        ) -> tuple[Any, Store]:
            call_log.append("derived")
            return (effect.value * 2, store)

        def handle_base(
            effect: BaseEffect, env: Environment, store: Store
        ) -> tuple[Any, Store]:
            call_log.append("base")
            return (effect.value * 3, store)

        @do
        def program():
            result = yield DerivedEffect(10)
            return result

        pure_handlers: PureHandlers = {
            DerivedEffect: handle_derived,
            BaseEffect: handle_base,
        }
        result = run_sync(program(), pure_handlers=pure_handlers)

        assert result.is_ok
        assert result.value == 20  # Derived handler used (10 * 2)
        assert call_log == ["derived"]

    def test_mro_fallback_to_base_class(self):
        """When no exact match, falls back to base class handler."""
        call_log = []

        def handle_base(
            effect: BaseEffect, env: Environment, store: Store
        ) -> tuple[Any, Store]:
            call_log.append("base")
            return (effect.value * 3, store)

        @do
        def program():
            result = yield DerivedEffect(14)
            return result

        # Only base handler registered, derived should use it via MRO
        pure_handlers: PureHandlers = {BaseEffect: handle_base}
        result = run_sync(program(), pure_handlers=pure_handlers)

        assert result.is_ok
        assert result.value == 42  # Base handler used (14 * 3)
        assert call_log == ["base"]

    def test_user_registry_checked_before_builtin(self):
        """User registry is checked before built-in registry at each MRO level."""

        def custom_handle_base(
            effect: BaseEffect, env: Environment, store: Store
        ) -> tuple[Any, Store]:
            return (effect.value * 10, store)

        @do
        def program():
            result = yield DerivedEffect(4)
            return result

        pure_handlers: PureHandlers = {BaseEffect: custom_handle_base}
        result = run_sync(program(), pure_handlers=pure_handlers)

        assert result.is_ok
        assert result.value == 40


# ============================================================================
# Test Effect Dispatcher Caching
# ============================================================================


class TestEffectDispatcherCaching:
    """Tests for dispatcher caching behavior."""

    def test_resolved_handler_is_cached(self):
        """Resolved handler for a type is cached for O(1) subsequent dispatch."""
        dispatcher = EffectDispatcher(
            user_pure={BaseEffect: lambda e, env, s: (e.value, s)},
        )

        # First lookup
        handler1 = dispatcher._lookup_pure(DerivedEffect)
        # Second lookup should be from cache
        handler2 = dispatcher._lookup_pure(DerivedEffect)

        assert handler1 is handler2
        assert DerivedEffect in dispatcher._pure_cache


# ============================================================================
# Test Handler Wrapping
# ============================================================================


class TestHandlerWrapping:
    """Tests for handler wrapping utilities."""

    def test_wrap_sync_handler_basic(self):
        """wrap_sync_handler enables aspect-style behavior."""
        call_log = []

        def original_handler(
            effect: CustomPureEffect, env: Environment, store: Store
        ) -> tuple[Any, Store]:
            call_log.append("original")
            return (effect.value, store)

        def wrapper(
            effect: CustomPureEffect,
            env: Environment,
            store: Store,
            next_handler: SyncEffectHandler,
        ) -> tuple[Any, Store]:
            call_log.append("before")
            result, new_store = next_handler(effect, env, store)
            call_log.append("after")
            return (result * 2, new_store)

        wrapped = wrap_sync_handler(original_handler, wrapper)

        @do
        def program():
            result = yield CustomPureEffect(21)
            return result

        pure_handlers: PureHandlers = {CustomPureEffect: wrapped}
        result = run_sync(program(), pure_handlers=pure_handlers)

        assert result.is_ok
        assert result.value == 42
        assert call_log == ["before", "original", "after"]

    def test_wrap_sync_handler_audit_logging(self):
        """Handler wrapping can implement audit logging."""
        audit_log = []

        def audit_wrapper(
            effect: CustomPureEffect,
            env: Environment,
            store: Store,
            next_handler: SyncEffectHandler,
        ) -> tuple[Any, Store]:
            audit_log.append(f"Handling {type(effect).__name__} with value={effect.value}")
            result, new_store = next_handler(effect, env, store)
            audit_log.append(f"Completed with result={result}")
            return result, new_store

        def handler(
            effect: CustomPureEffect, env: Environment, store: Store
        ) -> tuple[Any, Store]:
            return (effect.value * 2, store)

        wrapped = wrap_sync_handler(handler, audit_wrapper)

        @do
        def program():
            yield CustomPureEffect(10)
            yield CustomPureEffect(20)
            return "done"

        pure_handlers: PureHandlers = {CustomPureEffect: wrapped}
        result = run_sync(program(), pure_handlers=pure_handlers)

        assert result.is_ok
        assert len(audit_log) == 4
        assert "CustomPureEffect" in audit_log[0]

    def test_wrap_async_handler_basic(self):
        """wrap_async_handler enables aspect-style behavior for async handlers."""
        call_log = []

        async def original_handler(
            effect: CustomEffectfulEffect, env: Environment, store: Store
        ) -> tuple[Any, Store]:
            call_log.append("original")
            return (effect.value, store)

        async def wrapper(
            effect: CustomEffectfulEffect,
            env: Environment,
            store: Store,
            next_handler: AsyncEffectHandler,
        ) -> tuple[Any, Store]:
            call_log.append("before")
            result, new_store = await next_handler(effect, env, store)
            call_log.append("after")
            return (result * 2, new_store)

        wrapped = wrap_async_handler(original_handler, wrapper)

        @do
        def program():
            result = yield CustomEffectfulEffect(21)
            return result

        effectful_handlers: EffectfulHandlers = {CustomEffectfulEffect: wrapped}
        result = run_sync(program(), effectful_handlers=effectful_handlers)

        assert result.is_ok
        assert result.value == 42
        assert call_log == ["before", "original", "after"]


# ============================================================================
# Test Override Built-ins
# ============================================================================


class TestOverrideBuiltins:
    """Tests for override_builtins flag."""

    def test_cannot_override_builtin_without_flag(self):
        """Overriding built-in handlers requires override_builtins=True."""

        def custom_get_handler(
            effect: StateGetEffect, env: Environment, store: Store
        ) -> tuple[Any, Store]:
            return ("custom_value", store)

        @do
        def program():
            result = yield StateGetEffect("key")
            return result

        pure_handlers: PureHandlers = {StateGetEffect: custom_get_handler}

        with pytest.raises(HandlerRegistryError) as exc_info:
            run_sync(program(), pure_handlers=pure_handlers)

        assert "override_builtins=True" in str(exc_info.value)

    def test_can_override_builtin_with_flag(self):
        """Built-in handlers can be overridden with override_builtins=True."""

        def custom_get_handler(
            effect: StateGetEffect, env: Environment, store: Store
        ) -> tuple[Any, Store]:
            return ("custom_value", store)

        @do
        def program():
            result = yield StateGetEffect("key")
            return result

        pure_handlers: PureHandlers = {StateGetEffect: custom_get_handler}
        result = run_sync(
            program(),
            pure_handlers=pure_handlers,
            override_builtins=True,
        )

        assert result.is_ok
        assert result.value == "custom_value"

    def test_wrap_builtin_handler(self):
        """Can wrap built-in handlers with override_builtins=True."""

        call_count = [0]
        builtin_handlers = default_pure_handlers()
        original_handler = builtin_handlers[StateGetEffect]

        def counting_wrapper(
            effect: StateGetEffect,
            env: Environment,
            store: Store,
            next_handler: SyncEffectHandler,
        ) -> tuple[Any, Store]:
            call_count[0] += 1
            return next_handler(effect, env, store)

        wrapped = wrap_sync_handler(original_handler, counting_wrapper)

        @do
        def program():
            from doeff.effects.state import get, put
            yield put("key", "value")
            result = yield get("key")
            return result

        pure_handlers: PureHandlers = {StateGetEffect: wrapped}
        result = run_sync(
            program(),
            pure_handlers=pure_handlers,
            override_builtins=True,
        )

        assert result.is_ok
        assert result.value == "value"
        assert call_count[0] == 1


# ============================================================================
# Test Conflict Detection
# ============================================================================


class TestConflictDetection:
    """Tests for handler registration conflict detection."""

    def test_effect_cannot_be_in_both_registries(self):
        """An effect type cannot be in both pure and effectful registries."""

        def sync_handler(e, env, s):
            return (42, s)

        async def async_handler(e, env, s):
            return (42, s)

        pure_handlers: PureHandlers = {CustomPureEffect: sync_handler}
        effectful_handlers: EffectfulHandlers = {CustomPureEffect: async_handler}

        @do
        def program():
            return 42

        with pytest.raises(HandlerRegistryError) as exc_info:
            run_sync(
                program(),
                pure_handlers=pure_handlers,
                effectful_handlers=effectful_handlers,
            )

        assert "cannot be in both" in str(exc_info.value).lower()

    def test_cannot_change_builtin_category_without_flag(self):
        """Cannot register built-in pure effect as effectful without override flag."""

        async def async_handler(e, env, s):
            return (42, s)

        effectful_handlers: EffectfulHandlers = {StateGetEffect: async_handler}

        @do
        def program():
            return 42

        with pytest.raises(HandlerRegistryError):
            run_sync(program(), effectful_handlers=effectful_handlers)


# ============================================================================
# Test Integration with Existing CESK Features
# ============================================================================


class TestIntegrationWithCESK:
    """Tests that extensibility integrates with existing CESK features."""

    def test_custom_effect_works_with_catch(self):
        """Custom effects work with error handling."""
        from doeff.effects.result import catch

        def failing_handler(
            effect: CustomPureEffect, env: Environment, store: Store
        ) -> tuple[Any, Store]:
            raise ValueError("Handler failed")

        @do
        def inner():
            result = yield CustomPureEffect(42)
            return result

        @do
        def program():
            result = yield catch(inner(), lambda e: "recovered")
            return result

        pure_handlers: PureHandlers = {CustomPureEffect: failing_handler}
        result = run_sync(program(), pure_handlers=pure_handlers)

        assert result.is_ok
        assert result.value == "recovered"

    def test_custom_effect_works_with_local(self):
        """Custom effects can access locally modified environment."""
        from doeff.effects.reader import local

        def handler(
            effect: CustomPureEffect, env: Environment, store: Store
        ) -> tuple[Any, Store]:
            prefix = env.get("prefix", "")
            return (f"{prefix}{effect.value}", store)

        @do
        def inner():
            result = yield CustomPureEffect(42)
            return result

        @do
        def program():
            result = yield local({"prefix": "value:"}, inner())
            return result

        pure_handlers: PureHandlers = {CustomPureEffect: handler}
        result = run_sync(program(), pure_handlers=pure_handlers)

        assert result.is_ok
        assert result.value == "value:42"


# ============================================================================
# Test Backward Compatibility
# ============================================================================


class TestBackwardCompatibility:
    """Tests that existing code works without changes."""

    def test_existing_programs_work_without_handlers(self):
        """Programs using built-in effects work without explicit handler registration."""
        from doeff.effects.state import get, put

        @do
        def program():
            yield put("key", 42)
            value = yield get("key")
            return value

        result = run_sync(program())

        assert result.is_ok
        assert result.value == 42

    def test_run_sync_backward_compatible(self):
        """run_sync accepts same arguments as before."""
        from doeff.effects.reader import ask

        @do
        def program():
            value = yield ask("key")
            return value * 2

        # Old-style call with positional args
        result = run_sync(program(), {"key": 21})

        assert result.is_ok
        assert result.value == 42
