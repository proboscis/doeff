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

import doeff

# REMOVED: from doeff_core_effects.cache import in_memory_cache_handler, memo_rewriters
from doeff import EffectBase, WithHandler, do


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
