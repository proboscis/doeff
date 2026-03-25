"""Test: WithHandler must be deep (auto-reinstall after Resume).

A handler that uses Resume(k, value) should be re-invoked when the
resumed continuation performs another effect of the same type.

This is the standard "deep handler" semantics from algebraic effects
literature (OCaml 5, Koka, Effekt all use deep handlers by default).
"""
from doeff import do, run, WithHandler, Resume, Transfer, Pass
from doeff_core_effects import Ask
from doeff_core_effects.handlers import reader
from doeff_vm import EffectBase


def test_reader_handles_multiple_asks():
    """reader handler must handle ALL Ask effects, not just the first one."""

    @do
    def prog():
        a = yield Ask("key_a")
        b = yield Ask("key_b")
        c = yield Ask("key_c")
        return f"{a}-{b}-{c}"

    env = {"key_a": "hello", "key_b": "world", "key_c": "!"}
    result = run(WithHandler(reader(env=env), prog()))
    assert result == "hello-world-!"


def test_custom_handler_handles_multiple_effects():
    """Custom handler with Resume must be re-invoked for subsequent effects."""

    class MyEffect(EffectBase):
        def __init__(self, x):
            self.x = x

    call_count = 0

    @do
    def handler(effect, k):
        nonlocal call_count
        if isinstance(effect, MyEffect):
            call_count += 1
            result = yield Resume(k, effect.x * 10)
            return result
        yield Pass(effect, k)

    @do
    def prog():
        a = yield MyEffect(1)
        b = yield MyEffect(2)
        c = yield MyEffect(3)
        return a + b + c

    result = run(WithHandler(handler, prog()))
    assert result == 60  # 10 + 20 + 30
    assert call_count == 3  # handler called 3 times


def test_nested_do_multiple_asks():
    """Ask effects from nested @do functions must all be handled."""

    @do
    def get_greeting():
        name = yield Ask("name")
        greeting = yield Ask("greeting")
        return f"{greeting}, {name}"

    @do
    def get_farewell():
        name = yield Ask("name")
        farewell = yield Ask("farewell")
        return f"{farewell}, {name}"

    @do
    def prog():
        g = yield get_greeting()
        f = yield get_farewell()
        return f"{g} / {f}"

    env = {"name": "Alice", "greeting": "Hello", "farewell": "Bye"}
    result = run(WithHandler(reader(env=env), prog()))
    assert result == "Hello, Alice / Bye, Alice"


def test_transfer_does_not_reinstall():
    """Transfer (tail-resume) should NOT reinstall the handler — it's a tail call."""

    class Counter:
        def __init__(self):
            self.n = 0

    counter = Counter()

    @do
    def handler(effect, k):
        if isinstance(effect, Ask):
            counter.n += 1
            # Transfer = tail position, handler NOT reinstalled
            yield Transfer(k, f"val_{counter.n}")
        else:
            yield Pass(effect, k)

    @do
    def prog():
        # Only the first Ask should be handled; after Transfer the handler is gone
        a = yield Ask("x")
        return a

    result = run(WithHandler(handler, prog()))
    assert result == "val_1"
    assert counter.n == 1
