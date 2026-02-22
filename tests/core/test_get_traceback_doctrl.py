from __future__ import annotations

from dataclasses import dataclass

from doeff import Program, do
from doeff._types_internal import EffectBase
from doeff.rust_vm import (
    Delegate,
    GetTraceback,
    Pass,
    Resume,
    WithHandler,
    default_handlers,
    run,
)


@dataclass(frozen=True, kw_only=True)
class Ping(EffectBase):
    value: int


def test_get_traceback_inside_dispatch_returns_parent_chain_frames() -> None:
    captured: dict[str, list[object]] = {}

    def inner_handler(effect, _k):
        if isinstance(effect, Ping):
            return (yield Delegate())
        yield Pass()

    def outer_handler(effect, k):
        if isinstance(effect, Ping):
            hops = yield GetTraceback(k)
            captured["hops"] = hops
            return (yield Resume(k, effect.value + 1))
        yield Pass()

    @do
    def program_body() -> Program[int]:
        return (yield Ping(value=41))

    wrapped = WithHandler(outer_handler, WithHandler(inner_handler, program_body()))
    result = run(wrapped, handlers=default_handlers())
    assert result.is_ok(), result.error
    assert result.value == 42

    hops = captured.get("hops")
    assert isinstance(hops, list)
    assert len(hops) >= 2

    first_hop = hops[0]
    second_hop = hops[1]
    assert getattr(first_hop, "frames")
    assert getattr(second_hop, "frames")

    first_frame = first_hop.frames[0]
    second_frame = second_hop.frames[0]
    assert first_frame.func_name.endswith("inner_handler")
    assert second_frame.func_name.endswith("program_body")
    assert first_frame.source_file.endswith("test_get_traceback_doctrl.py")
    assert second_frame.source_file.endswith("test_get_traceback_doctrl.py")
