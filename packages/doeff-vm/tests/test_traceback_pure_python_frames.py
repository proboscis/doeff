"""Reproduce: pure Python function frames missing from doeff traceback.

When a pure Python function (no effects, no yields) raises an exception
inside a handler chain, the doeff traceback should include the Python
stack frames showing exactly which file/line in the pure function caused
the error.

Currently, the traceback only shows the handler chain and the final
exception message, losing the call site information needed for debugging.

Example from production:
    A @do program calls `compute_signal(data)` which is a pure function.
    Inside compute_signal, a numpy matmul produces NaN, and a subsequent
    `.index` access on None raises AttributeError.

    Expected traceback:
        compute_signal()  cllm_compute.py:184
            cb = np.diag(1.0 / db) @ cb @ np.diag(1.0 / db)
        ... handler chain ...
        AttributeError: 'NoneType' object has no attribute 'index'

    Actual traceback:
        SimTimeRuntime._protocol_handler()  sim_time.py:48
        get_openai_client.try_ask_client()  client.py:160
        ... handler chain ...
        AttributeError: 'NoneType' object has no attribute 'index'

    The cllm_compute.py:184 frame is completely lost.
"""

import pytest

from doeff import Program, do, run
from doeff_vm import WithHandler


# --- Pure Python function that raises ---

def _level_c():
    """Innermost function — this is where the error originates."""
    result = None
    return result.index  # AttributeError: 'NoneType' object has no attribute 'index'


def _level_b():
    """Intermediate call."""
    return _level_c()


def _level_a():
    """Top-level pure function called from @do program."""
    return _level_b()


# --- Program that calls the pure function ---

@do
def program_calling_pure_function() -> Program[int]:
    """Program that calls a chain of pure Python functions."""
    result = _level_a()  # no yield, pure call — raises inside
    return result


# --- Handler (simple passthrough) ---

@do
def passthrough_handler(effect, k):
    """Handler that passes all effects through."""
    from doeff import Pass
    yield Pass(effect, k)


# --- Test ---

def test_pure_python_frames_in_traceback():
    """Pure Python call frames should appear in __doeff_traceback__.

    When _level_c raises AttributeError, the traceback should contain
    frames for _level_a, _level_b, _level_c so the developer can see
    exactly where the error originated.
    """
    program = WithHandler(passthrough_handler, program_calling_pure_function())

    with pytest.raises(AttributeError, match="'NoneType' object has no attribute 'index'") as exc_info:
        run(program)

    exc = exc_info.value
    tb_data = getattr(exc, '__doeff_traceback__', None)

    # The traceback data should exist
    assert tb_data is not None, (
        "__doeff_traceback__ is None — no traceback data was captured at all"
    )

    # Collect all function names from frame entries
    frame_names = []
    for entry in tb_data:
        if isinstance(entry, (list, tuple)):
            if entry[0] == "frame" and len(entry) >= 4:
                frame_names.append(entry[1])
            elif len(entry) >= 3 and isinstance(entry[2], (int, float)):
                frame_names.append(entry[0])

    # The pure Python call chain should be present
    assert "_level_a" in frame_names, (
        f"_level_a not found in traceback frames: {frame_names}\n"
        f"Full tb_data: {tb_data}"
    )
    assert "_level_b" in frame_names, (
        f"_level_b not found in traceback frames: {frame_names}\n"
        f"Full tb_data: {tb_data}"
    )
    assert "_level_c" in frame_names, (
        f"_level_c not found in traceback frames: {frame_names}\n"
        f"Full tb_data: {tb_data}"
    )


def test_pure_python_frames_without_handler():
    """Same test but without any handler — baseline."""
    with pytest.raises(AttributeError, match="'NoneType' object has no attribute 'index'") as exc_info:
        run(program_calling_pure_function())

    exc = exc_info.value
    tb_data = getattr(exc, '__doeff_traceback__', None)

    # Even without handlers, the pure Python frames should be captured
    if tb_data is not None:
        frame_names = []
        for entry in tb_data:
            if isinstance(entry, (list, tuple)):
                if entry[0] == "frame" and len(entry) >= 4:
                    frame_names.append(entry[1])
                elif len(entry) >= 3 and isinstance(entry[2], (int, float)):
                    frame_names.append(entry[0])

        assert "_level_c" in frame_names, (
            f"_level_c not found in traceback frames: {frame_names}\n"
            f"Full tb_data: {tb_data}"
        )


def test_deep_handler_chain_preserves_python_frames():
    """Multiple nested handlers should still preserve pure Python frames."""

    @do
    def handler_a(effect, k):
        from doeff import Pass
        yield Pass(effect, k)

    @do
    def handler_b(effect, k):
        from doeff import Pass
        yield Pass(effect, k)

    program = WithHandler(
        handler_a,
        WithHandler(
            handler_b,
            program_calling_pure_function()
        )
    )

    with pytest.raises(AttributeError) as exc_info:
        run(program)

    exc = exc_info.value
    tb_data = getattr(exc, '__doeff_traceback__', None)
    assert tb_data is not None

    frame_names = []
    for entry in tb_data:
        if isinstance(entry, (list, tuple)):
            if entry[0] == "frame" and len(entry) >= 4:
                frame_names.append(entry[1])
            elif len(entry) >= 3 and isinstance(entry[2], (int, float)):
                frame_names.append(entry[0])

    assert "_level_c" in frame_names, (
        f"Pure Python frame _level_c missing from traceback under nested handlers.\n"
        f"Frames found: {frame_names}\n"
        f"Full tb_data: {tb_data}"
    )
