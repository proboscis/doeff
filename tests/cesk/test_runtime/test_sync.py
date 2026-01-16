"""Tests for UnifiedSyncRuntime."""

from __future__ import annotations

import pytest

from doeff._vendor import FrozenDict
from doeff.cesk.runtime.sync import SyncRuntimeError, UnifiedSyncRuntime
from doeff.do import do
from doeff.effects import ask, get, put, Pure


class TestUnifiedSyncRuntimeBasics:
    def test_runs_pure_program(self) -> None:
        runtime = UnifiedSyncRuntime()
        
        result = runtime.run(Pure(42))
        
        assert result == 42
    
    def test_runs_do_program_with_pure_return(self) -> None:
        @do
        def program():
            return 100
            yield
        
        runtime = UnifiedSyncRuntime()
        result = runtime.run(program())
        
        assert result == 100
    
    def test_handles_ask_effect_with_env(self) -> None:
        @do
        def program():
            name = yield ask("user")
            return f"Hello, {name}!"
        
        runtime = UnifiedSyncRuntime()
        result = runtime.run(program(), env={"user": "Alice"})
        
        assert result == "Hello, Alice!"
    
    def test_handles_state_effects(self) -> None:
        @do
        def program():
            yield put("counter", 0)
            yield put("counter", 10)
            value = yield get("counter")
            return value
        
        runtime = UnifiedSyncRuntime()
        result = runtime.run(program())
        
        assert result == 10
    
    def test_handles_initial_store(self) -> None:
        @do
        def program():
            value = yield get("preset")
            return value * 2
        
        runtime = UnifiedSyncRuntime()
        result = runtime.run(program(), store={"preset": 21})
        
        assert result == 42


class TestUnifiedSyncRuntimeErrors:
    def test_propagates_exception_from_program(self) -> None:
        @do
        def program():
            raise ValueError("boom")
            yield
        
        runtime = UnifiedSyncRuntime()
        
        with pytest.raises(SyncRuntimeError) as exc_info:
            runtime.run(program())
        
        assert "boom" in str(exc_info.value)
    
    def test_raises_on_unhandled_effect(self) -> None:
        from doeff._types_internal import EffectBase
        from dataclasses import dataclass
        from typing import Callable
        
        @dataclass(frozen=True)
        class UnknownEffect(EffectBase):
            def intercept(self, transform: Callable) -> "UnknownEffect":
                return self
        
        @do
        def program():
            yield UnknownEffect()
            return "never"
        
        runtime = UnifiedSyncRuntime()
        
        with pytest.raises(SyncRuntimeError) as exc_info:
            runtime.run(program())
        
        assert "Unhandled effect" in str(exc_info.value)


class TestUnifiedSyncRuntimeComposition:
    def test_nested_programs(self) -> None:
        @do
        def inner():
            return 10
        
        @do
        def outer():
            x = yield inner()
            return x + 5
        
        runtime = UnifiedSyncRuntime()
        result = runtime.run(outer())
        
        assert result == 15
    
    def test_multiple_yields(self) -> None:
        @do
        def program():
            yield put("a", 1)
            yield put("b", 2)
            a = yield get("a")
            b = yield get("b")
            return a + b
        
        runtime = UnifiedSyncRuntime()
        result = runtime.run(program())
        
        assert result == 3
