from __future__ import annotations

import pytest

from doeff.program import Program
from doeff.cesk.runtime.simulation import SimulationRuntime


def test_pure_value_execution():
    runtime = SimulationRuntime()
    prog = Program.pure(42)
    
    result = runtime.run(prog)
    
    assert result == 42


def test_simple_program_with_state():
    from doeff import do, Put, Get
    
    @do
    def simple_program():
        yield Put("x", 10)
        value = yield Get("x")
        return value + 5
    
    runtime = SimulationRuntime()
    result = runtime.run(simple_program())
    
    assert result == 15


def test_program_with_environment():
    from doeff import do, Ask
    
    @do
    def env_program():
        config = yield Ask("config_value")
        return config * 2
    
    runtime = SimulationRuntime()
    result = runtime.run(env_program(), env={"config_value": 21})
    
    assert result == 42


def test_program_with_error_handling():
    from doeff import do, Put
    
    @do
    def error_program():
        yield Put("x", 10)
        raise ValueError("Test error")
    
    runtime = SimulationRuntime()
    
    with pytest.raises(ValueError, match="Test error"):
        runtime.run(error_program())


def test_nested_programs():
    from doeff import do, Put, Get
    
    @do
    def inner():
        yield Put("y", 20)
        return 10
    
    @do
    def outer():
        yield Put("x", 5)
        inner_result = yield inner()
        x = yield Get("x")
        y = yield Get("y")
        return x + inner_result + y
    
    runtime = SimulationRuntime()
    result = runtime.run(outer())
    
    assert result == 35


def test_multiple_state_operations():
    from doeff import do, Put, Get, Modify
    
    @do
    def multi_state():
        yield Put("counter", 0)
        yield Modify("counter", lambda x: x + 1)
        yield Modify("counter", lambda x: x + 2)
        yield Modify("counter", lambda x: x + 3)
        final = yield Get("counter")
        return final
    
    runtime = SimulationRuntime()
    result = runtime.run(multi_state())
    
    assert result == 6
