"""Tests for WithObserve — effect observation."""

from doeff import do, run as doeff_run, WithHandler, WithObserve
from doeff_vm import EffectBase, Callable
from doeff.program import Resume, Pass


class TestObserve:
    def test_observer_sees_effects(self):
        """Observer is called on every effect."""
        class Ask(EffectBase):
            def __init__(self, key):
                super().__init__()
                self.key = key

        seen = []

        def observer(effect):
            seen.append(f"seen:{effect.key}")

        @do
        def handler(effect, k):
            result = yield Resume(k, f"val:{effect.key}")
            return result

        @do
        def body():
            x = yield Ask("a")
            y = yield Ask("b")
            return (x, y)

        result = doeff_run(WithHandler(handler, WithObserve(Callable(observer), body())))
        assert result == ("val:a", "val:b")
        assert seen == ["seen:a", "seen:b"]

    def test_observer_does_not_modify_effect(self):
        """Observer return value is ignored — original effect proceeds."""
        class Ask(EffectBase):
            def __init__(self, key):
                super().__init__()
                self.key = key

        def observer(effect):
            return "this is ignored"

        @do
        def handler(effect, k):
            result = yield Resume(k, effect.key)
            return result

        @do
        def body():
            return (yield Ask("original"))

        result = doeff_run(WithHandler(handler, WithObserve(Callable(observer), body())))
        assert result == "original"

    def test_observer_with_multiple_effect_types(self):
        """Observer sees all effect types."""
        class Ask(EffectBase):
            def __init__(self, key):
                super().__init__()
                self.key = key

        class Log(EffectBase):
            def __init__(self, msg):
                super().__init__()
                self.msg = msg

        seen = []

        def observer(effect):
            seen.append(type(effect).__name__)

        @do
        def handler(effect, k):
            result = yield Resume(k, None)
            return result

        @do
        def body():
            yield Log("hello")
            yield Ask("key")
            yield Log("world")
            return "done"

        result = doeff_run(WithHandler(handler, WithObserve(Callable(observer), body())))
        assert result == "done"
        assert seen == ["Log", "Ask", "Log"]

    def test_nested_observers(self):
        """Multiple observers all see effects."""
        class Ask(EffectBase):
            def __init__(self, key):
                super().__init__()
                self.key = key

        log_inner = []
        log_outer = []

        def inner_obs(effect):
            log_inner.append("inner")

        def outer_obs(effect):
            log_outer.append("outer")

        @do
        def handler(effect, k):
            result = yield Resume(k, 42)
            return result

        @do
        def body():
            return (yield Ask("x"))

        result = doeff_run(
            WithHandler(handler,
                WithObserve(Callable(outer_obs),
                    WithObserve(Callable(inner_obs), body())))
        )
        assert result == 42
        assert log_inner == ["inner"]
        assert log_outer == ["outer"]

    def test_observer_at_outer_position(self):
        """Observer outside handler still sees effects handled by inner handler."""
        class Ask(EffectBase):
            def __init__(self, key):
                super().__init__()
                self.key = key

        seen = []

        def observer(effect):
            seen.append(f"observed:{effect.key}")

        @do
        def handler(effect, k):
            result = yield Resume(k, "handled")
            return result

        @do
        def body():
            return (yield Ask("x"))

        # Observer is OUTSIDE handler — should still see the effect
        result = doeff_run(
            WithObserve(Callable(observer),
                WithHandler(handler, body()))
        )
        assert result == "handled"
        assert seen == ["observed:x"]
