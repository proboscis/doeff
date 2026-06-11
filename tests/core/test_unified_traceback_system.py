from __future__ import annotations

from dataclasses import dataclass

from doeff_vm import Pass, Resume, WithHandler

# REMOVED: from doeff import ProgramCallStack
from doeff import Effect, EffectBase, Program, do
from tests._run_helpers import run_with_defaults

# REMOVED: from doeff.trace import TraceDispatch
# REMOVED: from doeff.traceback import attach_doeff_traceback, get_attached_doeff_traceback


@dataclass(frozen=True, kw_only=True)
class NeedsHandler(EffectBase):
    value: int


@dataclass(frozen=True, kw_only=True)
class Explode(EffectBase):
    pass






def test_delegation_chain_routes_to_outer_handler() -> None:
    @do
    def inner_handler(effect: Effect, k):
        if isinstance(effect, NeedsHandler):
            delegated_result = yield effect
            return delegated_result
        yield Pass(effect, k)

    @do
    def outer_handler(effect: Effect, k):
        if isinstance(effect, NeedsHandler):
            return (yield Resume(k, effect.value))
        yield Pass(effect, k)

    @do
    def body() -> Program[int]:
        result = yield NeedsHandler(value=7)
        return result

    wrapped = WithHandler(outer_handler, WithHandler(inner_handler, body()))
    result = run_with_defaults(wrapped)
    assert result.is_ok(), result.error
    assert result.value == 7
