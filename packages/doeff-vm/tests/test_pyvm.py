import asyncio

import pytest

from doeff import do, Program
from doeff.effects import Get, Put, Modify, Ask, Tell, Pure


def test_import():
    """Test that doeff_vm can be imported."""
    import doeff_vm
    assert hasattr(doeff_vm, 'PyVM')
    assert hasattr(doeff_vm, 'PyStdlib')


def test_pyvm_creation():
    """Test PyVM can be created."""
    from doeff_vm import PyVM
    vm = PyVM()
    assert vm is not None


def test_stdlib_creation():
    """Test stdlib can be created from PyVM."""
    from doeff_vm import PyVM
    vm = PyVM()
    stdlib = vm.stdlib()
    assert stdlib is not None


@do
def simple_program() -> Program[int]:
    return 42
    yield  # make it a generator


def test_simple_pure_program():
    """Test running a simple program that returns a value."""
    from doeff_vm import PyVM
    vm = PyVM()
    result = vm.run(simple_program())
    assert result == 42


@do
def counter_program() -> Program[int]:
    yield Put("counter", 0)
    val = yield Get("counter")
    yield Put("counter", val + 1)
    new_val = yield Get("counter")
    return new_val


def test_state_effects():
    """Test Get/Put effects with state handler."""
    from doeff_vm import PyVM
    vm = PyVM()
    stdlib = vm.stdlib()
    _ = stdlib.state
    stdlib.install_state(vm)
    
    result = vm.run(counter_program())
    assert result == 1


@do
def nested_program() -> Program[int]:
    yield Put("a", 10)
    yield Put("b", 20)
    a = yield Get("a")
    b = yield Get("b")
    return a + b


def test_multiple_state_operations():
    """Test multiple state operations."""
    from doeff_vm import PyVM
    vm = PyVM()
    stdlib = vm.stdlib()
    _ = stdlib.state
    stdlib.install_state(vm)
    
    result = vm.run(nested_program())
    assert result == 30


@do
def logging_program() -> Program[str]:
    yield Tell("Starting")
    yield Tell("Processing")
    yield Tell("Done")
    return "completed"


def test_writer_effects():
    """Test Tell effects with writer handler."""
    from doeff_vm import PyVM
    vm = PyVM()
    stdlib = vm.stdlib()
    _ = stdlib.writer
    stdlib.install_writer(vm)
    
    result = vm.run(logging_program())
    assert result == "completed"
    
    logs = vm.logs()
    assert len(logs) == 3
    assert logs[0] == "Starting"
    assert logs[1] == "Processing"
    assert logs[2] == "Done"


@do
def state_and_logging_program() -> Program[int]:
    yield Tell("Setting counter")
    yield Put("counter", 100)
    yield Tell("Getting counter")
    val = yield Get("counter")
    yield Tell(f"Counter is {val}")
    return val


def test_combined_effects():
    """Test using multiple effect types together."""
    from doeff_vm import PyVM
    vm = PyVM()
    stdlib = vm.stdlib()
    _ = stdlib.state
    _ = stdlib.writer
    stdlib.install_state(vm)
    stdlib.install_writer(vm)
    
    result = vm.run(state_and_logging_program())
    assert result == 100
    
    logs = vm.logs()
    assert len(logs) == 3


@do
def modify_program() -> Program[tuple[int, int]]:
    yield Put("counter", 10)
    old_value = yield Modify("counter", lambda x: x * 2)
    new_value = yield Get("counter")
    return (old_value, new_value)


def test_modify_effect():
    """Test Modify effect with Python callback function."""
    from doeff_vm import PyVM
    vm = PyVM()
    stdlib = vm.stdlib()
    _ = stdlib.state
    stdlib.install_state(vm)
    
    result = vm.run(modify_program())
    assert result == (10, 20)


@do
def nested_state_and_writer_program() -> Program[int]:
    yield Tell("outer start")
    yield Put("x", 1)
    yield Tell("after put x=1")
    x = yield Get("x")
    yield Tell(f"got x={x}")
    yield Put("x", x + 10)
    final = yield Get("x")
    yield Tell(f"final x={final}")
    return final


def test_nested_stdlib_handlers():
    """Test multiple stdlib handlers (state + writer) working together."""
    from doeff_vm import PyVM
    vm = PyVM()
    stdlib = vm.stdlib()
    _ = stdlib.state
    _ = stdlib.writer
    stdlib.install_state(vm)
    stdlib.install_writer(vm)
    
    result = vm.run(nested_state_and_writer_program())
    assert result == 11
    
    logs = vm.logs()
    assert len(logs) == 4
    assert logs[0] == "outer start"
    assert logs[1] == "after put x=1"
    assert logs[2] == "got x=1"
    assert logs[3] == "final x=11"


@do
def interleaved_effects_program() -> Program[int]:
    yield Put("a", 100)
    yield Tell("set a=100")
    yield Put("b", 200)
    yield Tell("set b=200")
    a = yield Get("a")
    b = yield Get("b")
    yield Tell(f"sum={a+b}")
    yield Modify("a", lambda x: x * 2)
    a2 = yield Get("a")
    yield Tell(f"doubled a={a2}")
    return a2 + b


def test_interleaved_state_writer_modify():
    """Test interleaved state operations with logging and modify."""
    from doeff_vm import PyVM
    vm = PyVM()
    stdlib = vm.stdlib()
    _ = stdlib.state
    _ = stdlib.writer
    stdlib.install_state(vm)
    stdlib.install_writer(vm)
    
    result = vm.run(interleaved_effects_program())
    assert result == 400  # a=200, b=200 -> 400
    
    logs = vm.logs()
    assert "set a=100" in logs
    assert "set b=200" in logs
    assert "sum=300" in logs
    assert "doubled a=200" in logs


class WithHandler:
    """Control primitive to install a Python handler."""
    def __init__(self, handler, body):
        self.handler = handler
        self.body = body


class Resume:
    """Control primitive to resume continuation with value."""
    def __init__(self, continuation, value):
        self.continuation = continuation
        self.value = value


class Delegate:
    """Control primitive to delegate effect to outer handler."""
    pass


class CustomEffect:
    """Custom effect for testing Python handlers."""
    def __init__(self, value):
        self.value = value


def make_custom_handler():
    """Create a handler that doubles the effect value and delegates unknown effects."""
    def handler(effect, k):
        if isinstance(effect, CustomEffect):
            resume_value = yield Resume(k, effect.value * 2)
            return resume_value
        else:
            yield Delegate()
    return handler


def test_python_handler_basic():
    """Test basic Python handler invocation."""
    from doeff_vm import PyVM
    vm = PyVM()
    
    def body():
        result = yield CustomEffect(21)
        return result
    
    def main():
        handler = make_custom_handler()
        result = yield WithHandler(handler, body)
        return result
    
    result = vm.run(main())
    assert result == 42


def make_state_counting_handler():
    """Create a handler that counts effect invocations and delegates unknown effects."""
    state = {"count": 0}

    def handler(effect, k):
        if isinstance(effect, CustomEffect):
            state["count"] += 1
            resume_value = yield Resume(k, state["count"])
            return resume_value
        else:
            yield Delegate()
    return handler, state


def test_python_handler_multiple_effects():
    """Test Python handler handling multiple effects."""
    from doeff_vm import PyVM
    vm = PyVM()
    
    def body():
        a = yield CustomEffect(1)
        b = yield CustomEffect(2)
        c = yield CustomEffect(3)
        return [a, b, c]
    
    def main():
        handler, _ = make_state_counting_handler()
        result = yield WithHandler(handler, body)
        return result
    
    result = vm.run(main())
    assert result == [1, 2, 3]


def test_python_handler_with_stdlib():
    """Test Python handler working alongside stdlib handlers."""
    from doeff_vm import PyVM
    vm = PyVM()
    stdlib = vm.stdlib()
    _ = stdlib.state
    stdlib.install_state(vm)
    
    def body():
        x = yield CustomEffect(10)
        yield Put("x", x)
        y = yield Get("x")
        return y
    
    def main():
        handler = make_custom_handler()
        result = yield WithHandler(handler, body)
        return result
    
    result = vm.run(main())
    assert result == 20


def test_nested_with_handler():
    """Test nested WithHandler: inner handler completes, then outer handler handles."""
    from doeff_vm import PyVM
    vm = PyVM()

    def inner_handler(effect, k):
        if isinstance(effect, CustomEffect):
            resume_value = yield Resume(k, effect.value + 100)
            return resume_value
        else:
            raise ValueError(f"Unknown effect: {effect}")

    def outer_handler(effect, k):
        if isinstance(effect, CustomEffect):
            resume_value = yield Resume(k, effect.value * 2)
            return resume_value
        else:
            raise ValueError(f"Unknown effect: {effect}")

    def inner_body():
        val = yield CustomEffect(5)  # inner_handler: 5 + 100 = 105
        return val

    def outer_body():
        inner_result = yield WithHandler(inner_handler, inner_body)  # 105
        outer_val = yield CustomEffect(inner_result)  # outer_handler: 105 * 2 = 210
        return outer_val

    def main():
        result = yield WithHandler(outer_handler, outer_body)
        return result

    result = vm.run(main())
    assert result == 210


def test_handler_transforms_resume_result():
    """Test that handler can transform the value returned after Resume."""
    from doeff_vm import PyVM
    vm = PyVM()

    def transforming_handler(effect, k):
        if isinstance(effect, CustomEffect):
            resume_value = yield Resume(k, effect.value)
            return resume_value * 3
        else:
            raise ValueError(f"Unknown effect: {effect}")

    def body():
        x = yield CustomEffect(10)  # handler sends 10 back
        return x + 5  # body returns 15

    def main():
        # Handler resumes body with 10, body returns 15,
        # handler gets resume_value=15, returns 15*3=45.
        result = yield WithHandler(transforming_handler, body)
        return result

    result = vm.run(main())
    assert result == 45


def test_handler_abandon_continuation():
    """Test handler that returns without resuming the continuation (abandon)."""
    from doeff_vm import PyVM
    vm = PyVM()

    def abandoning_handler(effect, k):
        if isinstance(effect, CustomEffect):
            # Return directly without Resume; the body's continuation is abandoned.
            return effect.value * 10
        yield  # unreachable, but makes this function a generator

    def body():
        x = yield CustomEffect(7)  # handler does not resume, so body stops here
        return x + 1000  # this line should never execute

    def main():
        result = yield WithHandler(abandoning_handler, body)
        return result

    result = vm.run(main())
    assert result == 70


def test_handler_accumulates_across_effects():
    """Test handler that accumulates state across multiple effect invocations."""
    from doeff_vm import PyVM
    vm = PyVM()

    accumulated = []

    def accumulating_handler(effect, k):
        if isinstance(effect, CustomEffect):
            accumulated.append(effect.value)
            total = sum(accumulated)
            resume_value = yield Resume(k, total)
            return resume_value
        else:
            raise ValueError(f"Unknown effect: {effect}")

    def body():
        a = yield CustomEffect(10)  # accumulated=[10], total=10
        b = yield CustomEffect(20)  # accumulated=[10,20], total=30
        c = yield CustomEffect(30)  # accumulated=[10,20,30], total=60
        return (a, b, c)

    def main():
        result = yield WithHandler(accumulating_handler, body)
        return result

    result = vm.run(main())
    assert result == (10, 30, 60)
    assert accumulated == [10, 20, 30]


def test_scheduler_creation():
    """Test scheduler handler can be created and installed."""
    from doeff_vm import PyVM, PySchedulerHandler
    vm = PyVM()
    scheduler = vm.scheduler()
    assert scheduler is not None
    scheduler.install(vm)


class CreatePromise:
    """Scheduler effect: create a new promise."""
    pass


def test_scheduler_create_promise():
    """Test scheduler CreatePromise effect."""
    from doeff_vm import PyVM
    vm = PyVM()
    scheduler = vm.scheduler()
    scheduler.install(vm)

    def body():
        promise = yield CreatePromise()
        return promise

    result = vm.run(body())
    # Result should be a dict representing a PromiseHandle
    assert isinstance(result, dict)
    assert result["type"] == "Promise"
    assert "promise_id" in result


def test_py_store_basic():
    """Test PyStore read/write."""
    from doeff_vm import PyVM
    vm = PyVM()

    # Set and get
    vm.set_store("key1", 42)
    assert vm.get_store("key1") == 42

    # Get the dict directly
    store = vm.py_store()
    assert isinstance(store, dict)
    assert store["key1"] == 42

    # Store persists across runs
    vm.set_store("key2", "hello")

    def prog():
        return 1
        yield

    vm.run(prog())

    assert vm.get_store("key2") == "hello"


class PythonAsyncSyntaxEscape:
    """Marker yielded by generators to request an async Python call."""
    def __init__(self, action):
        self.action = action


async def async_run(vm, program):
    """Async driver that can await PythonAsyncSyntaxEscape actions.

    Unlike ``vm.run()``, this driver can handle ``CallAsync`` events by
    awaiting the returned coroutine inside a real asyncio event loop.
    """
    vm.start_program(program)

    while True:
        result = vm.step_once()
        tag = result[0]
        if tag == "done":
            return result[1]
        elif tag == "call_async":
            func, args = result[1], result[2]
            try:
                awaitable = func(*args)
                value = await awaitable
                vm.feed_async_result(value)
            except Exception as e:
                vm.feed_async_error(e)
        elif tag == "continue":
            # yield to the event loop briefly so other tasks can run
            await asyncio.sleep(0)
        else:
            raise RuntimeError(f"Unexpected step result tag: {tag}")


# ---------------------------------------------------------------------------
# Async tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_async_run_basic():
    """Test async_run with a PythonAsyncSyntaxEscape handler returning a value."""
    from doeff_vm import PyVM
    vm = PyVM()

    async def my_async_action():
        await asyncio.sleep(0.01)
        return 42

    def body():
        result = yield PythonAsyncSyntaxEscape(action=my_async_action)
        return result

    result = await async_run(vm, body())
    assert result == 42


@pytest.mark.asyncio
async def test_async_run_multiple_awaits():
    """Test async_run with several sequential async escapes."""
    from doeff_vm import PyVM
    vm = PyVM()

    async def async_add(a, b):
        await asyncio.sleep(0)
        return a + b

    def body():
        x = yield PythonAsyncSyntaxEscape(action=lambda: async_add(1, 2))
        y = yield PythonAsyncSyntaxEscape(action=lambda: async_add(x, 10))
        return y

    result = await async_run(vm, body())
    assert result == 13


@pytest.mark.asyncio
async def test_async_run_error_propagation():
    """Test that exceptions from async actions propagate correctly."""
    from doeff_vm import PyVM
    vm = PyVM()

    async def failing_action():
        raise ValueError("async boom")

    def body():
        result = yield PythonAsyncSyntaxEscape(action=failing_action)
        return result  # should not reach here

    with pytest.raises(RuntimeError, match=".*"):
        await async_run(vm, body())


@pytest.mark.asyncio
async def test_async_run_with_pure_return():
    """Test async_run with a program that has no async escapes."""
    from doeff_vm import PyVM
    vm = PyVM()

    def body():
        return 99
        yield  # make it a generator

    result = await async_run(vm, body())
    assert result == 99


@pytest.mark.asyncio
async def test_async_run_with_state_effects():
    """Test async_run with stdlib state effects + async escapes."""
    from doeff_vm import PyVM
    vm = PyVM()
    stdlib = vm.stdlib()
    _ = stdlib.state
    stdlib.install_state(vm)

    async def fetch_value():
        await asyncio.sleep(0)
        return 100

    def body():
        fetched = yield PythonAsyncSyntaxEscape(action=fetch_value)
        yield Put("x", fetched)
        val = yield Get("x")
        return val

    result = await async_run(vm, body())
    assert result == 100


## -- Scoped stdlib handler tests (run_scoped) ----------------------------

def test_run_scoped_state():
    """Test run_scoped installs and removes the state handler."""
    from doeff_vm import PyVM
    vm = PyVM()

    result = vm.run_scoped(counter_program(), state=True)
    assert result == 1


def test_run_scoped_writer():
    """Test run_scoped installs and removes the writer handler."""
    from doeff_vm import PyVM
    vm = PyVM()

    result = vm.run_scoped(logging_program(), writer=True)
    assert result == "completed"
    logs = vm.logs()
    assert len(logs) == 3


def test_run_scoped_combined():
    """Test run_scoped with state + writer together."""
    from doeff_vm import PyVM
    vm = PyVM()

    result = vm.run_scoped(state_and_logging_program(), state=True, writer=True)
    assert result == 100
    logs = vm.logs()
    assert len(logs) == 3


def test_run_scoped_handlers_do_not_leak():
    """Test that scoped handlers are removed after run_scoped completes.

    After a run_scoped call with state=True, a subsequent run() without
    handlers should fail because there is no handler for Get/Put effects.
    """
    from doeff_vm import PyVM
    vm = PyVM()

    # First run succeeds with scoped state handler
    result = vm.run_scoped(counter_program(), state=True)
    assert result == 1

    # Second run without handlers should fail (no state handler)
    with pytest.raises(RuntimeError, match="(no matching handler|unhandled effect)"):
        vm.run(counter_program())


def test_run_scoped_handlers_do_not_leak_writer():
    """Test that scoped writer handlers are removed after run_scoped completes."""
    from doeff_vm import PyVM
    vm = PyVM()

    # First run succeeds with scoped writer handler
    result = vm.run_scoped(logging_program(), writer=True)
    assert result == "completed"

    # Second run without handlers should fail
    with pytest.raises(RuntimeError, match="(no matching handler|unhandled effect)"):
        vm.run(logging_program())


def test_run_scoped_error_still_cleans_up():
    """Test that handlers are removed even when the program raises an error."""
    from doeff_vm import PyVM
    vm = PyVM()

    @do
    def failing_program() -> Program[int]:
        yield Put("x", 1)
        raise ValueError("intentional error")
        yield  # make it a generator

    # This should raise but still clean up the handlers
    with pytest.raises(RuntimeError):
        vm.run_scoped(failing_program(), state=True)

    # Verify handler was cleaned up: running a state program should fail
    with pytest.raises(RuntimeError, match="(no matching handler|unhandled effect)"):
        vm.run(counter_program())


def test_run_scoped_successive_independent_runs():
    """Test that successive run_scoped calls are independent."""
    from doeff_vm import PyVM
    vm = PyVM()

    @do
    def put_program() -> Program[None]:
        yield Put("key", 42)
        return None

    @do
    def get_program() -> Program[int]:
        val = yield Get("key")
        return val

    # Run 1: put a value
    vm.run_scoped(put_program(), state=True)

    # Run 2: get the value (state persists in RustStore across runs,
    # but the handler must be re-installed via scoping)
    result = vm.run_scoped(get_program(), state=True)
    # RustStore state persists across runs (it's the store, not the handler)
    assert result == 42


## -- ProgramBase validation tests (to_generator_strict) --------------------

def test_yielded_raw_generator_rejected_as_program():
    """Yielding a raw generator inside a program body should raise TypeError.

    The VM requires Yielded::Program entries to be ProgramBase objects (with
    a ``to_generator`` method). Raw generators must go through ``vm.run()``
    or ``vm.start_program()`` instead.
    """
    from doeff_vm import PyVM
    vm = PyVM()

    def sub_generator():
        """A plain generator (not decorated with @do)."""
        yield Pure(42)

    def main():
        # Yielding a raw generator triggers classify_yielded -> Yielded::Program
        # -> StartProgram -> to_generator_strict -> TypeError
        result = yield sub_generator()
        return result

    with pytest.raises(TypeError, match="Expected ProgramBase"):
        vm.run(main())


def test_yielded_program_base_accepted():
    """Yielding a @do-decorated ProgramBase inside a program body should work.

    This is the happy-path counterpart of the strict validation test.
    """
    from doeff_vm import PyVM
    vm = PyVM()

    @do
    def sub_program() -> Program[int]:
        return 99
        yield  # make it a generator

    def main():
        result = yield sub_program()
        return result

    result = vm.run(main())
    assert result == 99


def test_run_accepts_raw_generator():
    """vm.run() should accept raw generators at the top level (lenient path)."""
    from doeff_vm import PyVM
    vm = PyVM()

    def raw_gen():
        return 7
        yield  # make it a generator

    result = vm.run(raw_gen())
    assert result == 7


## -- Gap-detection tests (G1, G2, G4) --------------------------------------
# These tests expose specific spec-vs-impl divergences.
# They should FAIL before the corresponding fixes are applied.


class GetHandlers:
    """Control primitive: request the current handler chain."""
    pass


class UnknownThing:
    """Not an effect, not a primitive, not a program.

    Used by G4 test to verify that truly unrecognized yielded objects
    produce a TypeError (Yielded::Unknown) rather than being silently
    dispatched as Effect::Python.
    """

    def __init__(self, value):
        self.value = value


def test_handler_returning_program_base():
    """G1: CallHandler should call to_generator on the handler's return value.

    If the handler is a regular function (not a generator function) that
    returns a @do-decorated ProgramBase, the VM must call to_generator()
    on it before pushing it as a PythonGenerator frame.

    Currently fails because CallHandler wraps the result as-is, so the
    VM tries to call __next__ on a ProgramBase object.
    """
    from doeff_vm import PyVM

    vm = PyVM()

    def handler_func(effect, k):
        """Regular function that returns a @do-decorated ProgramBase."""

        @do
        def handler_program() -> Program[int]:
            result = yield Resume(k, effect.value * 2)
            return result

        return handler_program()  # Returns ProgramBase, NOT a generator

    def body():
        result = yield CustomEffect(21)
        return result

    def main():
        result = yield WithHandler(handler_func, body)
        return result

    result = vm.run(main())
    assert result == 42


def test_get_handlers_uses_dispatch_handler_chain():
    """G2: GetHandlers inside a handler should return the dispatch-time handler_chain.

    When a handler installs additional handlers (via WithHandler) during its
    execution and then calls GetHandlers, the result should reflect the
    handler_chain snapshot taken when dispatch started — NOT the current
    scope_chain which includes the newly installed handler.

    Currently fails because handle_get_handlers uses current_scope_chain()
    instead of the dispatch context's handler_chain.
    """
    from doeff_vm import PyVM

    vm = PyVM()

    def outer_handler(effect, k):
        if isinstance(effect, CustomEffect):
            # Install a temporary inner handler during handler execution
            def temp_handler(eff, k2):
                yield Delegate()

            def temp_body():
                # GetHandlers here: if using scope_chain, result includes
                # temp_handler; if using handler_chain, it does not.
                handlers = yield GetHandlers()
                return handlers

            inner_result = yield WithHandler(temp_handler, temp_body)
            # inner_result is the handler list from GetHandlers
            resume_value = yield Resume(k, inner_result)
            return resume_value
        yield Delegate()

    def body():
        result = yield CustomEffect(42)
        return result

    def main():
        result = yield WithHandler(outer_handler, body)
        return result

    result = vm.run(main())
    # result is the list returned by GetHandlers.
    # Spec (handler_chain): should NOT include temp_handler.
    # Impl (scope_chain): DOES include temp_handler → list is longer.
    #
    # With only outer_handler installed at dispatch time, handler_chain
    # has exactly 1 handler.  scope_chain would have 2 (temp + outer).
    assert isinstance(result, list)
    assert len(result) == 1, (
        f"Expected 1 handler (dispatch handler_chain) but got {len(result)} "
        f"(scope_chain leaked newly installed handler)"
    )


def test_yielding_unknown_object_raises_type_error():
    """G4: Yielding a primitive value (not an effect/primitive/program) raises TypeError.

    Primitive Python types (int, str, bool, None, etc.) are not valid effects
    and cannot be dispatched through handlers.  The VM should raise TypeError
    for these, not silently try to dispatch them.

    Class instances remain valid as custom effects (Effect::Python).
    """
    from doeff_vm import PyVM

    vm = PyVM()

    def body_int():
        result = yield 42
        return result

    def body_str():
        result = yield "not an effect"
        return result

    with pytest.raises(TypeError):
        vm.run(body_int())

    with pytest.raises(TypeError):
        vm.run(body_str())


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
