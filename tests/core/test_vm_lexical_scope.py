from __future__ import annotations

from dataclasses import dataclass

import pytest

import doeff_vm
from doeff import (
    Ask,
    Effect,
    EffectBase,
    Gather,
    Get,
    Local,
    Put,
    Spawn,
    Tell,
    WithHandler,
    default_handlers,
    do,
    run,
)


def _run(program, *, env=None, store=None):
    return run(program, handlers=default_handlers(), env=env, store=store)


def _assert_ok(result):
    assert result.is_ok(), getattr(result, "error", None)
    return result.value


def _scope_repr(scope) -> str:
    return repr(scope)


def test_t1_push_pop_scope() -> None:
    @do
    def program():
        k0 = yield doeff_vm.GetContinuation()
        root = yield doeff_vm.GetScopeOf(k0)
        yield doeff_vm.PushScope()
        k1 = yield doeff_vm.GetContinuation()
        inner = yield doeff_vm.GetScopeOf(k1)
        yield doeff_vm.PopScope()
        k2 = yield doeff_vm.GetContinuation()
        restored = yield doeff_vm.GetScopeOf(k2)
        return (_scope_repr(root), _scope_repr(inner), _scope_repr(restored))

    root, inner, restored = _assert_ok(_run(program()))
    assert root != inner
    assert root == restored


def test_t2_nested_scopes() -> None:
    @do
    def program():
        scopes: list[str] = []
        k0 = yield doeff_vm.GetContinuation()
        scopes.append(_scope_repr((yield doeff_vm.GetScopeOf(k0))))
        yield doeff_vm.PushScope()
        k1 = yield doeff_vm.GetContinuation()
        scopes.append(_scope_repr((yield doeff_vm.GetScopeOf(k1))))
        yield doeff_vm.PushScope()
        k2 = yield doeff_vm.GetContinuation()
        scopes.append(_scope_repr((yield doeff_vm.GetScopeOf(k2))))
        yield doeff_vm.PushScope()
        k3 = yield doeff_vm.GetContinuation()
        scopes.append(_scope_repr((yield doeff_vm.GetScopeOf(k3))))
        yield doeff_vm.PopScope()
        yield doeff_vm.PopScope()
        yield doeff_vm.PopScope()
        k4 = yield doeff_vm.GetContinuation()
        scopes.append(_scope_repr((yield doeff_vm.GetScopeOf(k4))))
        return scopes

    scopes = _assert_ok(_run(program()))
    assert len(set(scopes[:4])) == 4
    assert scopes[0] == scopes[4]


def test_t3_alloc_and_read_var() -> None:
    @do
    def program():
        var = yield doeff_vm.AllocVar(42)
        return (yield doeff_vm.ReadVar(var))

    assert _assert_ok(_run(program())) == 42


def test_t4_shadow_write() -> None:
    @do
    def program():
        var = yield doeff_vm.AllocVar(10)
        yield doeff_vm.PushScope()
        yield doeff_vm.WriteVar(var, 20)
        inner = yield doeff_vm.ReadVar(var)
        yield doeff_vm.PopScope()
        outer = yield doeff_vm.ReadVar(var)
        return (inner, outer)

    assert _assert_ok(_run(program())) == (20, 10)


def test_t5_nonlocal_write() -> None:
    @do
    def program():
        var = yield doeff_vm.AllocVar(10)
        yield doeff_vm.PushScope()
        yield doeff_vm.WriteVarNonlocal(var, 20)
        yield doeff_vm.PopScope()
        return (yield doeff_vm.ReadVar(var))

    assert _assert_ok(_run(program())) == 20


def test_t6_read_walks_chain() -> None:
    @do
    def program():
        var = yield doeff_vm.AllocVar(10)
        yield doeff_vm.PushScope()
        value = yield doeff_vm.ReadVar(var)
        yield doeff_vm.PopScope()
        return value

    assert _assert_ok(_run(program())) == 10


def test_t7_var_dropped_on_pop() -> None:
    @do
    def program():
        yield doeff_vm.PushScope()
        var = yield doeff_vm.AllocVar(42)
        yield doeff_vm.PopScope()
        return (yield doeff_vm.ReadVar(var))

    result = _run(program())
    assert not result.is_ok()
    assert "ReadVar" in str(result.error)


def test_t8_multiple_vars() -> None:
    @do
    def program():
        x = yield doeff_vm.AllocVar(1)
        y = yield doeff_vm.AllocVar(2)
        return ((yield doeff_vm.ReadVar(x)), (yield doeff_vm.ReadVar(y)))

    assert _assert_ok(_run(program())) == (1, 2)


def test_t9_three_level_shadow() -> None:
    @do
    def program():
        var = yield doeff_vm.AllocVar(10)
        yield doeff_vm.PushScope()
        yield doeff_vm.WriteVar(var, 20)
        yield doeff_vm.PushScope()
        yield doeff_vm.WriteVar(var, 30)
        inner = yield doeff_vm.ReadVar(var)
        yield doeff_vm.PopScope()
        middle = yield doeff_vm.ReadVar(var)
        yield doeff_vm.PopScope()
        outer = yield doeff_vm.ReadVar(var)
        return (inner, middle, outer)

    assert _assert_ok(_run(program())) == (30, 20, 10)


def test_t10_handler_var_in_handler_scope() -> None:
    holder: dict[str, object] = {}

    @dataclass(frozen=True)
    class Ping(EffectBase):
        pass

    @do
    def handler(effect: Effect, k):
        if not isinstance(effect, Ping):
            yield doeff_vm.Pass()
            return
        if "var" not in holder:
            holder["var"] = yield doeff_vm.AllocVar(41)
        value = yield doeff_vm.ReadVar(holder["var"])
        return (yield doeff_vm.Resume(k, value))

    @do
    def body():
        return (yield Ping())

    assert _assert_ok(_run(WithHandler(handler, body()))) == 41


def test_t11_nested_handler_var_visibility() -> None:
    holder: dict[str, object] = {}

    @dataclass(frozen=True)
    class InitOuter(EffectBase):
        pass

    @dataclass(frozen=True)
    class ReadOuter(EffectBase):
        pass

    @do
    def outer_handler(effect: Effect, k):
        if isinstance(effect, InitOuter):
            holder["var"] = yield doeff_vm.AllocVar(10)
            return (yield doeff_vm.Resume(k, None))
        yield doeff_vm.Pass()

    @do
    def inner_handler(effect: Effect, k):
        if not isinstance(effect, ReadOuter):
            yield doeff_vm.Pass()
            return
        value = yield doeff_vm.ReadVar(holder["var"])
        return (yield doeff_vm.Resume(k, value))

    @do
    def body():
        yield InitOuter()
        return (yield ReadOuter())

    result = _run(WithHandler(outer_handler, WithHandler(inner_handler, body())))
    assert _assert_ok(result) == 10


def test_t12_handler_var_not_visible_outside() -> None:
    holder: dict[str, object] = {}

    @dataclass(frozen=True)
    class Ping(EffectBase):
        pass

    @do
    def handler(effect: Effect, k):
        if not isinstance(effect, Ping):
            yield doeff_vm.Pass()
            return
        if "var" not in holder:
            holder["var"] = yield doeff_vm.AllocVar(99)
        value = yield doeff_vm.ReadVar(holder["var"])
        return (yield doeff_vm.Resume(k, value))

    @do
    def program():
        _ = yield WithHandler(handler, Ping())
        return (yield doeff_vm.ReadVar(holder["var"]))

    result = _run(program())
    assert not result.is_ok()
    assert "ReadVar" in str(result.error)


def test_t13_spawn_inherits_yield_site_handlers() -> None:
    @dataclass(frozen=True)
    class PingA(EffectBase):
        pass

    @dataclass(frozen=True)
    class PingB(EffectBase):
        pass

    @do
    def handler_a(effect: Effect, k):
        if not isinstance(effect, PingA):
            yield doeff_vm.Pass()
            return
        return (yield doeff_vm.Resume(k, "a"))

    @do
    def handler_b(effect: Effect, k):
        if not isinstance(effect, PingB):
            yield doeff_vm.Pass()
            return
        return (yield doeff_vm.Resume(k, "b"))

    @do
    def child():
        return ((yield PingA()), (yield PingB()))

    @do
    def program():
        task = yield Spawn(child(), daemon=False)
        return list((yield Gather(task)))[0]

    wrapped = WithHandler(handler_a, WithHandler(handler_b, program()))
    assert _assert_ok(_run(wrapped)) == ("a", "b")


def test_t14_spawn_inherits_yield_site_vars() -> None:
    @do
    def child(var):
        return (yield doeff_vm.ReadVar(var))

    @do
    def program():
        var = yield doeff_vm.AllocVar(42)
        task = yield Spawn(child(var), daemon=False)
        return list((yield Gather(task)))[0]

    assert _assert_ok(_run(program())) == 42


def test_t15_spawn_var_shadow_isolation() -> None:
    @do
    def child(var):
        yield doeff_vm.PushScope()
        yield doeff_vm.WriteVar(var, 99)
        return (yield doeff_vm.ReadVar(var))

    @do
    def program():
        var = yield doeff_vm.AllocVar(10)
        task = yield Spawn(child(var), daemon=False)
        child_value = list((yield Gather(task)))[0]
        parent_value = yield doeff_vm.ReadVar(var)
        return (child_value, parent_value)

    assert _assert_ok(_run(program())) == (99, 10)


def test_t16_spawn_no_handler_duplication() -> None:
    @dataclass(frozen=True)
    class Inspect(EffectBase):
        pass

    @do
    def inspector(effect: Effect, k):
        if not isinstance(effect, Inspect):
            yield doeff_vm.Pass()
            return
        handlers = yield doeff_vm.GetHandlers()
        return (yield doeff_vm.Resume(k, len(handlers)))

    @do
    def child():
        return (yield Inspect())

    @do
    def program():
        direct = yield child()
        task = yield Spawn(child(), daemon=False)
        spawned = list((yield Gather(task)))[0]
        return (direct, spawned)

    wrapped = WithHandler(inspector, program())
    direct, spawned = _assert_ok(_run(wrapped))
    assert direct == spawned


def test_t17_multiple_spawn_share_scope() -> None:
    @do
    def child(var):
        return (yield doeff_vm.ReadVar(var))

    @do
    def program():
        var = yield doeff_vm.AllocVar(42)
        task1 = yield Spawn(child(var), daemon=False)
        task2 = yield Spawn(child(var), daemon=False)
        return tuple((yield Gather(task1, task2)))

    assert _assert_ok(_run(program())) == (42, 42)


def test_t18_spawn_500_no_quadratic_memory() -> None:
    @do
    def child(var):
        return (yield doeff_vm.ReadVar(var))

    @do
    def program():
        var = yield doeff_vm.AllocVar(7)
        tasks = []
        for _ in range(500):
            tasks.append((yield Spawn(child(var), daemon=False)))
        return list((yield Gather(*tasks)))

    values = _assert_ok(_run(program()))
    assert len(values) == 500
    assert all(value == 7 for value in values)


def test_t19_local_ask_basic() -> None:
    @do
    def program():
        return (yield Ask("key"))

    assert _assert_ok(_run(Local({"key": "value"}, program()))) == "value"


def test_t20_local_shadow() -> None:
    @do
    def program():
        return (yield Ask("x"))

    wrapped = Local({"x": 10}, Local({"x": 20}, program()))
    assert _assert_ok(_run(wrapped)) == 20


def test_t21_local_scope_restore() -> None:
    @do
    def noop():
        return None

    @do
    def program():
        yield Local({"x": 20}, noop())
        return (yield Ask("x"))

    assert _assert_ok(_run(Local({"x": 10}, program()))) == 10


def test_t22_ask_walks_to_outer() -> None:
    @do
    def program():
        return (yield Ask("x"))

    wrapped = Local({"x": 10}, Local({"y": 20}, program()))
    assert _assert_ok(_run(wrapped)) == 10


def test_t23_state_as_scoped_var() -> None:
    @do
    def program():
        yield Put("value", 10)
        yield doeff_vm.PushScope()
        yield Put("value", 20)
        yield doeff_vm.PopScope()
        return (yield Get("value"))

    assert _assert_ok(_run(program(), store={"value": 0})) == 20


def test_t24_writer_as_scoped_var() -> None:
    @do
    def program():
        yield Tell("a")
        yield doeff_vm.PushScope()
        yield Tell("b")
        yield doeff_vm.PopScope()
        yield Tell("c")
        return "done"

    result = _run(program())
    assert _assert_ok(result) == "done"
    assert list(result.log) == ["a", "b", "c"]
