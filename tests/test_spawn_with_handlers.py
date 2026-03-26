"""Test: Spawn/Wait must propagate handler chain to spawned tasks.

Spawned tasks need access to the full handler chain so that effects
(Ask, Try, cache, etc.) work inside spawned programs.

Bug: `RuntimeError: VM error (non-exception value in Raise):
"generator yielded non-DoExpr: <class 'doeff.program.WithHandler'>."`
when a spawned task performs effects that require handlers.
"""
from doeff import do, run, WithHandler, Resume, Pass
from doeff_core_effects import Ask, Try, slog, Tell
from doeff_core_effects.handlers import (
    reader, state, writer, try_handler, slog_handler, await_handler,
)
from doeff_core_effects.scheduler import Spawn, Wait, Gather, scheduled
from doeff_vm import EffectBase, Ok


# --- Custom effect + handler (mimics LLMStructuredQuery + openai_handler) ---

class CustomQuery(EffectBase):
    def __init__(self, prompt):
        self.prompt = prompt


@do
def custom_query_handler(effect, k):
    if isinstance(effect, CustomQuery):
        # Handler body uses Try internally (like openai production handler)
        @do
        def process():
            yield Tell(f"Processing: {effect.prompt}")
            return f"result({effect.prompt})"

        safe = yield Try(process())
        if safe.is_err():
            raise safe.error
        return (yield Resume(k, safe.value))
    yield Pass(effect, k)


# --- Tests ---

def _run_with_handlers(program):
    """Run program with standard handler chain + custom_query_handler + scheduler."""
    wrapped = program
    handlers = [
        reader(env={"model": "test"}),
        state(),
        writer(),
        try_handler,
        slog_handler(),
        custom_query_handler,
    ]
    for h in reversed(handlers):
        wrapped = WithHandler(h, wrapped)
    return run(scheduled(wrapped))


def test_spawn_wait_simple():
    """Basic Spawn/Wait with no effects inside task."""
    @do
    def prog():
        @do
        def task_body():
            return 42

        t = yield Spawn(task_body())
        result = yield Wait(t)
        return result

    assert _run_with_handlers(prog()) == 42


def test_spawn_wait_with_ask():
    """Spawned task uses Ask — needs reader handler."""
    @do
    def prog():
        @do
        def task_body():
            model = yield Ask("model")
            return f"model={model}"

        t = yield Spawn(task_body())
        result = yield Wait(t)
        return result

    assert _run_with_handlers(prog()) == "model=test"


def test_spawn_wait_with_try():
    """Spawned task uses Try — needs try_handler."""
    @do
    def prog():
        @do
        def task_body():
            @do
            def inner():
                return 42
            safe = yield Try(inner())
            return safe.value

        t = yield Spawn(task_body())
        result = yield Wait(t)
        return result

    assert _run_with_handlers(prog()) == 42


def test_spawn_wait_with_custom_effect():
    """Spawned task performs CustomQuery — needs custom_query_handler."""
    @do
    def prog():
        @do
        def task_body():
            result = yield CustomQuery("hello")
            return result

        t = yield Spawn(task_body())
        result = yield Wait(t)
        return result

    assert _run_with_handlers(prog()) == "result(hello)"


def test_spawn_wait_with_try_wrapping_custom_effect():
    """Spawned task: Try(CustomQuery) — the TRD-063 pattern."""
    @do
    def prog():
        t = yield Spawn(Try(CustomQuery("hello")))
        result = yield Wait(t)
        return result

    result = _run_with_handlers(prog())
    assert isinstance(result, Ok)
    assert result.value == "result(hello)"


def test_spawn_multiple_wait_sequential():
    """Spawn N tasks, Wait for each sequentially."""
    @do
    def prog():
        tasks = []
        for i in range(5):
            @do
            def task_body(n=i):
                return n * 10
            t = yield Spawn(task_body())
            tasks.append(t)

        results = []
        for t in tasks:
            r = yield Wait(t)
            results.append(r)
        return results

    assert _run_with_handlers(prog()) == [0, 10, 20, 30, 40]


def test_spawn_multiple_with_ask():
    """Spawn N tasks that each use Ask, Wait sequentially."""
    @do
    def prog():
        tasks = []
        for i in range(5):
            @do
            def task_body(n=i):
                model = yield Ask("model")
                return f"{model}:{n}"
            t = yield Spawn(task_body())
            tasks.append(t)

        results = []
        for t in tasks:
            r = yield Wait(t)
            results.append(r)
        return results

    assert _run_with_handlers(prog()) == [
        "test:0", "test:1", "test:2", "test:3", "test:4"
    ]


def test_spawn_multiple_with_custom_effect():
    """Spawn N tasks that each perform CustomQuery, Wait sequentially.
    This is the exact pattern that fails in TRD-063 pipeline."""
    @do
    def prog():
        prompts = ["a", "b", "c"]
        tasks = []
        for p in prompts:
            t = yield Spawn(Try(CustomQuery(p)))
            tasks.append((p, t))

        results = []
        for p, t in tasks:
            safe = yield Wait(t)
            if isinstance(safe, Ok):
                results.append((p, safe.value))
        return results

    result = _run_with_handlers(prog())
    assert result == [("a", "result(a)"), ("b", "result(b)"), ("c", "result(c)")]


def test_gather_small():
    """Gather with a small number of tasks."""
    @do
    def prog():
        t1 = yield Spawn(Try(CustomQuery("x")))
        t2 = yield Spawn(Try(CustomQuery("y")))
        results = yield Gather(t1, t2)
        return results

    result = _run_with_handlers(prog())
    assert len(result) == 2
    assert all(isinstance(r, Ok) for r in result)
    assert result[0].value == "result(x)"
    assert result[1].value == "result(y)"
