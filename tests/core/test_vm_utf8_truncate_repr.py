from __future__ import annotations

from dataclasses import dataclass

from doeff import Effect, Program, do
from doeff._types_internal import EffectBase
from doeff.rust_vm import Pass, Resume, WithHandler, default_handlers, run
from doeff.trace import TraceDispatch


@dataclass(frozen=True, kw_only=True)
class Utf8BoundaryEffect(EffectBase):
    text: str

    def __repr__(self) -> str:
        # Keep a 1-byte prefix so byte 200 often falls inside a multibyte codepoint.
        return f"X{self.text}"


@do
def _resume_utf8_boundary_effect(effect: Effect, k: object):
    if isinstance(effect, Utf8BoundaryEffect):
        return (yield Resume(k, effect.text))
    yield Pass()


@do
def _program_with_effect(text: str) -> Program[None]:
    _ = yield Utf8BoundaryEffect(text=text)
    raise ValueError("boom")
    yield


def _dispatch_from_trace(text: str) -> TraceDispatch:
    result = run(
        WithHandler(_resume_utf8_boundary_effect, _program_with_effect(text)),
        handlers=default_handlers(),
        print_doeff_trace=False,
    )
    assert result.is_err()
    assert isinstance(result.error, ValueError)
    traceback_data = result.traceback_data
    assert traceback_data is not None
    dispatches = [entry for entry in traceback_data.entries if isinstance(entry, TraceDispatch)]
    assert dispatches
    return dispatches[-1]


def _assert_truncated_utf8(text: str) -> None:
    dispatch = _dispatch_from_trace(text)
    assert dispatch.effect_repr.endswith("...")
    assert len(dispatch.effect_repr.encode("utf-8")) <= 203
    assert dispatch.effect_repr.encode("utf-8").decode("utf-8") == dispatch.effect_repr


def test_truncate_repr_japanese() -> None:
    _assert_truncated_utf8("日" * 67)


def test_truncate_repr_emoji() -> None:
    _assert_truncated_utf8("😀" * 50)


def test_truncate_repr_mixed() -> None:
    _assert_truncated_utf8("abc日😀" * 20)


def test_truncate_repr_ascii_short() -> None:
    payload = "a" * 120
    dispatch = _dispatch_from_trace(payload)
    assert dispatch.effect_repr == f"X{payload}"
