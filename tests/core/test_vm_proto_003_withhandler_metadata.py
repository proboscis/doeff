from __future__ import annotations

from typing import Any

import doeff_vm
import pytest

from doeff import Delegate, Effect, EffectBase, WithHandler, do
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


def test_withhandler_accepts_do_handler_as_pykleisli() -> None:
    @do
    def handler(_effect: Effect, _k):
        yield Delegate()

    control = WithHandler(handler, _probe_program())
    assert isinstance(control.handler, doeff_vm.PyKleisli)


def test_withhandler_rejects_plain_handler_with_do_hint() -> None:
    def bad_handler(_effect, _k):
        return 123

    with pytest.raises(TypeError) as exc_info:
        WithHandler(bad_handler, _probe_program())

    message = str(exc_info.value)
    assert "bad_handler" in message
    assert "@do" in message


def test_handler_trace_metadata_uses_registration_values_not_runtime_dunder_reads() -> None:
    @do
    def crash_handler(effect: Effect, _k):
        if isinstance(effect, _ProbeEffect):
            raise RuntimeError("boom")
        yield Delegate()

    control = WithHandler(crash_handler, _probe_program())
    original_callable = getattr(crash_handler, "__wrapped__", None)
    assert original_callable is not None
    original_qualname = crash_handler.__qualname__
    original_source_file = original_callable.__code__.co_filename
    original_source_line = original_callable.__code__.co_firstlineno

    # Mutate the underlying callable after registration.
    # VM-PROTO-003 requires trace metadata to come from registration-time fields,
    # not runtime getattr("__name__") / getattr("__code__") probing.
    crash_handler.__name__ = "mutated_handler_name"
    crash_handler.__qualname__ = "mutated_handler_qualname"

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

    assert handler_name == original_qualname
    assert handler_source_file == original_source_file
    assert handler_source_line == original_source_line
