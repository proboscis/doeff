"""Test edge cases for enhanced Recover effect."""

import pytest
from doeff import do, EffectGenerator, Recover, Fail, ProgramInterpreter, Log


@pytest.mark.asyncio
async def test_error_handler_that_raises_different_exception():
    """Test error handler that raises a different exception."""
    @do
    def failing() -> EffectGenerator[str]:
        yield Fail(ValueError("original"))
        return ""
    
    def handler_that_raises(exc: Exception) -> str:
        # Handler raises a different exception
        raise RuntimeError(f"Handler failed: {exc}")
    
    @do
    def test_program() -> EffectGenerator[str]:
        result = yield Recover(failing, handler_that_raises)
        return result
    
    engine = ProgramInterpreter()
    result = await engine.run(test_program())
    
    # Should fail with the new exception from handler
    assert result.is_err
    assert "Handler failed" in str(result.result.error)


@pytest.mark.asyncio
async def test_error_handler_program_that_fails():
    """Test error handler that returns a failing Program."""
    @do
    def failing() -> EffectGenerator[str]:
        yield Fail(ValueError("original"))
        return ""
    
    @do
    def handler_that_fails(exc: Exception) -> EffectGenerator[str]:
        yield Log(f"Handler got {exc}")
        # Handler program also fails
        yield Fail(RuntimeError("Handler program failed"))
        return "unreachable"
    
    @do  
    def test_program() -> EffectGenerator[str]:
        result = yield Recover(failing, handler_that_fails)
        return result
    
    engine = ProgramInterpreter()
    result = await engine.run(test_program())
    
    # Should fail with the handler's error
    assert result.is_err
    assert "Handler program failed" in str(result.result.error)


@pytest.mark.asyncio
async def test_recover_with_async_effect_in_handler():
    """Test error handler that uses async effects."""
    @do
    def failing() -> EffectGenerator[str]:
        yield Fail(ValueError("error"))
        return ""
    
    @do
    def async_handler(exc: Exception) -> EffectGenerator[str]:
        from doeff import Await
        import asyncio
        
        # Use async effect in handler
        async def async_task():
            await asyncio.sleep(0.001)
            return f"Async handled: {exc}"
        
        result = yield Await(async_task())
        return result
    
    @do
    def test_program() -> EffectGenerator[str]:
        result = yield Recover(failing, async_handler)
        return result
    
    engine = ProgramInterpreter()
    result = await engine.run(test_program())
    
    assert result.is_ok
    assert result.value == "Async handled: error"


@pytest.mark.asyncio
async def test_recover_with_no_args_kleisli_program():
    """Test Recover with a no-args @do decorated function as fallback."""
    @do
    def failing() -> EffectGenerator[str]:
        yield Fail(ValueError("error"))
        return ""
    
    @do
    def no_args_fallback() -> EffectGenerator[str]:
        yield Log("No args fallback")
        return "fallback_value"
    
    @do
    def test_program() -> EffectGenerator[str]:
        # Should work as a thunk, not an error handler
        result = yield Recover(failing, no_args_fallback)
        return result
    
    engine = ProgramInterpreter()
    result = await engine.run(test_program())
    
    assert result.is_ok
    assert result.value == "fallback_value"


@pytest.mark.asyncio
async def test_recover_with_one_arg_kleisli_program():
    """Test Recover with a one-arg @do decorated function as error handler."""
    @do
    def failing() -> EffectGenerator[str]:
        yield Fail(KeyError("missing"))
        return ""
    
    @do
    def one_arg_handler(exc: Exception) -> EffectGenerator[str]:
        yield Log(f"Handler got: {type(exc).__name__}")
        return f"Handled: {exc}"
    
    @do
    def test_program() -> EffectGenerator[str]:
        # Should work as an error handler
        result = yield Recover(failing, one_arg_handler)
        return result
    
    engine = ProgramInterpreter()
    result = await engine.run(test_program())
    
    assert result.is_ok
    assert "Handled:" in result.value
    assert "missing" in result.value


@pytest.mark.asyncio
async def test_recover_success_path_doesnt_call_handler():
    """Test that handler is not called on success."""
    handler_called = [False]
    
    @do
    def succeeding() -> EffectGenerator[str]:
        yield Log("Success path")
        return "success"
    
    def handler(exc: Exception) -> str:
        handler_called[0] = True
        return "handler_value"
    
    @do
    def test_program() -> EffectGenerator[str]:
        result = yield Recover(succeeding, handler)
        return result
    
    engine = ProgramInterpreter()
    result = await engine.run(test_program())
    
    assert result.is_ok
    assert result.value == "success"
    assert not handler_called[0], "Handler should not be called on success"


@pytest.mark.asyncio
async def test_recover_with_custom_exception_class():
    """Test Recover with custom exception classes."""
    class CustomError(Exception):
        def __init__(self, code: int, message: str):
            self.code = code
            self.message = message
            super().__init__(message)
    
    @do
    def failing() -> EffectGenerator[str]:
        yield Fail(CustomError(404, "Not Found"))
        return ""
    
    def custom_handler(exc: Exception) -> str:
        if isinstance(exc, CustomError):
            return f"Error {exc.code}: {exc.message}"
        return "Unknown error"
    
    @do
    def test_program() -> EffectGenerator[str]:
        result = yield Recover(failing, custom_handler)
        return result
    
    engine = ProgramInterpreter()
    result = await engine.run(test_program())
    
    assert result.is_ok
    assert result.value == "Error 404: Not Found"


@pytest.mark.asyncio
async def test_recover_chain_with_multiple_handlers():
    """Test chaining multiple Recover effects with different handlers."""
    @do
    def may_fail(n: int) -> EffectGenerator[int]:
        if n < 0:
            yield Fail(ValueError("negative"))
        elif n == 0:
            yield Fail(ZeroDivisionError("zero"))
        return n * 2
    
    def value_handler(exc: Exception) -> int:
        if isinstance(exc, ValueError):
            return 0  # Convert negative to 0
        raise exc  # Re-raise other exceptions
    
    def zero_handler(exc: Exception) -> int:
        if isinstance(exc, ZeroDivisionError):
            return 1  # Convert zero to 1
        raise exc
    
    @do
    def test_program() -> EffectGenerator[int]:
        # Chain recovers with different handlers
        @do
        def with_value_recovery(n: int) -> EffectGenerator[int]:
            return (yield Recover(may_fail(n), value_handler))
        
        @do
        def with_zero_recovery(n: int) -> EffectGenerator[int]:
            return (yield Recover(with_value_recovery(n), zero_handler))
        
        r1 = yield with_zero_recovery(-5)  # ValueError -> 0
        r2 = yield with_zero_recovery(0)   # ZeroDivisionError -> 1
        r3 = yield with_zero_recovery(5)   # Success -> 10
        
        return r1 + r2 + r3
    
    engine = ProgramInterpreter()
    result = await engine.run(test_program())
    
    assert result.is_ok
    assert result.value == 11  # 0 + 1 + 10


@pytest.mark.asyncio
async def test_recover_with_gather():
    """Test Recover within gathered parallel programs."""
    from doeff import Gather
    
    @do
    def may_fail(n: int) -> EffectGenerator[int]:
        if n < 0:
            yield Fail(ValueError(f"negative: {n}"))
        return n * 2
    
    def handler(exc: Exception) -> int:
        return 0  # Default value on error
    
    @do
    def test_program() -> EffectGenerator[list]:
        # Create programs with recovery wrapped in @do functions
        @do
        def with_recovery(n: int) -> EffectGenerator[int]:
            return (yield Recover(may_fail(n), handler))
        
        # Gather all results - pass programs as *args
        results = yield Gather(
            with_recovery(5),    # Success: 10
            with_recovery(-3),   # Error: 0
            with_recovery(7),    # Success: 14
            with_recovery(-1),   # Error: 0
        )
        return results
    
    engine = ProgramInterpreter()
    result = await engine.run(test_program())
    
    assert result.is_ok
    assert result.value == [10, 0, 14, 0]