from dataclasses import dataclass
from pathlib import Path

import doeff_vm
from doeff import Gather, Pass, Resume, Spawn, WithHandler, do
from doeff.effects.base import Effect, EffectBase
from doeff.handlers import sqlite_cache_handler
from doeff.handlers.cache_handlers import memo_rewriters
from doeff.rust_vm import default_handlers
from doeff.rust_vm import run as vm_run


def test_memory_stats_exported_with_expected_keys():
    stats = doeff_vm.memory_stats()

    assert callable(doeff_vm.memory_stats)
    assert set(stats) >= {
        "live_segments",
        "live_continuations",
        "live_ir_streams",
        "rust_heap_bytes",
    }
    assert all(isinstance(stats[key], int) for key in stats)


def test_memory_stats_counts_return_to_baseline_after_run():
    before = doeff_vm.memory_stats()

    result = doeff_vm.run(doeff_vm.Pure(7))
    after = doeff_vm.memory_stats()

    assert result.is_ok()
    assert result.value == 7
    assert after["live_segments"] == before["live_segments"]
    assert after["live_continuations"] == before["live_continuations"]
    assert after["live_ir_streams"] == before["live_ir_streams"]


def test_memory_stats_counts_return_to_baseline_after_deep_handler_spawn_chain(
    tmp_path: Path,
):
    cache_path = tmp_path / "vm_memory_stats.sqlite3"

    @dataclass(frozen=True, kw_only=True)
    class SyntheticQuery(EffectBase):
        key: str

    def synthetic_query_handler():
        @do
        def _handler(effect: Effect, k):
            if not isinstance(effect, SyntheticQuery):
                yield Pass()
                return
            return (yield Resume(k, effect.key))

        return _handler

    @do
    def worker(batch_index: int, task_index: int):
        return (yield SyntheticQuery(key=f"{batch_index}:{task_index}"))

    @do
    def scenario():
        for batch_index in range(2):
            tasks = []
            for task_index in range(20):
                task = yield Spawn(
                    worker(batch_index=batch_index, task_index=task_index),
                    daemon=False,
                )
                tasks.append(task)
            values = yield Gather(*tasks)
            if len(values) != 20:
                raise AssertionError(f"expected 20 values, got {len(values)}")
        return None

    wrapped = scenario()
    for handler in reversed(
        (
            synthetic_query_handler(),
            *memo_rewriters(SyntheticQuery),
            sqlite_cache_handler(cache_path),
        )
    ):
        wrapped = WithHandler(handler, wrapped)

    before = doeff_vm.memory_stats()
    result = vm_run(wrapped, handlers=default_handlers())
    after = doeff_vm.memory_stats()

    assert result.is_ok()
    assert after["live_segments"] == before["live_segments"]
    assert after["live_continuations"] == before["live_continuations"]
    assert after["live_ir_streams"] == before["live_ir_streams"]


def test_pyvm_run_releases_internal_vm_capacities_after_deep_handler_spawn_chain(
    tmp_path: Path,
):
    cache_path = tmp_path / "vm_memory_stats_pyvm.sqlite3"

    @dataclass(frozen=True, kw_only=True)
    class SyntheticQuery(EffectBase):
        key: str

    def synthetic_query_handler():
        @do
        def _handler(effect: Effect, k):
            if not isinstance(effect, SyntheticQuery):
                yield Pass()
                return
            return (yield Resume(k, effect.key))

        return _handler

    @do
    def worker(batch_index: int, task_index: int):
        return (yield SyntheticQuery(key=f"{batch_index}:{task_index}"))

    @do
    def scenario():
        for batch_index in range(2):
            tasks = []
            for task_index in range(20):
                task = yield Spawn(
                    worker(batch_index=batch_index, task_index=task_index),
                    daemon=False,
                )
                tasks.append(task)
            values = yield Gather(*tasks)
            if len(values) != 20:
                raise AssertionError(f"expected 20 values, got {len(values)}")
        return None

    program = scenario()
    for handler in reversed(
        (
            synthetic_query_handler(),
            *memo_rewriters(SyntheticQuery),
            sqlite_cache_handler(cache_path),
            *default_handlers(),
        )
    ):
        program = WithHandler(handler, program)

    vm = doeff_vm.PyVM()
    vm.run(program)
    after = vm.memory_stats()

    assert after["arena_capacity"] == 0
    assert after["dispatch_capacity"] == 0
    assert after["segment_dispatch_binding_capacity"] == 0
    assert after["scope_state_capacity"] == 0
    assert after["scope_writer_log_capacity"] == 0
    assert after["retired_scope_state_capacity"] == 0
    assert after["retired_scope_writer_log_capacity"] == 0
