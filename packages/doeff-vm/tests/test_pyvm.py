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


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
