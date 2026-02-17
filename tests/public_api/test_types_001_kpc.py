"""SPEC-TYPES-001 §11.3 — KPC Dispatch and Auto-Unwrap Tests (KD-01 through KD-13).

MACRO MODEL: @do call path produces DoCtrl (Call) semantics resolved by
the VM trampoline, NOT an EffectBase dispatched through the handler stack.
Tests updated to assert macro-model invariants; RED against current runtime.
"""

from __future__ import annotations

from doeff import (
    Ask,
    EffectBase,
    Get,
    KleisliProgram,
    Program,
    ProgramBase,
    Put,
    default_handlers,
    do,
    run,
)
from doeff.program import DoCtrl, GeneratorProgram


def _prog(gen_factory):
    """Wrap a generator factory into a GeneratorProgram."""
    return GeneratorProgram(gen_factory)


# ---------------------------------------------------------------------------
# KD-01: @do function call creates a DoCtrl/ProgramBase (macro model)
# ---------------------------------------------------------------------------


class TestKD01KPCCreation:
    def test_call_returns_program(self) -> None:
        """@do call returns a ProgramBase/DoCtrl object."""

        @do
        def add_one(x: int):
            return x + 1

        result = add_one(1)
        # Runtime contract: result is program-shaped (DoCtrl/ProgramBase)
        assert isinstance(result, (ProgramBase, DoCtrl)), (
            f"@do call should return ProgramBase/DoCtrl, got {type(result).__name__}"
        )

    def test_call_does_not_execute_body(self) -> None:
        executed = []

        @do
        def side_effectful(x: int):
            executed.append(x)
            return x

        _kpc = side_effectful(42)
        assert executed == [], "Body should not execute on call"


# ---------------------------------------------------------------------------
# KD-02: KPC is NOT an effect — it's a Call DoCtrl resolved by the VM
# ---------------------------------------------------------------------------


class TestKD02KPCProgramShape:
    def test_kpc_is_programbase(self) -> None:
        """KPC must be program-shaped for composability APIs."""

        @do
        def identity(x: int):
            return x

        kpc = identity(1)
        assert isinstance(kpc, (ProgramBase, DoCtrl))

    def test_call_result_is_doexpr(self) -> None:
        """@do call result should still be a DoExpr runtime object."""

        @do
        def identity(x: int):
            return x

        assert isinstance(identity(1), (DoCtrl, ProgramBase))


# ---------------------------------------------------------------------------
# KD-03: run(kpc, handlers=[]) SUCCEEDS — no handler needed for @do calls
# ---------------------------------------------------------------------------


class TestKD03NoHandlerNeeded:
    def test_empty_handlers_succeeds(self) -> None:
        """Macro model: @do calls are VM DoCtrl, resolved without any handler."""

        @do
        def identity(x: int):
            return x

        # In macro model, this should succeed — no kpc handler needed
        result = run(identity(1), handlers=[])
        assert result.value == 1

    def test_pure_computation_no_handlers(self) -> None:
        """Pure @do function should run with empty handler list."""

        @do
        def add(a: int, b: int):
            return a + b

        result = run(add(3, 4), handlers=[])
        assert result.value == 7


# ---------------------------------------------------------------------------
# KD-04: run(kpc, handlers=default_handlers()) succeeds
# ---------------------------------------------------------------------------


class TestKD04DefaultHandlers:
    def test_kpc_with_defaults(self) -> None:
        @do
        def add(a: int, b: int):
            return a + b

        result = run(add(3, 4), handlers=default_handlers())
        assert result.value == 7

    def test_default_handlers_no_kpc_handler(self) -> None:
        """Macro model: default_handlers() should NOT include a kpc handler."""
        handlers = default_handlers()
        handler_names = [getattr(h, "name", repr(h)) for h in handlers]
        handler_names_lower = [n.lower() for n in handler_names]
        assert not any("kpc" in name for name in handler_names_lower), (
            f"default_handlers() must not include kpc handler in macro model, got: {handler_names}"
        )


# ---------------------------------------------------------------------------
# KD-05: Plain-typed args auto-unwrap
# ---------------------------------------------------------------------------


class TestKD05AutoUnwrap:
    def test_effect_arg_is_resolved(self) -> None:
        @do
        def use_value(val: str):
            return f"got:{val}"

        @do
        def main():
            result = yield use_value(Ask("key"))
            return result

        result = run(main(), handlers=default_handlers(), env={"key": "hello"})
        assert result.value == "got:hello"

    def test_program_arg_is_resolved(self) -> None:
        @do
        def produce():
            return 42

        @do
        def consume(val: int):
            return val + 1

        @do
        def main():
            result = yield consume(produce())
            return result

        result = run(main(), handlers=default_handlers())
        assert result.value == 43

    def test_multiple_args_resolved(self) -> None:
        @do
        def combine(a: str, b: int):
            return f"{a}={b}"

        @do
        def main():
            result = yield combine(Ask("name"), Get("count"))
            return result

        result = run(
            main(),
            handlers=default_handlers(),
            env={"name": "items"},
            store={"count": 5},
        )
        assert result.value == "items=5"


# ---------------------------------------------------------------------------
# KD-06: Program[T]-annotated args NOT unwrapped
# ---------------------------------------------------------------------------


class TestKD06ProgramAnnotationNoUnwrap:
    def test_program_annotation_passes_program_object(self) -> None:
        @do
        def inspect_arg(p: Program[int]):
            # p should be the DoExpr itself, not the resolved value
            assert isinstance(p, (ProgramBase, EffectBase, DoCtrl))
            val = yield p  # manually resolve
            return val + 100

        @do
        def produce():
            return 42

        @do
        def main():
            result = yield inspect_arg(produce())
            return result

        result = run(main(), handlers=default_handlers())
        assert result.value == 142


# ---------------------------------------------------------------------------
# KD-07: Effect-annotated args NOT unwrapped
# ---------------------------------------------------------------------------


class TestKD07EffectAnnotationNoUnwrap:
    def test_effect_annotation_passes_effect_object(self) -> None:
        @do
        def inspect_effect(e: EffectBase):
            assert isinstance(e, EffectBase)
            val = yield e  # manually resolve
            return val

        @do
        def main():
            result = yield inspect_effect(Ask("key"))
            return result

        result = run(
            main(),
            handlers=default_handlers(),
            env={"key": "value"},
        )
        assert result.value == "value"


# ---------------------------------------------------------------------------
# KD-08: Unannotated args default to auto-unwrap
# ---------------------------------------------------------------------------


class TestKD08UnannotatedUnwrap:
    def test_no_annotation_unwraps(self) -> None:
        @do
        def use_value(val):
            return f"got:{val}"

        @do
        def main():
            result = yield use_value(Ask("key"))
            return result

        result = run(main(), handlers=default_handlers(), env={"key": "hello"})
        assert result.value == "got:hello"


# ---------------------------------------------------------------------------
# KD-09: Non-generator early return
# ---------------------------------------------------------------------------


class TestKD09NonGeneratorReturn:
    def test_plain_return(self) -> None:
        @do
        def pure_add(a: int, b: int):
            return a + b  # no yields

        result = run(pure_add(3, 4), handlers=default_handlers())
        assert result.value == 7

    def test_plain_return_no_handlers(self) -> None:
        """Macro model: pure @do should work without any handlers."""

        @do
        def pure_add(a: int, b: int):
            return a + b

        result = run(pure_add(3, 4), handlers=[])
        assert result.value == 7


# ---------------------------------------------------------------------------
# KD-10: @do preserves metadata
# ---------------------------------------------------------------------------


class TestKD10Metadata:
    def test_preserves_name(self) -> None:
        @do
        def my_function(x: int):
            """My docstring."""
            return x

        assert my_function.__name__ == "my_function"

    def test_preserves_doc(self) -> None:
        @do
        def documented(x: int):
            """This is documented."""
            return x

        assert documented.__doc__ == "This is documented."

    def test_preserves_qualname(self) -> None:
        @do
        def inner_func(x: int):
            return x

        assert "inner_func" in inner_func.__qualname__


# ---------------------------------------------------------------------------
# KD-11: @do on class methods (descriptor protocol)
# ---------------------------------------------------------------------------


class TestKD11MethodDecoration:
    def test_do_on_method(self) -> None:
        class Service:
            @do
            def fetch(self, key: str):
                value = yield Ask(key)
                return value

        svc = Service()
        result = run(svc.fetch("api_key"), handlers=default_handlers(), env={"api_key": "secret"})
        assert result.value == "secret"


# ---------------------------------------------------------------------------
# KD-12: Kleisli composition >> operator
# ---------------------------------------------------------------------------


class TestKD12KleisliComposition:
    def test_rshift_reports_non_composable_call_nodes(self) -> None:
        @do
        def step_one(x: int):
            return x + 10

        @do
        def step_two(x: int):
            return x * 2

        pipeline = step_one >> step_two
        assert isinstance(pipeline, KleisliProgram)

        try:
            result = run(pipeline(5), handlers=default_handlers())
        except TypeError as exc:
            assert "Kleisli program must return a Program or Effect" in str(exc)
        else:
            assert result.is_err()
            assert isinstance(result.error, TypeError)
            assert "Kleisli program must return a Program or Effect" in str(result.error)


# ---------------------------------------------------------------------------
# KD-13: Nested @do calls resolve correctly
# ---------------------------------------------------------------------------


class TestKD13NestedDoCalls:
    def test_do_calling_do(self) -> None:
        @do
        def inner(x: int):
            return x * 2

        @do
        def outer(x: int):
            doubled = yield inner(x)
            return doubled + 1

        result = run(outer(5), handlers=default_handlers())
        assert result.value == 11  # 5*2 + 1

    def test_three_level_nesting(self) -> None:
        @do
        def level3(x: int):
            return x + 1

        @do
        def level2(x: int):
            val = yield level3(x)
            return val * 2

        @do
        def level1(x: int):
            val = yield level2(x)
            return val + 100

        result = run(level1(5), handlers=default_handlers())
        assert result.value == 112  # ((5+1)*2) + 100

    def test_nested_with_effects(self) -> None:
        @do
        def fetch_and_double(key: str):
            val = yield Get(key)
            return val * 2

        @do
        def process():
            doubled = yield fetch_and_double("x")
            yield Put("result", doubled)
            return doubled

        result = run(process(), handlers=default_handlers(), store={"x": 21, "result": 0})
        assert result.value == 42
        assert result.raw_store["result"] == 42

    def test_nested_do_calls_no_kpc_handler(self) -> None:
        """Macro model: nested @do calls resolve via VM trampoline, not kpc handler.

        Pure nested @do (no actual effects) should work with empty handlers.
        """

        @do
        def inner(x: int):
            return x * 2

        @do
        def outer(x: int):
            doubled = yield inner(x)
            return doubled + 1

        result = run(outer(5), handlers=[])
        assert result.value == 11
