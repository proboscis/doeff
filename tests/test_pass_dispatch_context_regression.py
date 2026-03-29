"""Minimal repro for PR #367 regression: Pass/Delegate outside dispatch context.

After the OCaml 5 architecture refactor (PR #367), current_dispatch_id() returns
None in certain nested handler configurations. This causes:
  - "Pass called outside dispatch context" (when a handler tries to Pass)
  - "delegate: no outer handler" (when memo_rewriter tries to Delegate)

The root cause is that current_dispatch_id() was changed from reading stored
metadata on the Continuation to deriving it via topology walks. The topology walk
fails to find the dispatch context in certain handler nesting patterns.
"""

from __future__ import annotations

import pytest

import doeff
from doeff import WithHandler, do
from doeff import EffectBase
# REMOVED: from doeff_core_effects.cache import in_memory_cache_handler, memo_rewriters
from doeff import default_handlers, run
from doeff import EffectGenerator


class EffectA(EffectBase):
    pass


@do
def effect_a_handler(effect: EffectA, k: object):
    if not isinstance(effect, EffectA):
        yield doeff.Pass()
        return None
    return (yield doeff.Resume(k, "handled"))


def _compose(program, *handlers):
    wrapped = program
    for handler in reversed(handlers):
        wrapped = WithHandler(handler, wrapped)
    return wrapped


@pytest.mark.skip(reason="uses removed API: memo_rewriters, in_memory_cache_handler")
def test_memo_rewriter_delegate_finds_outer_handler():
    """memo_rewriter(EffectA) + cache_handler: Delegate must find the cache handler.

    This is the pattern from mediagen's make_memo_rewriter:
      effect_a_handler (Pass non-EffectA) -> memo_rewriter (Delegate CacheGet) -> cache_handler
    The Delegate fails with "no outer handler" because current_dispatch_id() returns None.
    """

    @do
    def program() -> EffectGenerator:
        return (yield EffectA())

    wrapped = _compose(
        program(),
        effect_a_handler,
        *memo_rewriters(EffectA),
        in_memory_cache_handler(),
    )
    result = run(wrapped, handlers=default_handlers())
    assert result.is_ok(), f"Expected Ok, got: {result.error}"
    assert result.value == "handled"
