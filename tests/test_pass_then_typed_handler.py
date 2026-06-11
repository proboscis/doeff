"""Regression: catch-all handler Pass() must respect types filter on next handler.

Reproduces a bug found in mediagen where the handler stack is:

    default_handlers
      → memo_rewriter (types=None, catch-all, does isinstance+Pass)
        → replace_handler (types=(ReplaceFx,))
          → WithIntercept(_slog_interceptor, types=(WriterTellEffect,))
            → program

When the program yields slog (WriterTellEffect/PyTell), WithIntercept intercepts
it first, then the effect continues outward. The memo_rewriter passes it (not
AnalyzeFx), and the VM must skip replace_handler (types don't match). The bug was
that replace_handler received a PyTell, crashing on accessing a field that only
exists on ReplaceFx.
"""

from dataclasses import dataclass

import doeff_vm
from doeff_vm import WithHandler

from doeff import (
    Effect,
    EffectBase,
    EffectGenerator,
    WriterTellEffect,
    do,
    run,
    slog,
)
from tests._run_helpers import run_with_defaults

# -- Effects -----------------------------------------------------------------


@dataclass(frozen=True)
class ReplaceFx(EffectBase):
    target: str
    duck_original: bool = True


@dataclass(frozen=True)
class AnalyzeFx(EffectBase):
    query: str


# -- Interceptor (mirrors mediagen's _slog_to_loguru) -----------------------


@do
def slog_interceptor(effect: Effect):
    """Intercepts WriterTellEffect, yields GetCallStack (side effect), returns original."""
    if isinstance(effect, WriterTellEffect) and isinstance(effect.message, dict):
        yield doeff_vm.GetCallStack()
    return effect


# -- Handlers ----------------------------------------------------------------


@do
def memo_rewriter(effect: Effect, k):
    if not isinstance(effect, AnalyzeFx):
        yield doeff_vm.Pass(effect, k)
        return
    return (yield doeff_vm.Resume(k, f"memo:{effect.query}"))


@do
def replace_handler(effect: Effect, k):
    if not isinstance(effect, ReplaceFx):
        yield doeff_vm.Pass(effect, k)
        return
    label = "ducked" if effect.duck_original else "replaced"
    return (yield doeff_vm.Resume(k, f"{label}:{effect.target}"))


# -- Programs ----------------------------------------------------------------


@do
def prog_slog() -> EffectGenerator[None]:
    yield slog(msg="analyzing video")
    return None


@do
def prog_slog_then_replace() -> EffectGenerator[tuple[str]]:
    yield slog(msg="starting")
    r = yield ReplaceFx("audio")
    return (r,)


@do
def prog_slog_then_analyze() -> EffectGenerator[tuple[str]]:
    yield slog(msg="starting")
    a = yield AnalyzeFx("vid")
    return (a,)


@do
def prog_replace() -> EffectGenerator[str]:
    return (yield ReplaceFx("audio"))


@do
def prog_analyze() -> EffectGenerator[str]:
    return (yield AnalyzeFx("video"))


# -- Helpers -----------------------------------------------------------------


def _mediagen_stack(program):
    """Exact mediagen interpreter topology.

    mediagen does reversed([..., replace_audio, memo_rewriter]), which means
    memo_rewriter is wrapped FIRST (innermost), replace_handler SECOND (outer):

    run(default_handlers) → replace_handler → memo_rewriter → WithIntercept → program

    Effect path for slog: program → WithIntercept → memo_rewriter(Pass) → replace_handler
    """
    intercepted = WithIntercept(  # noqa: F821 - legacy removed API reference is intentionally preserved
        slog_interceptor, program, types=(WriterTellEffect,), mode="include"
    )
    wrapped = intercepted
    # memo_rewriter first (innermost), replace_handler wraps it (outer)
    for h in [memo_rewriter, replace_handler]:
        wrapped = WithHandler(h, wrapped)
    return run(wrapped, handlers=[*default_handlers()])  # noqa: F821 - legacy removed API reference is intentionally preserved


def _no_intercept_stack(program):
    """Same but without WithIntercept — control group."""
    wrapped = program
    for h in [memo_rewriter, replace_handler]:
        wrapped = WithHandler(h, wrapped)
    return run_with_defaults(wrapped)


# -- Tests: full mediagen stack (WithIntercept + default_handlers) -----------


class TestFullMediagenStack:
    pass


# -- Tests: without WithIntercept (control) ----------------------------------


class TestWithoutIntercept:
    def test_slog_skips_typed_handler(self):
        result = _no_intercept_stack(prog_slog())
        assert result.is_ok(), f"Expected ok, got error: {result.error}"

    def test_replace_still_works(self):
        result = _no_intercept_stack(prog_replace())
        assert result.is_ok(), f"Expected ok, got error: {result.error}"
        assert result.value == "ducked:audio"

    def test_slog_then_replace(self):
        result = _no_intercept_stack(prog_slog_then_replace())
        assert result.is_ok(), f"Expected ok, got error: {result.error}"
        assert result.value == ("ducked:audio",)


# -- Tests: async paths -----------------------------------------------------


class TestAsyncPaths:
    pass
