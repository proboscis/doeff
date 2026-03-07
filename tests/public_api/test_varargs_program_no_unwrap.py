"""Regression tests for preserving Program args through WithIntercept round-trips.

KD-06 tests single `p: Program[int]` args and passes. The real-world bug
surfaces when a @do function uses `*programs: Program` (VAR_POSITIONAL) and is
called via `yield` from another @do function wrapped with `WithIntercept`:
the VM resolves the inner Apply objects to their computed values before
delivering them to the generator body.

Root cause (per Oracle analysis):
  WithIntercept round-trips DoCtrl::Apply through Python (call_expr_to_pyobject)
  which strips Pure wrappers from args. When the interceptor returns the Apply
  unchanged, the VM re-classifies it via classify_call_expr, sees bare Apply
  objects as non-Pure args, and evaluates them via ApplyResolveArg.

"""

from __future__ import annotations

from doeff_vm import WithIntercept

from doeff import (
    Effect,
    Program,
    ProgramBase,
    default_handlers,
    do,
    run,
)
from doeff.program import DoCtrl


@do
def _identity_interceptor(effect: Effect):
    return effect


class TestWithInterceptVariadicProgramNoUnwrap:
    """WithIntercept must preserve Program-annotated args as opaque programs."""

    def test_without_intercept_works(self) -> None:
        """Baseline: without WithIntercept, varargs Program works."""

        @do
        def produce(x: int):
            return x * 10

        @do
        def gather_programs(*programs: Program, concurrency: int):
            for p in programs:
                assert isinstance(p, (ProgramBase, DoCtrl)), (
                    f"Expected Program, got {type(p).__name__}: {p!r}"
                )
            results = []
            for p in programs:
                val = yield p
                results.append(val)
            return results

        @do
        def main():
            result = yield gather_programs(produce(1), produce(2), produce(3), concurrency=2)
            return result

        result = run(main(), handlers=default_handlers())
        assert result.value == [10, 20, 30]

    def test_with_intercept_unwraps_varargs(self) -> None:
        """BUG: WithIntercept causes *programs: Program to be resolved.

        The programs arrive as computed values (int) instead of
        Program objects (Apply/DoCtrl).
        """

        @do
        def produce(x: int):
            return x * 10

        @do
        def gather_programs(*programs: Program, concurrency: int):
            for p in programs:
                assert isinstance(p, (ProgramBase, DoCtrl)), (
                    f"Expected Program, got {type(p).__name__}: {p!r}"
                )
            results = []
            for p in programs:
                val = yield p
                results.append(val)
            return results

        @do
        def main():
            result = yield gather_programs(produce(1), produce(2), produce(3), concurrency=2)
            return result

        p = WithIntercept(_identity_interceptor, main())
        result = run(p, handlers=default_handlers())
        assert result.value == [10, 20, 30]

    def test_with_intercept_single_program_arg(self) -> None:
        """Check if single Program[T] annotation is also affected."""

        @do
        def produce():
            return 42

        @do
        def inspect_arg(p: Program[int]):
            assert isinstance(p, (ProgramBase, DoCtrl)), (
                f"Expected Program, got {type(p).__name__}: {p!r}"
            )
            val = yield p
            return val + 100

        @do
        def main():
            result = yield inspect_arg(produce())
            return result

        p = WithIntercept(_identity_interceptor, main())
        result = run(p, handlers=default_handlers())
        assert result.value == 142

    def test_with_intercept_mixed_args(self) -> None:
        """Program *args + plain kwargs under WithIntercept."""

        @do
        def produce(x: int):
            return x * 10

        @do
        def gather_with_label(*programs: Program, label: str):
            for p in programs:
                assert isinstance(p, (ProgramBase, DoCtrl)), (
                    f"Expected Program, got {type(p).__name__}: {p!r}"
                )
            results = []
            for p in programs:
                val = yield p
                results.append(val)
            return f"{label}: {results}"

        @do
        def main():
            result = yield gather_with_label(produce(1), produce(2), label="test")
            return result

        p = WithIntercept(_identity_interceptor, main())
        result = run(p, handlers=default_handlers())
        assert result.value == "test: [10, 20]"
