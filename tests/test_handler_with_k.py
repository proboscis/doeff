"""Tests for the new handler protocol with continuation (k) parameter.

This tests the algebraic effects-style handler protocol where handlers
receive the continuation and decide how to use it.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Callable

import pytest
from doeff._vendor import FrozenDict
from doeff.cesk import (
    Resume,
    Suspend,
    Next,
    Continuation,
    EffectHandlerWithK,
    wrap_legacy_sync_handler,
    wrap_legacy_async_handler,
    CESKState,
    Value,
    Environment,
    Store,
)
from doeff._types_internal import EffectBase
from dataclasses import dataclass

if TYPE_CHECKING:
    from doeff.types import Effect, Program


# ============================================================================
# Test Effects
# ============================================================================


@dataclass(frozen=True)
class TestGetEffect(EffectBase):
    """Test effect: get a value from store."""
    key: str

    def intercept(
        self, transform: Callable[[Effect], Effect | Program]
    ) -> TestGetEffect:
        return self


@dataclass(frozen=True)
class TestPutEffect(EffectBase):
    """Test effect: put a value in store."""
    key: str
    value: any

    def intercept(
        self, transform: Callable[[Effect], Effect | Program]
    ) -> TestPutEffect:
        return self


# ============================================================================
# Test Handlers (new style with k)
# ============================================================================


def handle_test_get(
    effect: TestGetEffect,
    env: Environment,
    store: Store,
    k: Continuation,
) -> Resume:
    """Handler for TestGetEffect using new protocol."""
    value = store.get(effect.key)
    return Resume(value=value, store=store)


def handle_test_put(
    effect: TestPutEffect,
    env: Environment,
    store: Store,
    k: Continuation,
) -> Resume:
    """Handler for TestPutEffect using new protocol."""
    new_store = {**store, effect.key: effect.value}
    return Resume(value=None, store=new_store)


# ============================================================================
# Tests: HandlerResult Types
# ============================================================================


class TestHandlerResultTypes:
    """Test the HandlerResult type definitions."""

    def test_resume_creation(self):
        """Resume can be created with value and store."""
        r = Resume(value=42, store={"key": "value"})
        assert r.value == 42
        assert r.store == {"key": "value"}

    def test_resume_is_frozen(self):
        """Resume is immutable."""
        r = Resume(value=42, store={})
        with pytest.raises(Exception):  # FrozenInstanceError
            r.value = 100

    def test_next_creation(self):
        """Next can be created with a CESKState."""
        state = CESKState(
            C=Value(42),
            E=FrozenDict(),
            S={},
            K=[],
        )
        n = Next(state=state)
        assert n.state == state


# ============================================================================
# Tests: Continuation
# ============================================================================


class TestContinuation:
    """Test the Continuation wrapper."""

    def test_continuation_creation(self):
        """Continuation can be created with frames and env."""
        k = Continuation(_frames=[], _env=FrozenDict({"x": 1}))
        assert k.frames == []
        assert k.env == FrozenDict({"x": 1})

    def test_continuation_resume(self):
        """Continuation.resume creates correct CESKState."""
        k = Continuation(_frames=[], _env=FrozenDict({"x": 1}))
        state = k.resume(42, {"key": "value"})

        assert isinstance(state, CESKState)
        assert isinstance(state.C, Value)
        assert state.C.v == 42
        assert state.E == FrozenDict({"x": 1})
        assert state.S == {"key": "value"}
        assert state.K == []

    def test_continuation_resume_preserves_frames(self):
        """Continuation.resume preserves the frame stack."""
        from doeff.cesk import LocalFrame

        frames = [LocalFrame(restore_env=FrozenDict())]
        k = Continuation(_frames=frames, _env=FrozenDict())
        state = k.resume(100, {})

        assert state.K == frames


# ============================================================================
# Tests: New Style Handlers
# ============================================================================


class TestNewStyleHandlers:
    """Test handlers using the new protocol with k."""

    def test_get_handler_returns_resume(self):
        """Get handler returns Resume with value from store."""
        effect = TestGetEffect(key="counter")
        env = FrozenDict()
        store = {"counter": 42}
        k = Continuation(_frames=[], _env=env)

        result = handle_test_get(effect, env, store, k)

        assert isinstance(result, Resume)
        assert result.value == 42
        assert result.store == store

    def test_put_handler_returns_resume_with_updated_store(self):
        """Put handler returns Resume with updated store."""
        effect = TestPutEffect(key="counter", value=100)
        env = FrozenDict()
        store = {"counter": 42}
        k = Continuation(_frames=[], _env=env)

        result = handle_test_put(effect, env, store, k)

        assert isinstance(result, Resume)
        assert result.value is None
        assert result.store == {"counter": 100}


# ============================================================================
# Tests: Legacy Handler Wrapping
# ============================================================================


class TestLegacyHandlerWrapping:
    """Test wrapping legacy handlers to new protocol."""

    def test_wrap_sync_handler(self):
        """Sync handler can be wrapped to new protocol."""
        def legacy_handler(effect, env, store):
            return (store.get(effect.key), store)

        wrapped = wrap_legacy_sync_handler(legacy_handler)

        effect = TestGetEffect(key="x")
        env = FrozenDict()
        store = {"x": 99}
        k = Continuation(_frames=[], _env=env)

        result = wrapped(effect, env, store, k)

        assert isinstance(result, Resume)
        assert result.value == 99
        assert result.store == store

    def test_wrapped_handler_modifies_store(self):
        """Wrapped handler can modify store."""
        def legacy_handler(effect, env, store):
            new_store = {**store, effect.key: effect.value}
            return (None, new_store)

        wrapped = wrap_legacy_sync_handler(legacy_handler)

        effect = TestPutEffect(key="y", value=200)
        env = FrozenDict()
        store = {}
        k = Continuation(_frames=[], _env=env)

        result = wrapped(effect, env, store, k)

        assert isinstance(result, Resume)
        assert result.store == {"y": 200}
