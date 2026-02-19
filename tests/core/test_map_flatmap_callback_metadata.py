from __future__ import annotations

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


def test_map_trace_uses_mapper_function_name() -> None:
    def mapper(value: int):
        if False:  # pragma: no cover - force generator callback for frame capture
            yield None
        return value + 1

    @do
    def body():
        mapped = Program.map(Program.pure(1), mapper)
        _ = yield mapped
        trace = yield ProgramTrace()
        return trace

    result = run(body(), handlers=default_handlers())
    assert result.is_ok(), result.error
    trace = result.value
    frame_names = [entry.function_name for entry in trace if isinstance(entry, TraceFrame)]
    assert mapper.__name__ in frame_names


def test_flat_map_trace_uses_binder_function_name() -> None:
    def binder(value: int):
        if False:  # pragma: no cover - force generator callback for frame capture
            yield None
        return Program.pure(value + 1)

    @do
    def body():
        flat_mapped = Program.flat_map(Program.pure(1), binder)
        _ = yield flat_mapped
        trace = yield ProgramTrace()
        return trace

    result = run(body(), handlers=default_handlers())
    assert result.is_ok(), result.error
    trace = result.value
    frame_names = [entry.function_name for entry in trace if isinstance(entry, TraceFrame)]
    assert binder.__name__ in frame_names
