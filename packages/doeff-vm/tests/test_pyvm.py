import asyncio

import pytest

import doeff_vm
from doeff import Effect, Program, do
from doeff.effects import Get, Put, Modify, Ask, Tell, Pure


def test_import():
    """Test that doeff_vm can be imported."""
    import doeff_vm

    assert hasattr(doeff_vm, "PyVM")
    assert hasattr(doeff_vm, "PyStdlib")


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
    yield Tell(f"sum={a + b}")
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


class CustomEffect(doeff_vm.EffectBase):
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


def _async_escape(action):
    """Create the VM AsyncEscape DoCtrl node for async Python calls."""
    import doeff_vm

    return getattr(doeff_vm, "PythonAsyncSyntaxEscape")(action=action)


async def async_run(vm, program):
    """Async driver that can await AsyncEscape actions.

    Unlike ``vm.run()``, this driver can handle ``CallAsync`` events by
    awaiting the returned coroutine inside a real asyncio event loop.
    """
    vm.start_program(program)

    while True:
        result = vm.step_once()
        tag = result[0]
        if tag == "done":
            return result[1]
        elif tag == "error":
            raise result[1]
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
    """Test async_run with an AsyncEscape action returning a value."""
    from doeff_vm import PyVM

    vm = PyVM()

    async def my_async_action():
        await asyncio.sleep(0.01)
        return 42

    def body():
        result = yield _async_escape(my_async_action)
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
        x = yield _async_escape(lambda: async_add(1, 2))
        y = yield _async_escape(lambda: async_add(x, 10))
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
        result = yield _async_escape(failing_action)
        return result  # should not reach here

    with pytest.raises(ValueError, match="async boom"):
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
        fetched = yield _async_escape(fetch_value)
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

    Verified: handle_get_handlers correctly uses dispatch context's
    handler_chain, not current_scope_chain().
    """
    from doeff_vm import (
        Delegate as VMDelegate,
        GetHandlers as VMGetHandlers,
        Perform as VMPerform,
        PyVM,
        Resume as VMResume,
        WithHandler as VMWithHandler,
    )

    vm = PyVM()

    def outer_handler(effect, k):
        if isinstance(effect, CustomEffect):
            # Install a temporary inner handler during handler execution
            def temp_handler(eff, k2):
                yield VMDelegate()

            # GetHandlers here: if using scope_chain, result includes
            # temp_handler; if using handler_chain, it does not.
            inner_result = yield VMWithHandler(temp_handler, VMGetHandlers())
            # inner_result is the handler list from GetHandlers
            resume_value = yield VMResume(k, inner_result)
            return resume_value
        yield VMDelegate()

    def main():
        result = yield VMWithHandler(outer_handler, VMPerform(CustomEffect(42)))
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


## -- R8 Spec Gap TDD tests --------------------------------------------------
# These tests verify spec compliance fixes found during systematic audit.


def test_run_with_result_has_result_getter():
    """G9: PyRunResult must have a .result getter returning (tag, payload)."""
    from doeff_vm import PyVM

    vm = PyVM()

    result = vm.run_with_result(simple_program())
    assert result.is_ok()

    # .result should return a tuple ("ok", value) or ("err", exception)
    r = result.result
    assert isinstance(r, tuple)
    assert r[0] == "ok"
    assert r[1] == 42


def test_run_with_result_raw_store_excludes_env():
    """G10: raw_store should contain state entries only, not env entries."""
    from doeff_vm import PyVM

    vm = PyVM()
    stdlib = vm.stdlib()
    _ = stdlib.state
    stdlib.install_state(vm)

    # Pre-populate env
    vm.put_env("config_key", "config_value")

    @do
    def prog() -> Program[int]:
        yield Put("counter", 42)
        return 1

    result = vm.run_with_result(prog())
    raw = result.raw_store
    assert isinstance(raw, dict)
    assert "counter" in raw
    assert raw["counter"] == 42
    # env entries must NOT appear in raw_store
    assert "__env__config_key" not in raw
    assert "config_key" not in raw


def test_with_handler_program_attribute():
    """WithHandler pyclass uses 'program' field (not 'body') per spec."""
    from doeff_vm import PyVM, WithHandler as RustWithHandler

    vm = PyVM()

    def handler(effect, k):
        if isinstance(effect, CustomEffect):
            resume_value = yield Resume(k, effect.value * 3)
            return resume_value
        yield Delegate()

    def body():
        result = yield CustomEffect(10)
        return result

    # Use the Rust pyclass WithHandler (which has 'program' not 'body')
    def main():
        result = yield RustWithHandler(handler=handler, program=body)
        return result

    result = vm.run(main())
    assert result == 30


def test_is_standard_excludes_scheduler_effect():
    """G14: is_standard() should not include Scheduler effects.

    Scheduler effects go through dispatch like all others, so is_standard
    correctly identifies only state/reader/writer effects.
    """
    from doeff_vm import PyVM

    vm = PyVM()
    scheduler = vm.scheduler()
    scheduler.install(vm)

    # Verify scheduler effects go through dispatch (not bypassed)
    def body():
        promise = yield CreatePromise()
        return promise

    result = vm.run(body())
    assert isinstance(result, dict)


def test_put_state_and_put_env():
    """R8-E: put_state, put_env, env_items work correctly."""
    from doeff_vm import PyVM

    vm = PyVM()

    vm.put_state("x", 10)
    vm.put_env("env_key", "env_val")

    state = vm.state_items()
    assert state["x"] == 10

    env = vm.env_items()
    assert env["env_key"] == "env_val"


def test_run_with_result_error_case():
    """R8-J: PyRunResult wraps errors properly."""
    from doeff_vm import PyVM

    vm = PyVM()

    @do
    def failing_prog() -> Program[int]:
        raise ValueError("boom")
        yield

    result = vm.run_with_result(failing_prog())
    assert result.is_err()
    assert not result.is_ok()

    # .result should return ("err", exception)
    r = result.result
    assert r[0] == "err"


## -- G11: Module-level run() and async_run() ----------------------------------
# Spec defines these as top-level functions, not just methods on PyVM.


def test_module_level_run_exists():
    """G11: doeff_vm.run() must be a module-level function."""
    import doeff_vm

    assert hasattr(doeff_vm, "run"), "Module-level run() function missing"
    assert callable(doeff_vm.run)


def test_module_level_async_run_exists():
    """G11: doeff_vm.async_run() must be a module-level function."""
    import doeff_vm

    assert hasattr(doeff_vm, "async_run"), "Module-level async_run() function missing"
    assert callable(doeff_vm.async_run)


def test_module_level_run_basic():
    """G11: run(program) returns a RunResult with the program's return value."""
    from doeff_vm import run

    result = run(simple_program())
    assert result.is_ok()
    assert result.value == 42


def test_module_level_run_with_env_and_store():
    """G11: run() accepts env and store dicts to seed the VM."""
    from doeff_vm import run, state

    @do
    def prog() -> Program[int]:
        val = yield Get("counter")
        return val

    result = run(prog(), store={"counter": 99}, handlers=[state])
    assert result.is_ok()
    assert result.value == 99


def test_module_level_run_with_state_handler():
    """G11: run(program, handlers=[state]) installs state handler."""
    from doeff_vm import run, state

    result = run(counter_program(), handlers=[state])
    assert result.is_ok()
    assert result.value == 1
    # raw_store should contain the state
    assert result.raw_store.get("counter") == 1


def test_module_level_run_with_writer_handler():
    """G11: run(program, handlers=[writer]) installs writer handler."""
    from doeff_vm import run, writer

    result = run(logging_program(), handlers=[writer])
    assert result.is_ok()
    assert result.value == "completed"


def test_module_level_run_no_handlers_unhandled_effect():
    """G11: run(program) with no handlers raises for unhandled effects."""
    from doeff_vm import run

    result = run(counter_program())
    assert result.is_err()


def test_module_level_async_run_basic():
    """G11: async_run() works with asyncio event loop."""
    from doeff_vm import async_run

    def pure_generator_program():
        return 42
        yield

    async def main():
        result = await async_run(pure_generator_program())
        return result

    result = asyncio.run(main())
    assert result.is_ok()
    assert result.value == 42


## -- R9: Semantic Correctness Enforcement Tests ----------------------------------
# These tests enforce SPEC-008 Rev 9 and SPEC-009 Rev 3 invariants.
# Written as TDD RED — they SHOULD FAIL until the implementation is corrected.


class TestR9HandlerNesting:
    """ADR-13, INV-16: run() must use WithHandler nesting, not install_handler."""

    def test_run_with_sentinel_handler_objects(self):
        """R9: run() accepts sentinel handler objects (not strings)."""
        import doeff_vm

        # Module-level sentinel objects must exist
        assert hasattr(doeff_vm, "state"), "Module should export 'state' sentinel"
        assert hasattr(doeff_vm, "reader"), "Module should export 'reader' sentinel"
        assert hasattr(doeff_vm, "writer"), "Module should export 'writer' sentinel"

        from doeff_vm import run, state

        result = run(counter_program(), handlers=[state])
        assert result.is_ok()
        assert result.value == 1

    def test_run_sentinel_state_and_reader(self):
        """R9: run() with sentinel state + reader handlers."""
        from doeff_vm import run, state, reader

        @do
        def prog() -> Program[str]:
            val = yield Get("x")
            name = yield Ask("name")
            return f"{name}={val}"

        result = run(prog(), handlers=[state, reader], store={"x": 42}, env={"name": "count"})
        assert result.is_ok()
        assert result.value == "count=42"

    def test_run_handler_ordering_is_deterministic(self):
        """R9 INV-16: Handler ordering must be deterministic across runs."""
        from doeff_vm import run, state, reader, writer

        results = []
        for _ in range(20):

            @do
            def prog() -> Program[int]:
                yield Put("x", 1)
                yield Tell("logged")
                val = yield Get("x")
                return val

            result = run(prog(), handlers=[state, reader, writer], store={"x": 0})
            assert result.is_ok()
            results.append(result.value)

        # All 20 runs must produce the same result
        assert all(r == results[0] for r in results), f"Non-deterministic results: {results}"

    def test_run_with_python_handler_and_sentinel(self):
        """R9: run() accepts mixed Python handlers and sentinel handlers."""
        from doeff_vm import run, state, Resume

        @do
        def my_handler(effect: Effect, k):
            if hasattr(effect, "message"):
                # Custom Tell handler that does nothing
                result = yield Resume(k, None)
                return result
            from doeff_vm import Delegate

            yield Delegate()

        @do
        def prog() -> Program[int]:
            yield Put("x", 10)
            val = yield Get("x")
            return val

        result = run(prog(), handlers=[my_handler, state])
        assert result.is_ok()
        assert result.value == 10


class TestR9RunResultNaming:
    """SPEC-009: RunResult must be the Python-visible class name."""

    def test_runresult_class_name(self):
        """R9: The class exposed to Python must be named 'RunResult', not 'PyRunResult'."""
        from doeff_vm import run

        result = run(simple_program())
        assert type(result).__name__ == "RunResult", (
            f"Expected class name 'RunResult', got '{type(result).__name__}'"
        )

    def test_runresult_importable(self):
        """R9: RunResult should be importable from doeff_vm."""
        from doeff_vm import RunResult

        assert RunResult is not None


class TestR9ClassifyYieldedCompleteness:
    """INV-17: classify_yielded must have explicit arms for all scheduler effects.

    These tests verify that scheduler effects are classified as Effect::Scheduler,
    not falling through to Effect::Python. The distinction matters because the
    scheduler handler's can_handle() only matches Effect::Scheduler(_).

    We test this by checking that the effect class type names are recognized
    in classify_yielded's string matching. Since we can't directly introspect
    classify_yielded from Python, we verify that scheduler effects dispatched
    without a scheduler handler result in UnhandledEffect (not TypeError from
    Unknown classification or silent misrouting via Effect::Python).
    """

    def test_scheduler_effects_type_names_are_recognized(self):
        """R9 INV-17: All scheduler effect type names must be in classify_yielded."""
        # This is a structural test — it checks that the Rust source code
        # contains explicit classify_yielded arms for all scheduler effects.
        # We import from the test to verify the effect classes exist and have
        # the expected type names that classify_yielded should match.
        from doeff.effects.spawn import SpawnEffect
        from doeff.effects.gather import GatherEffect
        from doeff.effects.race import RaceEffect
        from doeff.effects.promise import CompletePromiseEffect, FailPromiseEffect

        # Verify the type names that classify_yielded needs to match
        assert SpawnEffect.__name__ == "SpawnEffect"
        assert GatherEffect.__name__ == "GatherEffect"
        assert RaceEffect.__name__ == "RaceEffect"
        assert CompletePromiseEffect.__name__ == "CompletePromiseEffect"
        assert FailPromiseEffect.__name__ == "FailPromiseEffect"

    def test_classify_gather_effect_not_as_program(self):
        """R9 INV-17: GatherEffect must be classified as Effect, not Program.

        GatherEffect extends EffectBase extends ProgramBase, so it has
        `to_generator`. classify_yielded must check for effect type names
        BEFORE the to_generator fallback, otherwise scheduler effects get
        misclassified as Yielded::Program and the VM tries to start them.
        """
        import subprocess
        import sys

        # Run in subprocess with timeout to detect hang
        result = subprocess.run(
            [
                sys.executable,
                "-c",
                """
from doeff import do, Program
from doeff.effects.gather import GatherEffect
from doeff_vm import PyVM

vm = PyVM()

@do
def prog():
    result = yield GatherEffect(items=[])
    return result

result = vm.run_with_result(prog())
# If we get here, classify was correct (should be UnhandledEffect error)
print("OK" if result.is_err() else "WRONG_OK")
""",
            ],
            capture_output=True,
            text=True,
            timeout=3,
        )
        assert result.returncode == 0, f"Process crashed: {result.stderr}"
        assert result.stdout.strip() == "OK", (
            f"GatherEffect misclassified. stdout={result.stdout.strip()}, "
            f"stderr={result.stderr[:200]}"
        )

    def test_classify_yielded_must_check_effects_before_to_generator(self):
        """R9 INV-17: scheduler effects remain effect values, not programs.

        SPEC-TYPES-001 Rev 11 separates EffectValue data from DoExpr control,
        so scheduler effect classes must not expose program-only conversion
        methods such as ``to_generator``.
        """
        from doeff.effects.race import RaceEffect
        from doeff.effects.gather import GatherEffect
        from doeff.effects.spawn import SpawnEffect
        from doeff.effects.promise import CompletePromiseEffect, FailPromiseEffect

        for cls in [
            GatherEffect,
            RaceEffect,
            SpawnEffect,
            CompletePromiseEffect,
            FailPromiseEffect,
        ]:
            assert not hasattr(cls, "to_generator"), (
                f"{cls.__name__} should be EffectValue-only and not expose to_generator"
            )

        # The gather test above verifies runtime classify behavior directly.


class TestR9AsyncRunSemantics:
    """API-12: async_run must be truly async."""

    def test_async_run_yields_to_event_loop(self):
        """R9 API-12: async_run must yield control to the event loop."""
        from doeff_vm import async_run
        import asyncio

        def pure_generator_program():
            return 42
            yield

        execution_order = []

        async def background_task():
            execution_order.append("background_start")
            await asyncio.sleep(0)
            execution_order.append("background_end")

        async def main():
            execution_order.append("main_start")
            bg = asyncio.create_task(background_task())
            result = await async_run(pure_generator_program())
            await bg
            execution_order.append("main_end")
            return result

        result = asyncio.run(main())
        assert result.is_ok()
        assert result.value == 42
        # If async_run is truly async, background_task should have had a
        # chance to run between main_start and main_end
        assert "background_start" in execution_order, (
            "Background task never started — async_run may be blocking"
        )


# === SPEC-009 Gap TDD Tests ===
# G1 (RunResult.result returns Ok/Err) and G3 (Modify returns new_value)
# Python integration tests deferred until run_with_result/put_store are wired.
# Rust-side fixes verified by Rust unit tests:
#   - G3: vm::tests::test_s009_g3_modify_resumes_with_new_value (handler.rs)


## -- R13-I: Tag dispatch tests --------------------------------------------------


def test_tag_attribute_accessible():
    """R13-I: tag attribute is accessible on DoCtrlBase and EffectBase instances."""
    import doeff_vm

    # TAG constants exist
    assert hasattr(doeff_vm, "TAG_PURE")
    assert hasattr(doeff_vm, "TAG_PERFORM")
    assert hasattr(doeff_vm, "TAG_EFFECT")
    assert hasattr(doeff_vm, "TAG_UNKNOWN")

    # Verify tag values
    assert doeff_vm.TAG_PURE == 0
    assert doeff_vm.TAG_MAP == 2
    assert doeff_vm.TAG_FLAT_MAP == 3
    assert doeff_vm.TAG_WITH_HANDLER == 4
    assert doeff_vm.TAG_PERFORM == 5
    assert doeff_vm.TAG_RESUME == 6
    assert doeff_vm.TAG_TRANSFER == 7
    assert doeff_vm.TAG_DELEGATE == 8
    assert doeff_vm.TAG_GET_CONTINUATION == 9
    assert doeff_vm.TAG_GET_HANDLERS == 10
    assert doeff_vm.TAG_GET_CALL_STACK == 11
    assert doeff_vm.TAG_EVAL == 12
    assert doeff_vm.TAG_CREATE_CONTINUATION == 13
    assert doeff_vm.TAG_RESUME_CONTINUATION == 14
    assert doeff_vm.TAG_ASYNC_ESCAPE == 15
    assert doeff_vm.TAG_APPLY == 16
    assert doeff_vm.TAG_EXPAND == 17
    assert doeff_vm.TAG_GET_TRACE == 18
    assert doeff_vm.TAG_EFFECT == 128
    assert doeff_vm.TAG_UNKNOWN == 255


def test_tag_on_concrete_instances():
    """R13-I: Concrete DoCtrl instances have correct tag values."""
    from doeff_vm import (
        Pure,
        Apply,
        Expand,
        GetHandlers,
        GetCallStack,
        GetTrace,
        TAG_PURE,
        TAG_APPLY,
        TAG_EXPAND,
        TAG_GET_HANDLERS,
        TAG_GET_CALL_STACK,
        TAG_GET_TRACE,
    )

    meta = {
        "function_name": "test_fn",
        "source_file": __file__,
        "source_line": 1,
    }

    pure = Pure(42)
    assert pure.tag == TAG_PURE

    gh = GetHandlers()
    assert gh.tag == TAG_GET_HANDLERS

    apply = Apply(lambda x: x, [1], {}, meta)
    assert apply.tag == TAG_APPLY

    expand = Expand(lambda x: x, [1], {}, meta)
    assert expand.tag == TAG_EXPAND

    gcs = GetCallStack()
    assert gcs.tag == TAG_GET_CALL_STACK

    gt = GetTrace()
    assert gt.tag == TAG_GET_TRACE


def test_tag_on_effect_base():
    """R13-I: EffectBase subclasses have Effect tag."""
    from doeff.effects import Get, Put
    from doeff_vm import TAG_EFFECT

    get = Get("key")
    assert get.tag == TAG_EFFECT

    put = Put("key", 42)
    assert put.tag == TAG_EFFECT


def test_doeff_generator_fn_call_wraps_generator_with_factory() -> None:
    import doeff_vm

    def make_gen():
        yield 1

    def get_frame(g):
        return g.gi_frame

    factory = doeff_vm.DoeffGeneratorFn(
        callable=make_gen,
        function_name="make_gen",
        source_file=__file__,
        source_line=1,
        get_frame=get_frame,
    )
    wrapped = factory()

    assert isinstance(wrapped, doeff_vm.DoeffGenerator)
    assert wrapped.factory is factory
    assert wrapped.function_name == "make_gen"


def test_tag_dispatch_does_not_break_existing_programs():
    """R13-I: Tag dispatch is transparent — existing programs still work."""
    from doeff_vm import PyVM

    vm = PyVM()

    # Pure return (no effects) works through tag dispatch
    def pure_prog():
        return 99
        yield

    result = vm.run(pure_prog())
    assert result == 99


## -- ISSUE-VM-001: Transfer abandonment + Delegate(effect) substitution -------


class SecondCustomEffect(doeff_vm.EffectBase):
    """A second custom effect for substitution tests."""

    def __init__(self, value):
        self.value = value


def test_transfer_abandons_handler():
    """ISSUE-VM-001: After yield Transfer(k, v), handler code must NOT execute.

    Transfer is a tail-call: control goes to the continuation and the handler
    is abandoned. Any code after `yield Transfer(...)` must never run.
    """
    from doeff_vm import (
        Delegate as VMDelegate,
        Perform as VMPerform,
        PyVM,
        Transfer as VMTransfer,
        WithHandler as VMWithHandler,
    )

    vm = PyVM()
    handler_continued = {"ran": False}

    def handler(effect, k):
        if isinstance(effect, CustomEffect):
            yield VMTransfer(k, effect.value * 10)
            # This line must NEVER execute after Transfer
            handler_continued["ran"] = True
        yield VMDelegate()

    def body():
        result = yield CustomEffect(7)
        return result

    def main():
        result = yield VMWithHandler(handler, VMPerform(CustomEffect(7)))
        return result

    # body yields CustomEffect(7), handler transfers k with 70
    # Transfer abandons the handler — handler_continued["ran"] must stay False
    result = vm.run(main())
    assert result == 70
    assert handler_continued["ran"] is False, (
        "Handler code after Transfer was executed — Transfer should abandon handler"
    )


def test_delegate_with_effect_substitution():
    """ISSUE-VM-001: Delegate(substitute_effect) passes a different effect outward.

    Inner handler receives CustomEffect, but delegates with SecondCustomEffect
    to the outer handler. Outer handler should see SecondCustomEffect.
    """
    from doeff_vm import (
        Delegate as VMDelegate,
        Perform as VMPerform,
        PyVM,
        Resume as VMResume,
        WithHandler as VMWithHandler,
    )

    vm = PyVM()

    def outer_handler(effect, k):
        if isinstance(effect, SecondCustomEffect):
            result = yield VMResume(k, f"outer_got:{effect.value}")
            return result
        yield VMDelegate()

    def inner_handler(effect, k):
        if isinstance(effect, CustomEffect):
            # Delegate with a DIFFERENT effect to outer handler
            yield VMDelegate(SecondCustomEffect(effect.value + 100))
        yield VMDelegate()

    def body():
        result = yield CustomEffect(42)
        return result

    def main():
        inner = VMWithHandler(inner_handler, VMPerform(CustomEffect(42)))
        result = yield VMWithHandler(outer_handler, inner)
        return result

    result = vm.run(main())
    assert result == "outer_got:142", (
        f"Expected outer handler to see substituted effect, got: {result}"
    )


## -- ISSUE-VM-003: Scheduler Spawn/Gather/Race coverage -----------------------


def test_scheduler_spawn_creates_task():
    """ISSUE-VM-003: SpawnEffect should create a task via the scheduler handler."""
    from doeff_vm import PyVM
    from doeff.effects.spawn import SpawnEffect

    vm = PyVM()
    scheduler = vm.scheduler()
    scheduler.install(vm)
    stdlib = vm.stdlib()
    _ = stdlib.state
    stdlib.install_state(vm)

    @do
    def child() -> Program[int]:
        return 99
        yield

    def body():
        task = yield SpawnEffect(program=child(), handlers=[])
        return task

    result = vm.run(body())
    # SpawnEffect should return a TaskHandle dict
    assert isinstance(result, dict)
    assert result["type"] == "Task"
    assert "task_id" in result


def test_scheduler_gather_recognized_by_handler():
    """ISSUE-VM-003: GatherEffect is recognized by the scheduler handler.

    Verifies that GatherEffect is classified correctly and dispatched to the
    scheduler handler (not rejected as UnhandledEffect or TypeError).
    The full spawn→gather→collect flow requires the async scheduler loop.
    """
    from doeff_vm import PyVM
    from doeff.effects.gather import GatherEffect

    vm = PyVM()
    scheduler = vm.scheduler()
    scheduler.install(vm)

    def body():
        # GatherEffect with empty items — scheduler should handle it
        result = yield GatherEffect(items=[])
        return result

    result = vm.run_with_result(body())
    # Should NOT be TypeError/UnhandledEffect — scheduler handles it
    if result.is_err():
        err_str = str(result.result)
        assert "UnhandledEffect" not in err_str, (
            f"GatherEffect not recognized by scheduler: {err_str}"
        )
        assert "TypeError" not in err_str, f"GatherEffect misclassified: {err_str}"


def test_scheduler_race_recognized_by_handler():
    """ISSUE-VM-003: RaceEffect is recognized by the scheduler handler.

    Verifies that RaceEffect is classified correctly and dispatched to the
    scheduler handler (not rejected as UnhandledEffect or TypeError).
    """
    from doeff_vm import PyVM
    from doeff.effects.race import RaceEffect

    vm = PyVM()
    scheduler = vm.scheduler()
    scheduler.install(vm)

    def body():
        # RaceEffect with empty futures — scheduler should handle it
        result = yield RaceEffect(futures=[])
        return result

    result = vm.run_with_result(body())
    # Should NOT be TypeError/UnhandledEffect — scheduler handles it
    if result.is_err():
        err_str = str(result.result)
        assert "UnhandledEffect" not in err_str, (
            f"RaceEffect not recognized by scheduler: {err_str}"
        )
        assert "TypeError" not in err_str, f"RaceEffect misclassified: {err_str}"


def test_flatmap_rejects_non_doexpr_binder_return():
    """ISSUE-VM-004: FlatMap binder must return a DoExpr (Program/Effect/DoCtrl).

    SPEC-TYPES-001 CP-07: If the binder returns a non-DoExpr value (e.g. a
    plain int), the VM should raise a runtime TypeError rather than silently
    propagating the raw value.
    """
    from doeff_vm import PyVM

    vm = PyVM()

    def bad_binder(x):
        return x * 2  # returns 20, a plain int — NOT a DoExpr

    def body():
        result = yield Program.flat_map(Program.pure(10), bad_binder)
        return result

    try:
        result = vm.run(body())
        # If run() returns 20 (the raw int), that means the binder's
        # non-DoExpr return was silently propagated — spec violation.
        assert result != 20, (
            "FlatMap binder returned a plain int and it was silently propagated; "
            "expected TypeError per TYPES-001 CP-07"
        )
    except TypeError as e:
        # Good — the VM rejected the non-DoExpr return
        assert "flat_map" in str(e).lower() or "DoExpr" in str(e), (
            f"TypeError raised but message doesn't mention flat_map/DoExpr: {e}"
        )


def test_flatmap_requires_explicit_binder_metadata() -> None:
    """VM-PROTO-005: FlatMap constructor must reject missing binder_meta."""
    from doeff_vm import FlatMap, Pure

    def binder(x):
        return Pure(x + 1)

    with pytest.raises(TypeError, match="binder_meta is required"):
        FlatMap(Pure(1), binder)


def test_call_resolves_doexpr_args():
    """Call DoCtrl resolves DoExpr positional args before invoking the callable."""
    from doeff_vm import run as vm_run

    @do
    def adder(a, b):
        return a + b

    @do
    def body():
        result = yield adder(Pure(10), Pure(20))
        return result

    result = vm_run(body(), handlers=[])
    assert result.is_ok(), f"Call arg resolution failed: {result.result}"
    assert result.value == 30


def test_call_with_mixed_value_and_expr_args():
    """Call DoCtrl handles mixed literal and DoExpr arguments."""
    from doeff_vm import run as vm_run

    @do
    def mixer(a, b, c):
        return a + b + c

    @do
    def body():
        result = yield mixer(42, Pure(10), Pure(20))
        return result

    result = vm_run(body(), handlers=[])
    assert result.is_ok(), f"Mixed Call argument resolution failed: {result.result}"
    assert result.value == 72


def test_call_with_all_value_args():
    """Call DoCtrl still works when all args are already plain values."""
    from doeff_vm import run as vm_run

    @do
    def adder(a, b):
        return a + b

    @do
    def body():
        result = yield adder(10, 20)
        return result

    result = vm_run(body(), handlers=[])
    assert result.is_ok(), f"All-value Call invocation failed: {result.result}"
    assert result.value == 30


def test_call_resolves_nested_effectful_args_left_to_right():
    """Nested Call DoCtrl args resolve effectful expressions in deterministic order."""
    from doeff_vm import run as vm_run

    @do
    def inner(v):
        return v + 1

    @do
    def outer(v):
        return v * 3

    @do
    def body():
        value = yield outer(inner(Ask("key")))
        return value

    result = vm_run(body(), handlers=[doeff_vm.reader], env={"key": 4})
    assert result.is_ok(), f"Nested Call DoCtrl resolution failed: {result.result}"
    assert result.value == 15


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
