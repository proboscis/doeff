"""Current doeff-vm Python bridge contract tests."""

import doeff_vm
import pytest
from doeff_core_effects.handlers import listen_handler, reader, state, writer
from doeff_core_effects.scheduler import Gather, Spawn, scheduled

from doeff import Ask, Get, Listen, Put, Tell, do, run


class CustomEffect(doeff_vm.EffectBase):
    def __init__(self, value: int) -> None:
        self.value = value


class SecondEffect(doeff_vm.EffectBase):
    def __init__(self, value: int) -> None:
        self.value = value


@do
def simple_program():
    return 42


def test_import_exports_current_low_level_api() -> None:
    assert doeff_vm.PyVM is not None
    assert doeff_vm.EffectBase is not None
    assert doeff_vm.WithHandler is not None
    assert doeff_vm.TailEval is not None
    assert doeff_vm.vm_live_counts is not None

    removed_runtime_facade_symbols = {
        "run",
        "async_run",
        "state",
        "reader",
        "writer",
        "scheduler",
        "RunResult",
        "memory_stats",
    }
    assert removed_runtime_facade_symbols.isdisjoint(set(dir(doeff_vm)))


def test_pyvm_creation() -> None:
    vm = doeff_vm.PyVM()
    assert vm.arena_stats() == (0, 0, 0, 0)


def test_simple_pure_program() -> None:
    vm = doeff_vm.PyVM()
    assert vm.run(simple_program()) == 42


def test_apply_resolves_doexpr_args() -> None:
    vm = doeff_vm.PyVM()
    adder = doeff_vm.Callable(lambda left, right: left + right)
    program = doeff_vm.Apply(doeff_vm.Pure(adder), [doeff_vm.Pure(1), doeff_vm.Pure(2)])

    assert vm.run(program) == 3


def test_with_handler_rejects_non_callable_handler() -> None:
    with pytest.raises(TypeError, match="handler must be callable"):
        doeff_vm.WithHandler(42, simple_program())


def test_python_handler_basic() -> None:
    @do
    def handler(effect, k):
        if isinstance(effect, CustomEffect):
            return (yield doeff_vm.Resume(k, effect.value * 2))
        yield doeff_vm.Pass(effect, k)

    @do
    def body():
        value = yield CustomEffect(21)
        return value

    assert run(doeff_vm.WithHandler(handler, body())) == 42


def test_nested_with_handler_passes_to_outer_handler() -> None:
    @do
    def inner_handler(effect, k):
        if isinstance(effect, SecondEffect):
            return (yield doeff_vm.Resume(k, f"inner:{effect.value}"))
        yield doeff_vm.Pass(effect, k)

    @do
    def outer_handler(effect, k):
        if isinstance(effect, CustomEffect):
            return (yield doeff_vm.Resume(k, f"outer:{effect.value}"))
        yield doeff_vm.Pass(effect, k)

    @do
    def body():
        return (yield CustomEffect(5))

    program = doeff_vm.WithHandler(outer_handler, doeff_vm.WithHandler(inner_handler, body()))
    assert run(program) == "outer:5"


def test_get_handlers_reports_dispatch_continuation_chain() -> None:
    @do
    def inner_handler(effect, k):
        yield doeff_vm.Pass(effect, k)

    @do
    def outer_handler(effect, k):
        if isinstance(effect, CustomEffect):
            handlers = yield doeff_vm.GetHandlers(k)
            return (yield doeff_vm.Resume(k, [handler.__name__ for handler in handlers]))
        yield doeff_vm.Pass(effect, k)

    @do
    def body():
        return (yield CustomEffect(1))

    program = doeff_vm.WithHandler(outer_handler, doeff_vm.WithHandler(inner_handler, body()))
    assert run(program) == ["inner_handler", "outer_handler"]


def test_transfer_resumes_and_abandons_handler_generator() -> None:
    handler_continued = {"ran": False}

    @do
    def transfer_handler(effect, k):
        if isinstance(effect, CustomEffect):
            yield doeff_vm.Transfer(k, effect.value * 10)
            handler_continued["ran"] = True
        yield doeff_vm.Pass(effect, k)

    @do
    def body():
        value = yield CustomEffect(7)
        return value + 1

    assert run(doeff_vm.WithHandler(transfer_handler, body())) == 71
    assert handler_continued["ran"] is False


def test_resume_throw_can_be_caught_by_body() -> None:
    @do
    def throwing_handler(effect, k):
        if isinstance(effect, CustomEffect):
            return (yield doeff_vm.ResumeThrow(k, ValueError("boom")))
        yield doeff_vm.Pass(effect, k)

    @do
    def body():
        try:
            yield CustomEffect(1)
        except ValueError as exc:
            return str(exc)
        return "unreachable"

    assert run(doeff_vm.WithHandler(throwing_handler, body())) == "boom"


def test_unhandled_effect_raises_typed_exception() -> None:
    @do
    def body():
        return (yield CustomEffect(1))

    with pytest.raises(doeff_vm.UnhandledEffect, match="unhandled effect"):
        run(body())


def test_pyvm_rejects_raw_generator_top_level() -> None:
    def raw_generator():
        yield doeff_vm.Pure(1)

    with pytest.raises(TypeError, match="expected DoExpr or EffectBase"):
        doeff_vm.PyVM().run(raw_generator())


def test_yielded_plain_value_fails_loudly() -> None:
    @do
    def body():
        yield 42

    with pytest.raises(RuntimeError, match="expected DoExpr or EffectBase"):
        doeff_vm.PyVM().run(body())


def test_current_state_reader_writer_handlers_use_installer_api() -> None:
    @do
    def state_body():
        value = yield Get("counter")
        yield Put("counter", value + 1)
        return (yield Get("counter"))

    @do
    def reader_body():
        return (yield Ask("name"))

    @do
    def writer_body():
        yield Tell("starting")
        yield Tell("done")
        return "ok"

    @do
    def writer_listened():
        return (yield Listen(writer_body()))

    assert run(state(initial={"counter": 1})(state_body())) == 2
    assert run(reader(env={"name": "Ada"})(reader_body())) == "Ada"
    result, collected = run(writer()(listen_handler(writer_listened())))
    assert result == "ok"
    assert [e.msg for e in collected] == ["starting", "done"]


def test_scheduled_spawn_gather_runs_via_doeff_facade() -> None:
    @do
    def child(value: int):
        return value * 2

    @do
    def body():
        left = yield Spawn(child(2))
        right = yield Spawn(child(3))
        return list((yield Gather(left, right)))

    assert run(scheduled(body())) == [4, 6]


def test_vm_live_counts_return_to_baseline_after_run() -> None:
    before = doeff_vm.vm_live_counts()

    assert run(simple_program()) == 42

    assert doeff_vm.vm_live_counts() == before
