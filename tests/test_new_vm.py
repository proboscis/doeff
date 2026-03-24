"""Tests for the new doeff VM — Python bridge."""

import pytest
from doeff_vm import PyVM, K


@pytest.fixture
def vm():
    return PyVM()


# ---------------------------------------------------------------------------
# Basic programs
# ---------------------------------------------------------------------------

class TestBasicPrograms:
    def test_return_int(self, vm):
        def prog():
            return 42
            yield
        assert vm.run(prog()) == 42

    def test_return_string(self, vm):
        def prog():
            return "hello"
            yield
        assert vm.run(prog()) == "hello"

    def test_return_none(self, vm):
        def prog():
            return None
            yield
        assert vm.run(prog()) is None

    def test_return_bool(self, vm):
        def prog():
            return True
            yield
        assert vm.run(prog()) == True

    def test_yield_pure_then_return(self, vm):
        """Generator yields Pure, gets value back, returns."""
        class Pure:
            tag = 0
            def __init__(self, value):
                self.value = value

        def prog():
            x = yield Pure(99)
            return x
        assert vm.run(prog()) == 99


# ---------------------------------------------------------------------------
# Effect handlers — Perform + Resume
# ---------------------------------------------------------------------------

class Resume:
    tag = 6
    def __init__(self, k, value):
        self.continuation = k
        self.value = value


class Transfer:
    tag = 7
    def __init__(self, k, value):
        self.continuation = k
        self.value = value


class TestEffectHandlers:
    def test_perform_resume(self, vm):
        """Handler resumes with a value."""
        class Ask:
            pass

        def handler(effect, k):
            result = yield Resume(k, 100)
            return result

        def body():
            result = yield Ask()
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
            x = yield Get()
            return x * 2

        assert vm.run_with_handler(handler, body()) == 20

    def test_perform_transfer(self, vm):
        """Handler transfers (tail position)."""
        class Get:
            pass

        def handler(effect, k):
            yield Transfer(k, 77)

        def body():
            result = yield Get()
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
            a = yield Get()
            b = yield Get()
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
            result = yield Add(3, 4)
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
            x = yield Get()
            return x + 1

        result = vm.run_with_handler(handler, body())
        assert result == 43
        assert handler_saw == 43


# ---------------------------------------------------------------------------
# Nested handlers
# ---------------------------------------------------------------------------

class TestNestedHandlers:
    def test_inner_handles(self, vm):
        """Inner handler handles, outer untouched."""
        class Get:
            pass

        def inner(effect, k):
            result = yield Resume(k, 42)
            return result

        def body():
            result = yield Get()
            return result

        assert vm.run_with_handler(inner, body()) == 42


# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------

class TestErrors:
    def test_body_exception_propagates(self, vm):
        def body():
            raise RuntimeError("boom")
            yield

        with pytest.raises(RuntimeError):
            vm.run(body())

    def test_no_handler_error(self, vm):
        """Performing without a handler raises an error."""
        class MyEffect:
            pass

        def body():
            yield MyEffect()

        with pytest.raises(RuntimeError, match="no handler"):
            vm.run(body())
