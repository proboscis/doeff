"""Custom Effect Handlers with CESK Interpreter.

This example demonstrates how to extend the CESK interpreter with custom effects
and handlers without modifying the core interpreter code.

Key concepts:
- Define custom effect types by subclassing EffectBase
- Register handlers in pure_handlers or effectful_handlers registries
- Use wrap_sync_handler/wrap_async_handler for handler composition (logging, metrics)
- Use override_builtins=True to replace built-in handlers

Run with: uv run python examples/custom_handlers.py
"""

from dataclasses import dataclass
from typing import Any

from doeff._types_internal import EffectBase
from doeff.cesk import (
    Environment,
    Store,
    default_pure_handlers,
    run_sync,
    wrap_sync_handler,
)
from doeff.do import do


# ============================================================================
# Step 1: Define Custom Effect Types
# ============================================================================


@dataclass(frozen=True)
class CacheGetEffect(EffectBase):
    """Custom effect: get value from an in-memory cache."""

    key: str

    def intercept(self, transform):
        """Effects must implement intercept - return self for data-only effects."""
        return self


@dataclass(frozen=True)
class CachePutEffect(EffectBase):
    """Custom effect: put value into an in-memory cache."""

    key: str
    value: Any

    def intercept(self, transform):
        return self


@dataclass(frozen=True)
class MetricsIncrementEffect(EffectBase):
    """Custom effect: increment a metrics counter."""

    metric_name: str
    amount: int = 1

    def intercept(self, transform):
        return self


# ============================================================================
# Step 2: Define Effect Constructors (convenience functions)
# ============================================================================


def cache_get(key: str) -> CacheGetEffect:
    """Get a value from the cache."""
    return CacheGetEffect(key=key)


def cache_put(key: str, value: Any) -> CachePutEffect:
    """Put a value into the cache."""
    return CachePutEffect(key=key, value=value)


def metrics_incr(name: str, amount: int = 1) -> MetricsIncrementEffect:
    """Increment a metrics counter."""
    return MetricsIncrementEffect(metric_name=name, amount=amount)


# ============================================================================
# Step 3: Define Handlers
# ============================================================================


def handle_cache_get(effect: CacheGetEffect, env: Environment, store: Store) -> tuple[Any, Store]:
    """Handler for CacheGetEffect - retrieves from __cache__ in store."""
    cache = store.get("__cache__", {})
    value = cache.get(effect.key)
    return (value, store)


def handle_cache_put(effect: CachePutEffect, env: Environment, store: Store) -> tuple[Any, Store]:
    """Handler for CachePutEffect - stores in __cache__ in store."""
    cache = store.get("__cache__", {})
    new_cache = {**cache, effect.key: effect.value}
    new_store = {**store, "__cache__": new_cache}
    return (None, new_store)


def handle_metrics_increment(
    effect: MetricsIncrementEffect, env: Environment, store: Store
) -> tuple[Any, Store]:
    """Handler for MetricsIncrementEffect - updates __metrics__ in store."""
    metrics = store.get("__metrics__", {})
    current = metrics.get(effect.metric_name, 0)
    new_metrics = {**metrics, effect.metric_name: current + effect.amount}
    new_store = {**store, "__metrics__": new_metrics}
    return (None, new_store)


# ============================================================================
# Step 4: Create Handler Registry
# ============================================================================


def my_custom_handlers():
    """Create a registry with all custom handlers."""
    return {
        CacheGetEffect: handle_cache_get,
        CachePutEffect: handle_cache_put,
        MetricsIncrementEffect: handle_metrics_increment,
    }


# ============================================================================
# Step 5: Use Custom Effects in Programs
# ============================================================================


@do
def example_program():
    """A program that uses custom cache and metrics effects."""
    # Use custom cache effects
    yield cache_put("user:123", {"name": "Alice", "role": "admin"})
    yield metrics_incr("cache.writes")

    user = yield cache_get("user:123")
    yield metrics_incr("cache.reads")

    # Return the user data
    return user


def main():
    """Run the example program with custom handlers."""
    # Run with custom handlers
    result = run_sync(
        example_program(),
        pure_handlers=my_custom_handlers(),  # Register custom handlers
    )

    print(f"Result: {result.value}")
    # Output: Result: {'name': 'Alice', 'role': 'admin'}


# ============================================================================
# Advanced: Handler Wrapping (Logging, Metrics, etc.)
# ============================================================================


def logging_wrapper(effect, env, store, next_handler):
    """Aspect-style wrapper that adds logging around handler calls.

    Args:
        effect: The effect being handled
        env: The environment
        store: The store
        next_handler: The wrapped handler to call
    """
    print(f"[LOG] Handling effect: {type(effect).__name__}")
    result, new_store = next_handler(effect, env, store)
    print(f"[LOG] Effect handled, result: {result}")
    return result, new_store


@do
def example_with_logging():
    """Example using wrapped handlers for logging."""
    yield cache_put("key", "value")
    value = yield cache_get("key")
    return value


def main_with_logging():
    """Run with logging-wrapped handlers."""
    # Wrap existing handlers with logging
    logged_cache_get = wrap_sync_handler(handle_cache_get, logging_wrapper)
    logged_cache_put = wrap_sync_handler(handle_cache_put, logging_wrapper)

    # Create registry with wrapped handlers
    logged_handlers = {
        CacheGetEffect: logged_cache_get,
        CachePutEffect: logged_cache_put,
    }

    result = run_sync(
        example_with_logging(),
        pure_handlers=logged_handlers,
    )

    print(f"Final result: {result.value}")


# ============================================================================
# Advanced: Overriding Built-in Handlers
# ============================================================================


@do
def program_with_builtin_effects():
    """Program using built-in State effects."""
    from doeff.effects import get, put

    yield put("counter", 0)
    yield put("counter", 1)
    count = yield get("counter")
    return count


def custom_state_get_with_logging(effect, env, store):
    """Override built-in StateGetEffect with logging."""
    from doeff.effects.state import StateGetEffect

    print(f"[AUDIT] Reading state key: {effect.key}")
    value = store.get(effect.key)
    return (value, store)


def main_override_builtin():
    """Override built-in handlers (requires override_builtins=True)."""
    from doeff.effects.state import StateGetEffect

    result = run_sync(
        program_with_builtin_effects(),
        pure_handlers={
            StateGetEffect: custom_state_get_with_logging,
        },
        override_builtins=True,  # Required to override built-in handlers!
    )

    print(f"Result: {result.value}")


if __name__ == "__main__":
    print("=== Basic Custom Handlers ===")
    main()

    print("\n=== Handlers with Logging Wrapper ===")
    main_with_logging()

    print("\n=== Override Built-in Handlers ===")
    main_override_builtin()
