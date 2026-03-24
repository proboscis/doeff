"""Tests for the new doeff VM — Python bridge.

All programs are DoExpr objects (have `tag` attribute).
Raw generators are NOT accepted by the VM — use @do or DoExpr wrappers.
"""

import pytest
from doeff_vm import PyVM, K, Callable, EffectBase


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


class Expand:
    """DoExpr: evaluate inner expr to Stream, then run it."""
    tag = 17
    def __init__(self, expr):
        self.expr = expr


class Apply:
    """DoExpr: call f(args)."""
    tag = 16
    def __init__(self, f, args):
        self.f = f
        self.args = args


def program(gen_fn, *args):
    """Create a DoExpr that runs a generator function.

    This is the minimal equivalent of @do — wraps a generator factory
    as Expand(Apply(Callable(factory), args)).

    The factory must be explicitly wrapped as Callable — no auto-detection.
    """
    return Expand(Apply(Pure(Callable(gen_fn)), [Pure(a) for a in args]))


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

        assert vm.run_with_handler(handler, program(body)) == 100

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

        assert vm.run_with_handler(handler, program(body)) == 20

    def test_perform_transfer(self, vm):
        """Handler transfers (tail position)."""
        class Get:
            pass

        def handler(effect, k):
            yield Transfer(k, 77)

        def body():
            result = yield Perform(Get())
            return result

        assert vm.run_with_handler(handler, program(body)) == 77

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

        result = vm.run_with_handler(handler, program(body))
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

        assert vm.run_with_handler(handler, program(body)) == 7

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

        result = vm.run_with_handler(handler, program(body))
        assert result == 43
        assert handler_saw == 43

    def test_implicit_perform_effect_base(self, vm):
        """Yielding an EffectBase directly is treated as Perform(effect)."""
        class Ask(EffectBase):
            def __init__(self, key):
                super().__init__()
                self.key = key

        def handler(effect, k):
            if isinstance(effect, Ask):
                result = yield Resume(k, f"value_for_{effect.key}")
            else:
                result = yield Resume(k, None)
            return result

        def body():
            # No Perform() wrapper — EffectBase is implicitly Perform'd
            result = yield Ask("config")
            return result

        assert vm.run_with_handler(handler, program(body)) == "value_for_config"


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

        assert vm.run_with_handler(inner, program(body)) == 42


# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------

class TestErrors:
    def test_body_exception_propagates(self, vm):
        def body():
            raise RuntimeError("boom")
            yield Perform(None)

        with pytest.raises(RuntimeError):
            vm.run_with_handler(lambda e, k: None, program(body))

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


# ---------------------------------------------------------------------------
# Traceback
# ---------------------------------------------------------------------------

class GetTraceback:
    """DoExpr: query traceback from continuation. tag=23"""
    tag = 23
    def __init__(self, k):
        self.continuation = k


class TestTraceback:
    def test_get_traceback_from_handler(self, vm):
        """Handler can query traceback from continuation without consuming it."""
        class Ask(EffectBase):
            def __init__(self, key):
                super().__init__()
                self.key = key

        captured_traceback = None

        def handler(effect, k):
            nonlocal captured_traceback
            # Query traceback — does NOT consume k
            captured_traceback = yield GetTraceback(k)
            # k is still alive — resume it
            result = yield Resume(k, "answer")
            return result

        def inner():
            return (yield Ask("question"))

        def outer():
            return (yield program(inner))

        result = vm.run_with_handler(handler, program(outer))
        assert result == "answer"
        assert captured_traceback is not None
        assert isinstance(captured_traceback, list)
        # Should have at least one frame (inner's generator)
        assert len(captured_traceback) >= 1
        # Each frame is [func_name, source_file, source_line]
        frame = captured_traceback[0]
        assert "inner" in frame[0]  # func_name (may include qualname prefix)
        assert isinstance(frame[2], int)  # source_line

    def test_traceback_shows_call_chain(self, vm):
        """Traceback includes frames from nested generators."""
        class Ask(EffectBase):
            def __init__(self):
                super().__init__()

        captured_traceback = None

        def handler(effect, k):
            nonlocal captured_traceback
            captured_traceback = yield GetTraceback(k)
            result = yield Resume(k, 42)
            return result

        def leaf():
            return (yield Ask())

        def middle():
            return (yield program(leaf))

        def root():
            return (yield program(middle))

        result = vm.run_with_handler(handler, program(root))
        assert result == 42
        # Should see: leaf, middle, root (innermost first)
        func_names = [f[0] for f in captured_traceback]
        assert any("leaf" in n for n in func_names)
        assert any("middle" in n for n in func_names)
        assert any("root" in n for n in func_names)
