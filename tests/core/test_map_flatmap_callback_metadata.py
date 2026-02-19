from __future__ import annotations

import pytest

from doeff import Program, do
from doeff.effects import ProgramTrace
from doeff.rust_vm import default_handlers, run
from doeff.trace import TraceFrame


def test_map_stores_mapper_metadata_at_construction_time() -> None:
    def mapper(value: int) -> int:
        return value + 1

    mapped = Program.map(Program.pure(1), mapper)
    assert hasattr(mapped, "mapper_meta")
    mapper_meta = mapped.mapper_meta
    assert mapper_meta["function_name"] == mapper.__code__.co_name
    assert mapper_meta["source_file"] == mapper.__code__.co_filename
    assert mapper_meta["source_line"] == mapper.__code__.co_firstlineno


def test_flat_map_stores_binder_metadata_at_construction_time() -> None:
    def binder(value: int):
        return Program.pure(value + 1)

    flat_mapped = Program.flat_map(Program.pure(1), binder)
    assert hasattr(flat_mapped, "binder_meta")
    binder_meta = flat_mapped.binder_meta
    assert binder_meta["function_name"] == binder.__code__.co_name
    assert binder_meta["source_file"] == binder.__code__.co_filename
    assert binder_meta["source_line"] == binder.__code__.co_firstlineno


def test_map_rejects_callable_without_code_object() -> None:
    with pytest.raises(TypeError, match="__code__ is missing"):
        Program.map(Program.pure([1, 2, 3]), len)


def test_map_runs_with_plain_function_mapper() -> None:
    def mapper(value: int) -> int:
        return value + 1

    @do
    def body():
        mapped = Program.map(Program.pure(1), mapper)
        return (yield mapped)

    result = run(body(), handlers=default_handlers())
    assert result.is_ok(), result.error
    assert result.value == 2


def test_flat_map_runs_with_plain_function_binder() -> None:
    def binder(value: int):
        return Program.pure(value + 1)

    @do
    def body():
        flat_mapped = Program.flat_map(Program.pure(1), binder)
        return (yield flat_mapped)

    result = run(body(), handlers=default_handlers())
    assert result.is_ok(), result.error
    assert result.value == 2
