"""Test enhanced Recover with error handler functions."""

import pytest
from doeff import do, EffectGenerator, Recover, Fail, ProgramInterpreter, Log


@pytest.mark.asyncio
async def test_error_handler_returning_value():
    """Test error handler that returns a direct value."""
    @do
    def failing_program() -> EffectGenerator[str]:
        yield Fail(ValueError("Something went wrong"))
        return "never reached"
    
    # Error handler that inspects the exception
    def handle_error(exc: Exception) -> str:
        return f"Handled: {type(exc).__name__}: {str(exc)}"
    
    @do
    def test_program() -> EffectGenerator[str]:
        result = yield Recover(failing_program, handle_error)
        return result
    
    engine = ProgramInterpreter()
    result = await engine.run(test_program())
    
    assert result.is_ok
    assert result.value == "Handled: ValueError: Something went wrong"


@pytest.mark.asyncio
async def test_error_handler_returning_program():
    """Test error handler that returns a Program."""
    @do
    def failing_program() -> EffectGenerator[str]:
        yield Fail(KeyError("missing_key"))
        return "never reached"
    
    # Error handler that returns a Program based on error type
    @do
    def handle_error(exc: Exception) -> EffectGenerator[str]:
        yield Log(f"Error handler got: {type(exc).__name__}")
        if isinstance(exc, KeyError):
            return "Default value for missing key"
        else:
            return "Unknown error"
    
    @do
    def test_program() -> EffectGenerator[str]:
        result = yield Recover(failing_program, handle_error)
        return result
    
    engine = ProgramInterpreter()
    result = await engine.run(test_program())
    
    assert result.is_ok
    assert result.value == "Default value for missing key"


@pytest.mark.asyncio
async def test_conditional_error_handling():
    """Test conditional error handling based on exception type."""
    @do
    def risky_operation(should_fail: str) -> EffectGenerator[int]:
        if should_fail == "value":
            yield Fail(ValueError("Bad value"))
        elif should_fail == "type":
            yield Fail(TypeError("Wrong type"))
        else:
            return 42
    
    def smart_handler(exc: Exception) -> int:
        if isinstance(exc, ValueError):
            return -1  # Special error code for value errors
        elif isinstance(exc, TypeError):
            return -2  # Special error code for type errors
        else:
            return 0  # Generic error code
    
    @do
    def test_program() -> EffectGenerator[int]:
        # Test different error scenarios
        result1 = yield Recover(risky_operation("value"), smart_handler)
        result2 = yield Recover(risky_operation("type"), smart_handler)
        result3 = yield Recover(risky_operation("ok"), smart_handler)
        
        return result1 + result2 + result3  # -1 + -2 + 42 = 39
    
    engine = ProgramInterpreter()
    result = await engine.run(test_program())
    
    assert result.is_ok
    assert result.value == 39


@pytest.mark.asyncio
async def test_backward_compat_with_value():
    """Test backward compatibility with direct value fallback."""
    @do
    def failing_program() -> EffectGenerator[str]:
        yield Fail(RuntimeError("Oops"))
        return "never"
    
    @do
    def test_program() -> EffectGenerator[str]:
        # Using a direct value as fallback (old behavior)
        result = yield Recover(failing_program, "fallback_value")
        return result
    
    engine = ProgramInterpreter()
    result = await engine.run(test_program())
    
    assert result.is_ok
    assert result.value == "fallback_value"


@pytest.mark.asyncio
async def test_backward_compat_with_program():
    """Test backward compatibility with Program fallback."""
    @do
    def failing_program() -> EffectGenerator[str]:
        yield Fail(RuntimeError("Oops"))
        return "never"
    
    @do
    def fallback_program() -> EffectGenerator[str]:
        yield Log("Using fallback program")
        return "fallback_from_program"
    
    @do
    def test_program() -> EffectGenerator[str]:
        # Using a Program as fallback (old behavior)
        result = yield Recover(failing_program, fallback_program)
        return result
    
    engine = ProgramInterpreter()
    result = await engine.run(test_program())
    
    assert result.is_ok
    assert result.value == "fallback_from_program"


@pytest.mark.asyncio
async def test_error_handler_with_multiple_exception_types():
    """Test error handler that handles multiple exception types differently."""
    @do
    def operation_that_fails(error_type: str) -> EffectGenerator[str]:
        if error_type == "key":
            yield Fail(KeyError("not found"))
        elif error_type == "value":
            yield Fail(ValueError("invalid"))
        elif error_type == "index":
            yield Fail(IndexError("out of range"))
        else:
            return "success"
    
    @do
    def multi_handler(exc: Exception) -> EffectGenerator[str]:
        yield Log(f"Handling {type(exc).__name__}")
        
        if isinstance(exc, KeyError):
            return "default_key"
        elif isinstance(exc, ValueError):
            return "default_value"
        elif isinstance(exc, IndexError):
            return "default_index"
        else:
            yield Fail(exc)  # Re-raise unknown exceptions
            return "unreachable"
    
    @do
    def test_program() -> EffectGenerator[str]:
        r1 = yield Recover(operation_that_fails("key"), multi_handler)
        r2 = yield Recover(operation_that_fails("value"), multi_handler)
        r3 = yield Recover(operation_that_fails("index"), multi_handler)
        r4 = yield Recover(operation_that_fails("ok"), multi_handler)
        
        return f"{r1},{r2},{r3},{r4}"
    
    engine = ProgramInterpreter()
    result = await engine.run(test_program())
    
    assert result.is_ok
    assert result.value == "default_key,default_value,default_index,success"


@pytest.mark.asyncio
async def test_nested_recover_with_handlers():
    """Test nested Recover effects with error handlers."""
    @do
    def inner_failing() -> EffectGenerator[int]:
        yield Fail(ValueError("inner error"))
        return 0
    
    def inner_handler(exc: Exception) -> int:
        return 100
    
    @do
    def outer_program() -> EffectGenerator[int]:
        # Inner recover handles the error
        value = yield Recover(inner_failing, inner_handler)
        
        if value > 50:
            # This will fail
            yield Fail(RuntimeError("value too large"))
        return value
    
    def outer_handler(exc: Exception) -> int:
        if isinstance(exc, RuntimeError):
            return 50  # Cap the value
        return 0
    
    @do
    def test_program() -> EffectGenerator[int]:
        result = yield Recover(outer_program, outer_handler)
        return result
    
    engine = ProgramInterpreter()
    result = await engine.run(test_program())
    
    assert result.is_ok
    assert result.value == 50  # Outer handler capped the value


@pytest.mark.asyncio
async def test_error_handler_with_state_effects():
    """Test error handler that uses effects."""
    from doeff import Put, Get
    
    @do
    def failing_with_state() -> EffectGenerator[int]:
        yield Put("counter", 1)
        yield Fail(ValueError("failed after setting state"))
        return 0
    
    @do
    def stateful_handler(exc: Exception) -> EffectGenerator[int]:
        # Error handler can access and modify state
        counter = yield Get("counter")
        if counter is None:
            counter = 0
        yield Put("counter", counter + 10)
        yield Log(f"Handler incremented counter to {counter + 10}")
        return counter + 10
    
    @do
    def test_program() -> EffectGenerator[int]:
        result = yield Recover(failing_with_state, stateful_handler)
        final_counter = yield Get("counter")
        return result + (final_counter or 0)
    
    engine = ProgramInterpreter()
    result = await engine.run(test_program())
    
    assert result.is_ok
    assert result.value == 22  # 11 (from handler) + 11 (from state)


@pytest.mark.asyncio  
async def test_lambda_error_handler():
    """Test using lambda as error handler."""
    @do
    def failing() -> EffectGenerator[str]:
        yield Fail(KeyError("test_key"))
        return ""
    
    @do
    def test_program() -> EffectGenerator[str]:
        # Lambda error handler
        result = yield Recover(
            failing,
            lambda exc: f"KeyError: {exc.args[0]}" if isinstance(exc, KeyError) else "unknown"
        )
        return result
    
    engine = ProgramInterpreter()
    result = await engine.run(test_program())
    
    assert result.is_ok
    assert result.value == "KeyError: test_key"


@pytest.mark.asyncio
async def test_error_handler_that_returns_none():
    """Test error handler that returns None."""
    @do
    def failing() -> EffectGenerator[None]:
        yield Fail(ValueError("error"))
        return "not None"
    
    def none_handler(exc: Exception) -> None:
        # Explicitly return None
        return None
    
    @do
    def test_program() -> EffectGenerator[str]:
        result = yield Recover(failing, none_handler)
        return "None" if result is None else "not None"
    
    engine = ProgramInterpreter()
    result = await engine.run(test_program())
    
    assert result.is_ok
    assert result.value == "None"