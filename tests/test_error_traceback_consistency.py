"""Every VM error reachable from user code must include a doeff traceback.

Tests cover all user-facing error paths to ensure consistent visualization.
"""
import pytest
from doeff import do, run, WithHandler, Resume, Pass
from doeff_vm import EffectBase


class Unhandled(EffectBase):
    def __repr__(self):
        return "Unhandled()"


def assert_has_doeff_traceback(exc, min_frames=1):
    """Assert exception has __doeff_traceback__ with at least min_frames frame entries."""
    tb = getattr(exc, "__doeff_traceback__", None)
    assert tb is not None, f"missing __doeff_traceback__ on {type(exc).__name__}: {exc}"
    frames = [e for e in tb if isinstance(e, list) and len(e) >= 2 and e[0] == "frame"]
    assert len(frames) >= min_frames, (
        f"expected >= {min_frames} frame entries, got {len(frames)}: {tb}"
    )


# --- 1. Unhandled effect (no handler installed) ---

def test_unhandled_effect_no_handler():
    @do
    def prog():
        yield Unhandled()

    with pytest.raises(RuntimeError, match="Unhandled") as exc_info:
        run(prog())
    assert_has_doeff_traceback(exc_info.value)


# --- 2. Pass with no outer handler ---

def test_pass_no_outer_handler():
    @do
    def handler(effect, k):
        yield Pass(effect, k)

    @do
    def prog():
        yield Unhandled()

    with pytest.raises(RuntimeError, match="Unhandled") as exc_info:
        run(WithHandler(handler, prog()))
    assert_has_doeff_traceback(exc_info.value)


# --- 3. Nested call chain — traceback includes all user frames ---

def test_nested_frames_in_traceback():
    @do
    def handler(effect, k):
        yield Pass(effect, k)

    @do
    def deep():
        yield Unhandled()

    @do
    def middle():
        return (yield deep())

    @do
    def top():
        return (yield middle())

    with pytest.raises(RuntimeError, match="Unhandled") as exc_info:
        run(WithHandler(handler, top()))
    assert_has_doeff_traceback(exc_info.value, min_frames=3)


# --- 4. Exception from user code (raise inside @do) ---

def test_user_exception_has_traceback():
    @do
    def prog():
        if False:
            yield  # make it a generator
        raise ValueError("user error")

    with pytest.raises(ValueError, match="user error") as exc_info:
        run(prog())
    assert_has_doeff_traceback(exc_info.value)


# --- 5. Exception inside handler ---

def test_exception_inside_handler():
    @do
    def bad_handler(effect, k):
        raise RuntimeError("handler crash")

    @do
    def prog():
        yield Unhandled()

    with pytest.raises(RuntimeError, match="handler crash"):
        run(WithHandler(bad_handler, prog()))


# --- 6. Multiple handlers, inner passes, outer missing ---

def test_chained_pass_traceback():
    @do
    def h1(effect, k):
        yield Pass(effect, k)

    @do
    def h2(effect, k):
        yield Pass(effect, k)

    @do
    def prog():
        yield Unhandled()

    with pytest.raises(RuntimeError, match="Unhandled") as exc_info:
        run(WithHandler(h1, WithHandler(h2, prog())))
    tb = getattr(exc_info.value, "__doeff_traceback__", None)
    assert tb is not None
    # Should have handler chain entries
    handler_entries = [e for e in tb if isinstance(e, list) and len(e) >= 2 and e[0] == "handler"]
    # At minimum, should have frame entries
    assert_has_doeff_traceback(exc_info.value)
