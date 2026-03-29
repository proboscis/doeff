from __future__ import annotations

import doeff_vm

import pytest

pytestmark = pytest.mark.skip(reason="uses removed API: WriteVar, AllocVar, ReadVar")

from doeff import (
    AllocVar,
    Effect,
    Gather,
    Local,
    Pass,
    ReadVar,
    Spawn,
    Tell,
    WithHandler,
    WithIntercept,
    # REMOVED: WriteVar,
    # REMOVED: WriteVarNonlocal,
    default_handlers,
    do,
    run,
)
from doeff import EffectGenerator


@do
def _count_visible_handlers():
    handlers = yield doeff_vm.GetHandlers()
    return len(handlers)


@do
def _shadow_write(var):
    yield WriteVar(var, 20)
    return (yield ReadVar(var))


@do
def _nonlocal_write(var):
    yield WriteVarNonlocal(var, 20)
    return (yield ReadVar(var))


@do
def _spawn_reads_var(var):
    return (yield ReadVar(var))


@do
def _spawn_shadows_var(var):
    yield WriteVar(var, 99)
    return (yield ReadVar(var))


def test_alloc_var_and_read_var_round_trip() -> None:
    @do
    def program():
        var = yield AllocVar(42)
        return (yield ReadVar(var))

    result = run(program(), handlers=default_handlers())
    assert result.value == 42


def test_write_var_shadows_inside_local_child_scope() -> None:
    @do
    def program():
        var = yield AllocVar(10)
        inner = yield Local({}, _shadow_write(var))
        outer = yield ReadVar(var)
        return inner, outer

    result = run(program(), handlers=default_handlers())
    assert result.value == (20, 10)


def test_write_var_nonlocal_updates_parent_scope() -> None:
    @do
    def program():
        var = yield AllocVar(10)
        inner = yield Local({}, _nonlocal_write(var))
        outer = yield ReadVar(var)
        return inner, outer

    result = run(program(), handlers=default_handlers())
    assert result.value == (20, 20)


def test_spawn_inherits_scope_variables_from_yield_site() -> None:
    @do
    def program():
        var = yield AllocVar(42)
        task = yield Spawn(_spawn_reads_var(var))
        [value] = yield Gather(task)
        return value

    result = run(program(), handlers=default_handlers())
    assert result.value == 42


def test_spawn_shadow_write_does_not_mutate_parent_scope() -> None:
    @do
    def program():
        var = yield AllocVar(10)
        task = yield Spawn(_spawn_shadows_var(var))
        [inner] = yield Gather(task)
        outer = yield ReadVar(var)
        return inner, outer

    result = run(program(), handlers=default_handlers())
    assert result.value == (99, 10)


def test_spawn_does_not_duplicate_handler_chain() -> None:
    @do
    def program():
        direct = yield _count_visible_handlers()
        task = yield Spawn(_count_visible_handlers())
        [spawned] = yield Gather(task)
        return direct, spawned

    result = run(program(), handlers=default_handlers())
    assert result.value[1] == result.value[0]


def test_spawn_many_tasks_do_not_accumulate_handler_chain() -> None:
    @do
    def _cache_handler(effect: Effect, k) -> EffectGenerator:
        yield Pass()

    @do
    def _interceptor(expr):
        return expr

    @do
    def _worker(i: int):
        before = yield _count_visible_handlers()
        yield Tell(f"worker {i}")
        after = yield _count_visible_handlers()
        return before, after

    @do
    def program():
        tasks = []
        for i in range(40):
            tasks.append((yield Spawn(_worker(i))))
        return list((yield Gather(*tasks)))

    result = run(
        WithIntercept(_interceptor, WithHandler(_cache_handler, program())),
        handlers=default_handlers(),
    )

    starts = {before for before, _ in result.value}
    ends = {after for _, after in result.value}
    assert len(starts) == 1
    assert len(ends) == 1
    assert starts == ends
