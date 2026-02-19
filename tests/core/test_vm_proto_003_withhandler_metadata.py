from __future__ import annotations

from typing import Any

import doeff_vm
import pytest

from doeff import Delegate, EffectBase, WithHandler, do
from doeff.rust_vm import default_handlers, run


class _ProbeEffect(EffectBase):
    pass


@do
def _probe_program():
    yield _ProbeEffect()
    return "done"


def _extract_dispatch_entry(entries: list[Any]) -> Any | None:
    for entry in entries:
        if isinstance(entry, dict) and entry.get("kind") == "dispatch":
            return entry
        if hasattr(entry, "handler_name") and hasattr(entry, "handler_source_file"):
            return entry
    return None


def test_withhandler_wraps_generator_results_as_doeff_generator() -> None:
    def handler(_effect, _k):
        yield Delegate()

    control = WithHandler(handler, _probe_program())
    wrapped = control.handler

    wrapped_result = wrapped(_ProbeEffect(), object())
    assert isinstance(wrapped_result, doeff_vm.DoeffGenerator)


def test_withhandler_non_generator_return_raises_typeerror_with_hint() -> None:
    def bad_handler(_effect, _k):
        return 123

    control = WithHandler(bad_handler, _probe_program())
    wrapped = control.handler

    with pytest.raises(TypeError) as exc_info:
        wrapped(_ProbeEffect(), object())

    message = str(exc_info.value)
    assert "bad_handler" in message
    assert "must return a generator" in message
    assert "Did you forget 'yield'?" in message


def test_handler_trace_metadata_uses_registration_values_not_runtime_dunder_reads() -> None:
    def crash_handler(effect, _k):
        if isinstance(effect, _ProbeEffect):
            raise RuntimeError("boom")
        yield Delegate()

    control = WithHandler(crash_handler, _probe_program())

    # Mutate the wrapped callable after registration.
    # VM-PROTO-003 requires trace metadata to come from registration-time fields,
    # not runtime getattr("__name__") / getattr("__code__") probing.
    control.handler.__name__ = "mutated_handler_name"

    result = run(control, handlers=default_handlers(), print_doeff_trace=False)
    assert result.is_err()
    assert result.traceback_data is not None

    dispatch = _extract_dispatch_entry(list(result.traceback_data.entries))
    assert dispatch is not None

    if isinstance(dispatch, dict):
        handler_name = dispatch["handler_name"]
        handler_source_file = dispatch["handler_source_file"]
        handler_source_line = dispatch["handler_source_line"]
    else:
        handler_name = dispatch.handler_name
        handler_source_file = dispatch.handler_source_file
        handler_source_line = dispatch.handler_source_line

    assert handler_name == crash_handler.__qualname__
    assert handler_source_file == crash_handler.__code__.co_filename
    assert handler_source_line == crash_handler.__code__.co_firstlineno
