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


class CustomEffect:
    """Custom effect for testing Python handlers."""
    def __init__(self, value):
        self.value = value


def make_custom_handler():
    """Create a handler that doubles the effect value."""
    def handler(effect, k):
        if isinstance(effect, CustomEffect):
            resume_value = yield Resume(k, effect.value * 2)
            return resume_value
        else:
            raise ValueError(f"Unknown effect: {effect}")
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
    """Create a handler that counts effect invocations."""
    state = {"count": 0}
    
    def handler(effect, k):
        if isinstance(effect, CustomEffect):
            state["count"] += 1
            resume_value = yield Resume(k, state["count"])
            return resume_value
        else:
            raise ValueError(f"Unknown effect: {effect}")
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


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
