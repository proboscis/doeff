"""Tests for WithIntercept — effect interception."""

from doeff import do, run as doeff_run, WithHandler, WithIntercept, Perform
from doeff_vm import EffectBase, Callable
from doeff.program import Resume, Pass


class TestIntercept:
    def test_passthrough(self):
        """Interceptor returns same effect → passes through to handler."""
        class Ask(EffectBase):
            def __init__(self, key):
                super().__init__()
                self.key = key

        def interceptor(effect):
            return effect  # passthrough

        @do
        def handler(effect, k):
            result = yield Resume(k, f"handled:{effect.key}")
            return result

        @do
        def body():
            return (yield Ask("x"))

        result = doeff_run(WithHandler(handler, WithIntercept(Callable(interceptor), body())))
        assert result == "handled:x"

    def test_substitute_effect(self):
        """Interceptor returns different effect → substituted."""
        class Ask(EffectBase):
            def __init__(self, key):
                super().__init__()
                self.key = key

        def interceptor(effect):
            if isinstance(effect, Ask) and effect.key == "secret":
                return Ask("public")
            return effect

        @do
        def handler(effect, k):
            result = yield Resume(k, f"val:{effect.key}")
            return result

        @do
        def body():
            return (yield Ask("secret"))

        result = doeff_run(WithHandler(handler, WithIntercept(Callable(interceptor), body())))
        assert result == "val:public"

    import pytest
    @pytest.mark.xfail(reason="Multiple effects with interceptor — topology issue after Resume")
    def test_intercept_multiple_effects(self):
        """Interceptor transforms some effects, passes others."""
        class Ask(EffectBase):
            def __init__(self, key):
                super().__init__()
                self.key = key

        class Log(EffectBase):
            def __init__(self, msg):
                super().__init__()
                self.msg = msg

        logged = []

        def interceptor(effect):
            if isinstance(effect, Ask) and effect.key == "override":
                return Ask("replaced")
            return effect  # passthrough for Log and other Ask

        @do
        def handler(effect, k):
            if isinstance(effect, Log):
                logged.append(effect.msg)
                result = yield Resume(k, None)
                return result
            if isinstance(effect, Ask):
                result = yield Resume(k, f"val:{effect.key}")
                return result
            yield Pass(effect, k)

        @do
        def body():
            yield Log("start")
            x = yield Ask("override")
            y = yield Ask("normal")
            yield Log(f"got {x} and {y}")
            return (x, y)

        result = doeff_run(WithHandler(handler, WithIntercept(Callable(interceptor), body())))
        assert result == ("val:replaced", "val:normal")
        assert logged == ["start", "got val:replaced and val:normal"]

    def test_nested_intercepts(self):
        """Multiple interceptors compose — inner runs first."""
        class Ask(EffectBase):
            def __init__(self, key):
                super().__init__()
                self.key = key

        def add_prefix(effect):
            if isinstance(effect, Ask):
                return Ask(f"pre:{effect.key}")
            return effect

        def add_suffix(effect):
            if isinstance(effect, Ask):
                return Ask(f"{effect.key}:suf")
            return effect

        @do
        def handler(effect, k):
            result = yield Resume(k, effect.key)
            return result

        @do
        def body():
            return (yield Ask("x"))

        # Inner (add_prefix) runs first, then outer (add_suffix)
        result = doeff_run(
            WithHandler(handler,
                WithIntercept(Callable(add_suffix),
                    WithIntercept(Callable(add_prefix), body())))
        )
        assert result == "pre:x:suf"
