from __future__ import annotations

import doeff_vm

from doeff import (
    Ask,
    Effect,
    EffectBase,
    Gather,
    Get,
    Local,
    MissingEnvKeyError,
    Pass,
    Program,
    Put,
    Resume,
    Spawn,
    Try,
    WithHandler,
    do,
)
from tests._run_helpers import run_with_defaults


def _rust_ok_err_classes() -> tuple[type, type]:
    rust_ok = doeff_vm.Ok
    rust_err = doeff_vm.Err
    assert rust_ok is not None
    assert rust_err is not None
    return rust_ok, rust_err


def test_rust_ok_err_pyclass_constructors() -> None:
    rust_ok, rust_err = _rust_ok_err_classes()
    ok = rust_ok(1)
    err = rust_err(ValueError("x"))

    assert ok.is_ok() is True
    assert ok.is_err() is False
    assert bool(ok) is True
    assert ok.value == 1

    assert err.is_ok() is False
    assert err.is_err() is True
    assert bool(err) is False
    assert isinstance(err.error, ValueError)
    assert str(err.error) == "x"
    assert err.captured_traceback is None


def test_try_except_catches_yielded_program_error() -> None:
    @do
    def boom():
        raise ValueError("x")

    @do
    def program():
        try:
            _ = yield boom()
            return ("unexpected",)
        except Exception as exc:
            return ("caught", type(exc).__name__, str(exc))

    result = run_with_defaults(program())
    assert result.is_ok()
    assert result.value == ("caught", "ValueError", "x")



def test_try_wraps_handler_raised_error_as_result_value() -> None:
    class HandlerBoom(EffectBase):
        pass

    @do
    def handler(effect: Effect, k: object):
        if not isinstance(effect, HandlerBoom):
            yield Pass(effect, k)
            return
        raise ValueError("boom-from-handler")

    @do
    def inner():
        return (yield HandlerBoom())

    @do
    def program():
        return (yield Try(WithHandler(handler, inner())))

    result = run_with_defaults(program())
    assert result.is_ok()

    value = result.value
    assert value.is_err() is True
    assert isinstance(value.error, ValueError)
    assert str(value.error) == "boom-from-handler"


def test_safe_wraps_error_as_result_value() -> None:
    @do
    def boom():
        raise ValueError("x")

    @do
    def program():
        return (yield Try(boom()))

    result = run_with_defaults(program())
    assert result.is_ok()

    value = result.value
    assert type(value).__name__ == "Err"
    assert value.is_err() is True
    assert isinstance(value.error, ValueError)
    assert str(value.error) == "x"


class YieldSiteProbe(EffectBase):
    pass


@do
def _yield_site_helper_ok():
    return "ok"


@do
def _yield_site_helper_boom():
    raise ValueError("helper-boom")


def _yield_site_meta(fn):
    code = fn.__code__
    return {
        "function_name": code.co_name,
        "source_file": code.co_filename,
        "source_line": code.co_firstlineno,
    }


def _yield_site_expand_boom_factory():
    raise ValueError("expand-boom")


def _yield_site_apply_boom_callback():
    raise ValueError("apply-boom")


@do
def _yield_site_raise_after_helper_handler(effect: Effect, k: object):
    if not isinstance(effect, YieldSiteProbe):
        yield Pass(effect, k)
        return
    _ = yield _yield_site_helper_ok()
    raise ValueError("after-helper-boom")


@do
def _yield_site_helper_boom_handler(effect: Effect, k: object):
    if not isinstance(effect, YieldSiteProbe):
        yield Pass(effect, k)
        return
    _ = yield _yield_site_helper_boom()
    return (yield Resume(k, "unreachable"))


@do
def _yield_site_expand_boom_handler(effect: Effect, k: object):
    if not isinstance(effect, YieldSiteProbe):
        yield Pass(effect, k)
        return
    _ = yield doeff_vm.Expand(
        doeff_vm.Pure(_yield_site_expand_boom_factory),
        [],
        {},
        _yield_site_meta(_yield_site_expand_boom_factory),
    )
    return (yield Resume(k, "unreachable"))


@do
def _yield_site_apply_boom_handler(effect: Effect, k: object):
    if not isinstance(effect, YieldSiteProbe):
        yield Pass(effect, k)
        return
    _ = yield doeff_vm.Apply(
        doeff_vm.Pure(_yield_site_apply_boom_callback),
        [],
        {},
        _yield_site_meta(_yield_site_apply_boom_callback),
    )
    return (yield Resume(k, "unreachable"))


@do
def _yield_site_try_direct_effect():
    try:
        _ = yield YieldSiteProbe()
        return ("unexpected",)
    except Exception as exc:
        return ("caught", type(exc).__name__, str(exc))


@do
def _yield_site_inner_effect():
    return (yield YieldSiteProbe())







def test_state_handler_callback_error_is_catchable_at_yield_site() -> None:
    @do
    def catch_program():
        try:
            old = yield Get("count")
            yield Put("count", old / 0)
            return ("unexpected",)
        except Exception as exc:
            return ("caught", type(exc).__name__, str(exc))

    catch_result = run_with_defaults(catch_program(), store={"count": 1})
    assert catch_result.is_ok()
    assert catch_result.value[0] == "caught"



def test_lazy_ask_evaluates_program_env_once_per_run() -> None:
    calls = {"service": 0}

    @do
    def service_program():
        calls["service"] += 1
        if False:
            yield
        return 42

    @do
    def program():
        first = yield Ask("service")
        second = yield Ask("service")
        return (first, second)

    result = run_with_defaults(program(), env={"service": service_program()})
    assert result.is_ok()
    assert result.value == (42, 42)
    assert calls["service"] == 1



def test_ask_existing_and_none_values_succeed() -> None:
    @do
    def program():
        value = yield Ask("key")
        nullable = yield Ask("nullable_key")
        return (value, nullable)

    result = run_with_defaults(program(), env={"key": "value", "nullable_key": None})
    assert result.is_ok()
    assert result.value == ("value", None)


def test_local_adds_new_key_and_preserves_unrelated_values() -> None:
    @do
    def inner_program():
        new_key = yield Ask("new_key")
        other = yield Ask("other_key")
        return (new_key, other)

    @do
    def program():
        return (yield Local({"new_key": "new_value"}, inner_program()))

    result = run_with_defaults(program(), env={"other_key": "other"})
    assert result.is_ok()
    assert result.value == ("new_value", "other")



def test_nested_local_with_different_keys() -> None:
    @do
    def innermost():
        key1 = yield Ask("key1")
        key2 = yield Ask("key2")
        return (key1, key2)

    @do
    def middle():
        return (yield Local({"key2": "inner2"}, innermost()))

    @do
    def program():
        return (yield Local({"key1": "outer1"}, middle()))

    result = run_with_defaults(program(), env={"key1": "orig1", "key2": "orig2"})
    assert result.is_ok()
    assert result.value == ("outer1", "inner2")


def test_gather_children_inherit_parent_env() -> None:
    @do
    def child():
        return (yield Ask("shared_key"))

    @do
    def program():
        t1 = yield Spawn(child())
        t2 = yield Spawn(child())
        t3 = yield Spawn(child())
        return (yield Gather(t1, t2, t3))

    result = run_with_defaults(program(), env={"shared_key": "shared_value"})
    assert result.is_ok()
    assert result.value == ["shared_value", "shared_value", "shared_value"]


def test_child_local_override_is_isolated_from_siblings_and_parent() -> None:
    @do
    def child_with_local():
        @do
        def inner():
            return (yield Ask("key"))

        result = yield Local({"key": "child_override"}, inner())
        return f"local_child:{result}"

    @do
    def child_normal():
        value = yield Ask("key")
        return f"normal_child:{value}"

    @do
    def program():
        before = yield Ask("key")
        t1 = yield Spawn(child_with_local())
        t2 = yield Spawn(child_normal())
        t3 = yield Spawn(child_normal())
        results = yield Gather(t1, t2, t3)
        after = yield Ask("key")
        return (before, results, after)

    result = run_with_defaults(program(), env={"key": "parent_value"})
    assert result.is_ok()
    assert result.value == (
        "parent_value",
        [
            "local_child:child_override",
            "normal_child:parent_value",
            "normal_child:parent_value",
        ],
        "parent_value",
    )


def test_lazy_ask_program_with_effects_updates_state() -> None:
    @do
    def program_with_effects():
        _ = yield Put("counter", 100)
        counter = yield Get("counter")
        return counter * 2

    @do
    def program():
        result = yield Ask("compute")
        final_counter = yield Get("counter")
        return (result, final_counter)

    result = run_with_defaults(program(), env={"compute": program_with_effects()})
    assert result.is_ok()
    assert result.value == (200, 100)


def test_lazy_ask_different_keys_cached_independently() -> None:
    calls = {"a": 0, "b": 0}

    @do
    def program_a():
        calls["a"] += 1
        if False:
            yield
        return "result_a"

    @do
    def program_b():
        calls["b"] += 1
        if False:
            yield
        return "result_b"

    @do
    def program():
        a1 = yield Ask("key_a")
        b1 = yield Ask("key_b")
        a2 = yield Ask("key_a")
        b2 = yield Ask("key_b")
        return (a1, b1, a2, b2)

    result = run_with_defaults(program(), env={"key_a": program_a(), "key_b": program_b()},
    )
    assert result.is_ok()
    assert result.value == ("result_a", "result_b", "result_a", "result_b")
    assert calls == {"a": 1, "b": 1}


def test_lazy_ask_failed_evaluation_not_cached_after_replacement() -> None:
    attempts = {"service": 0}

    @do
    def sometimes_fails():
        attempts["service"] += 1
        if attempts["service"] == 1:
            raise ValueError("First attempt fails")
        if False:
            yield
        return "success"

    first_program = sometimes_fails()
    second_program = sometimes_fails()

    @do
    def program():
        first = yield Try(Ask("service"))
        if first.is_err():

            @do
            def inner():
                return (yield Ask("service"))

            return (yield Local({"service": second_program}, inner()))
        return "unexpected"

    result = run_with_defaults(program(), env={"service": first_program})
    assert result.is_ok()
    assert result.value == "success"
    assert attempts["service"] == 2


def test_lazy_ask_nested_dependency_resolves() -> None:
    @do
    def inner_service():
        if False:
            yield
        return 10

    @do
    def outer_service():
        inner = yield Ask("inner")
        return inner * 2

    @do
    def program():
        return (yield Ask("outer"))

    result = run_with_defaults(program(), env={"inner": inner_service(), "outer": outer_service()},
    )
    assert result.is_ok()
    assert result.value == 20



def test_lazy_ask_program_returning_program_can_be_resolved() -> None:
    @do
    def inner():
        if False:
            yield
        return 42

    @do
    def outer():
        if False:
            yield
        return inner()

    @do
    def program():
        result = yield Ask("service")
        if isinstance(result, Program):
            return (yield result)
        return result

    result = run_with_defaults(program(), env={"service": outer()})
    assert result.is_ok()
    assert result.value == 42


def test_hashable_non_string_ask_keys_work() -> None:
    @do
    def make_prog(val):
        if False:
            yield
        return val

    env = {
        "string_key": make_prog("string"),
        42: make_prog("int"),
        ("tuple", "key"): make_prog("tuple"),
    }

    @do
    def program():
        string_value = yield Ask("string_key")
        int_value = yield Ask(42)
        tuple_value = yield Ask(("tuple", "key"))
        return (string_value, int_value, tuple_value)

    result = run_with_defaults(program(), env=env)
    assert result.is_ok()
    assert result.value == ("string", "int", "tuple")




def test_lazy_ask_spawned_tasks_share_single_evaluation() -> None:
    calls = {"service": 0}

    @do
    def service_program():
        calls["service"] += 1
        if False:
            yield
        return 42

    @do
    def child():
        return (yield Ask("service"))

    @do
    def program():
        t1 = yield Spawn(child())
        t2 = yield Spawn(child())
        return (yield Gather(t1, t2))

    result = run_with_defaults(program(), env={"service": service_program()})
    assert result.is_ok()
    assert result.value == [42, 42]
    assert calls["service"] == 1


def test_lazy_ask_concurrent_waiters_do_not_reexecute() -> None:
    calls = {"service": 0}

    @do
    def service_program():
        calls["service"] += 1
        if False:
            yield
        return 42

    @do
    def child():
        return (yield Ask("service"))

    @do
    def program():
        t1 = yield Spawn(child())
        t2 = yield Spawn(child())
        return (yield Gather(t1, t2))

    result = run_with_defaults(
        program(),
        env={"service": service_program()},
    )
    assert result.is_ok()
    assert result.value == [42, 42]
    assert calls["service"] == 1


def test_lazy_ask_program_error_propagates() -> None:
    @do
    def failing_service():
        raise ValueError("lazy boom")

    @do
    def program():
        return (yield Ask("service"))

    result = run_with_defaults(program(), env={"service": failing_service()})
    assert result.is_err()
    assert isinstance(result.error, ValueError)
    assert str(result.error) == "lazy boom"


def test_lazy_ask_safe_captures_program_error() -> None:
    @do
    def failing_service():
        raise ValueError("lazy boom")

    @do
    def program():
        return (yield Try(Ask("service")))

    result = run_with_defaults(program(), env={"service": failing_service()})
    assert result.is_ok()
    safe_result = result.value
    assert safe_result.is_err()
    assert isinstance(safe_result.error, ValueError)
    assert str(safe_result.error) == "lazy boom"


def test_ask_missing_key_raises_missing_env_key_error() -> None:
    @do
    def program():
        return (yield Ask("missing"))

    result = run_with_defaults(program(), env={})
    assert result.is_err()
    assert isinstance(result.error, MissingEnvKeyError)
    assert isinstance(result.error, KeyError)
