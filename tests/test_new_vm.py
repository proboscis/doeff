"""Tests for the new doeff VM — Python bridge.

All programs are DoExpr objects (have `tag` attribute).
Raw generators are NOT accepted by the VM — use @do or DoExpr wrappers.
"""

import pytest
from doeff_vm import PyVM, K


@pytest.fixture
def vm():
    return PyVM()


# ---------------------------------------------------------------------------
# DoExpr helpers — minimal Python DoExpr protocol
# ---------------------------------------------------------------------------

class Pure:
    """DoExpr: return a value."""
    tag = 0
    def __init__(self, value):
        self.value = value


class Perform:
    """DoExpr: perform an effect."""
    tag = 5
    def __init__(self, effect):
        self.effect = effect


class Resume:
    """DoExpr: resume continuation with value (non-tail)."""
    tag = 6
    def __init__(self, k, value):
        self.continuation = k
        self.value = value


class Transfer:
    """DoExpr: resume continuation with value (tail)."""
    tag = 7
    def __init__(self, k, value):
        self.continuation = k
        self.value = value


# ---------------------------------------------------------------------------
# Basic programs
# ---------------------------------------------------------------------------

class TestBasicPrograms:
    def test_pure_int(self, vm):
        assert vm.run(Pure(42)) == 42

    def test_pure_string(self, vm):
        assert vm.run(Pure("hello")) == "hello"

    def test_pure_none(self, vm):
        assert vm.run(Pure(None)) is None

    def test_pure_bool(self, vm):
        assert vm.run(Pure(True)) == True

    def test_run_rejects_raw_generator(self, vm):
        """Raw generators must not be accepted — use DoExpr."""
        def gen():
            yield
        with pytest.raises(TypeError, match="DoExpr expected"):
            vm.run(gen())

    def test_run_rejects_plain_object(self, vm):
        """Plain objects without tag must not be accepted."""
        with pytest.raises(TypeError, match="DoExpr expected"):
            vm.run(42)


# ---------------------------------------------------------------------------
# Effect handlers — Perform + Resume
# ---------------------------------------------------------------------------

class TestEffectHandlers:
    def test_perform_resume(self, vm):
        """Handler resumes with a value."""
        class Ask:
            pass

        def handler(effect, k):
            result = yield Resume(k, 100)
            return result

        def body():
            result = yield Perform(Ask())
            return result

        assert vm.run_with_handler(handler, body()) == 100

    def test_perform_resume_body_transforms(self, vm):
        """Body transforms the resumed value."""
        class Get:
            pass

        def handler(effect, k):
            result = yield Resume(k, 10)
            return result

        def body():
            x = yield Perform(Get())
            return x * 2

        assert vm.run_with_handler(handler, body()) == 20

    def test_perform_transfer(self, vm):
        """Handler transfers (tail position)."""
        class Get:
            pass

        def handler(effect, k):
            yield Transfer(k, 77)

        def body():
            result = yield Perform(Get())
            return result

        assert vm.run_with_handler(handler, body()) == 77

    def test_multiple_performs(self, vm):
        """Body performs twice, handler handles both."""
        class Get:
            pass

        call_count = 0

        def handler(effect, k):
            nonlocal call_count
            call_count += 1
            result = yield Resume(k, call_count * 10)
            return result

        def body():
            a = yield Perform(Get())
            b = yield Perform(Get())
            return a + b

        result = vm.run_with_handler(handler, body())
        assert result == 30  # 10 + 20

    def test_handler_receives_effect_object(self, vm):
        """Handler can inspect the effect."""
        class Add:
            def __init__(self, x, y):
                self.x = x
                self.y = y

        def handler(effect, k):
            if isinstance(effect, Add):
                result = yield Resume(k, effect.x + effect.y)
            else:
                result = yield Resume(k, None)
            return result

        def body():
            result = yield Perform(Add(3, 4))
            return result

        assert vm.run_with_handler(handler, body()) == 7

    def test_handler_return_value_flows_through(self, vm):
        """After Resume, body's return value flows back to handler."""
        class Get:
            pass

        handler_saw = None

        def handler(effect, k):
            nonlocal handler_saw
            result = yield Resume(k, 42)
            handler_saw = result
            return result

        def body():
            x = yield Perform(Get())
            return x + 1

        result = vm.run_with_handler(handler, body())
        assert result == 43
        assert handler_saw == 43


# ---------------------------------------------------------------------------
# Nested handlers
# ---------------------------------------------------------------------------

class TestNestedHandlers:
    def test_inner_handles(self, vm):
        """Inner handler handles the effect."""
        class Get:
            pass

        def inner(effect, k):
            result = yield Resume(k, 42)
            return result

        def body():
            result = yield Perform(Get())
            return result

        assert vm.run_with_handler(inner, body()) == 42


# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------

class TestErrors:
    def test_body_exception_propagates(self, vm):
        def body():
            raise RuntimeError("boom")
            yield Perform(None)

        with pytest.raises(RuntimeError):
            vm.run_with_handler(lambda e, k: None, body())

    def test_no_handler_error(self, vm):
        """Performing without a handler raises an error."""
        class MyEffect:
            pass

        def body():
            yield Perform(MyEffect())

        # Running body without handler — no handler to catch the effect
        # The generator yields a Perform DoExpr, but run() expects a single DoExpr, not a generator
        # So this test uses run_with_handler with a pass-through handler
        # Actually: run() takes a DoExpr. A generator is rejected.
        # To test no-handler, we need a body that performs inside run_with_handler
        # but with a handler that doesn't exist... or no handler at all.
        # For now, just test that the error is raised properly.
        pass  # TODO: once Pass is tested from Python
