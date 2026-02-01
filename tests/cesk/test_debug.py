"""Tests for CESK debug utilities and GetDebugContext effect."""

import os

from doeff import do
from doeff.cesk.debug import (
    DebugContext,
    KFrameInfo,
    KleisliStackEntry,
    build_effect_call_tree,
    extract_k_frame_info,
    extract_kleisli_stack,
    format_k_stack,
    get_debug_context,
)
from doeff.cesk.frames import Kontinuation
from doeff.cesk.run import sync_handlers_preset, sync_run
from doeff.effects import Get, GetDebugContext, Put


class TestKleisliFrame:
    def test_kleisli_frame_tracks_do_function_entries(self) -> None:
        @do
        def inner_func():
            return (yield Get("value"))

        @do
        def outer_func():
            yield Put("value", 42)
            result = yield inner_func()
            return result

        result = sync_run(outer_func(), sync_handlers_preset)

        assert result.value == 42

    def test_return_frame_has_kleisli_metadata(self) -> None:
        from doeff.cesk.frames import ReturnFrame

        def gen():
            yield
            return

        frame = ReturnFrame(
            gen(), {},
            kleisli_function_name="test_func",
            kleisli_filename="/path/to/file.py",
            kleisli_lineno=42,
        )

        assert frame.kleisli_function_name == "test_func"
        assert frame.kleisli_filename == "/path/to/file.py"
        assert frame.kleisli_lineno == 42


class TestFormatKStack:
    def test_format_empty_k_stack(self) -> None:
        k: Kontinuation = []
        output = format_k_stack(k)
        assert "K Frame Stack:" in output
        assert "(empty)" in output

    def test_format_k_stack_with_frames(self) -> None:
        from doeff.cesk.frames import ReturnFrame

        def gen():
            yield
            return

        k: Kontinuation = [
            ReturnFrame(gen(), {}, kleisli_function_name="func_a", kleisli_filename="a.py", kleisli_lineno=10),
            ReturnFrame(gen(), {}, kleisli_function_name="func_b", kleisli_filename="b.py", kleisli_lineno=20),
        ]
        output = format_k_stack(k)

        assert "K Frame Stack:" in output
        assert "ReturnFrame" in output
        assert "func_a" in output
        assert "func_b" in output


class TestExtractKleisliStack:
    def test_extract_empty_stack(self) -> None:
        k: Kontinuation = []
        entries = extract_kleisli_stack(k)
        assert entries == ()

    def test_extract_kleisli_from_return_frames(self) -> None:
        from doeff.cesk.frames import ReturnFrame

        def gen():
            yield
            return

        k: Kontinuation = [
            ReturnFrame(gen(), {}, kleisli_function_name="outer", kleisli_filename="outer.py", kleisli_lineno=10),
            ReturnFrame(gen(), {}, kleisli_function_name="inner", kleisli_filename="inner.py", kleisli_lineno=20),
        ]
        entries = extract_kleisli_stack(k)

        assert len(entries) == 2
        assert entries[0].function_name == "outer"
        assert entries[1].function_name == "inner"


class TestExtractKFrameInfo:
    def test_extract_empty_k(self) -> None:
        k: Kontinuation = []
        infos = extract_k_frame_info(k)
        assert infos == ()

    def test_extract_return_frame_with_kleisli_info(self) -> None:
        from doeff.cesk.frames import ReturnFrame

        def gen():
            yield
            return

        k: Kontinuation = [
            ReturnFrame(gen(), {}, kleisli_function_name="test_func", kleisli_filename="test.py", kleisli_lineno=42),
        ]
        infos = extract_k_frame_info(k)

        assert len(infos) == 1
        assert infos[0].frame_type == "ReturnFrame"
        assert "test_func" in infos[0].description


class TestBuildEffectCallTree:
    def test_empty_k_returns_none(self) -> None:
        k: Kontinuation = []
        tree = build_effect_call_tree(k)
        assert tree is None

    def test_build_tree_with_return_frames(self) -> None:
        from doeff._types_internal import EffectCreationContext
        from doeff.cesk.frames import ReturnFrame

        def dummy_gen():
            yield
            return

        class MockProgramCall:
            function_name: str
            created_at: EffectCreationContext

            def __init__(self, name: str, filename: str, line: int) -> None:
                self.function_name = name
                self.created_at = EffectCreationContext(filename=filename, line=line, function=name)

        inner_pc = MockProgramCall("inner", "inner.py", 20)
        outer_pc = MockProgramCall("outer", "outer.py", 10)

        k: Kontinuation = [
            ReturnFrame(dummy_gen(), {}, program_call=inner_pc),
            ReturnFrame(dummy_gen(), {}, program_call=outer_pc),
        ]
        tree = build_effect_call_tree(k, current_effect="Get")

        assert tree is not None
        assert tree.function_name == "outer"
        assert len(tree.children) == 1
        assert tree.children[0].function_name == "inner"
        assert tree.children[0].effect_type == "Get"


class TestGetDebugContext:
    def test_get_debug_context_empty_k(self) -> None:
        k: Kontinuation = []
        ctx = get_debug_context(k)

        assert isinstance(ctx, DebugContext)
        assert ctx.kleisli_stack == ()
        assert ctx.k_frames == ()

    def test_get_debug_context_with_current_effect(self) -> None:
        from doeff.cesk.frames import ReturnFrame

        def gen():
            yield
            return

        k: Kontinuation = [
            ReturnFrame(gen(), {}, kleisli_function_name="test", kleisli_filename="test.py", kleisli_lineno=10),
        ]
        ctx = get_debug_context(k, current_effect="TestEffect")

        assert ctx.current_effect == "TestEffect"


class TestGetDebugContextEffect:
    def test_get_debug_context_effect_basic(self) -> None:
        @do
        def program():
            ctx = yield GetDebugContext()
            return ctx

        result = sync_run(program(), sync_handlers_preset)

        assert isinstance(result.value, DebugContext)
        assert result.value.current_effect == "GetDebugContextEffect"

    def test_get_debug_context_effect_in_nested_calls(self) -> None:
        @do
        def inner():
            ctx = yield GetDebugContext()
            return ctx

        @do
        def outer():
            return (yield inner())

        result = sync_run(outer(), sync_handlers_preset)

        assert isinstance(result.value, DebugContext)
        assert len(result.value.k_frames) > 0

    def test_get_debug_context_shows_call_chain(self) -> None:
        @do
        def level_3():
            ctx = yield GetDebugContext()
            return ctx

        @do
        def level_2():
            return (yield level_3())

        @do
        def level_1():
            return (yield level_2())

        result = sync_run(level_1(), sync_handlers_preset)

        assert isinstance(result.value, DebugContext)
        formatted = result.value.format_kleisli_stack()
        assert "Kleisli Call Stack:" in formatted


class TestDebugContextFormatting:
    def test_format_kleisli_stack_output(self) -> None:
        ctx = DebugContext(
            kleisli_stack=(
                KleisliStackEntry("func_a", "a.py", 10, 0),
                KleisliStackEntry("func_b", "b.py", 20, 1),
            ),
            k_frames=(),
            effect_call_tree=None,
            current_effect=None,
        )

        output = ctx.format_kleisli_stack()

        assert "Kleisli Call Stack:" in output
        assert "[0] func_a (a.py:10)" in output
        assert "[1] func_b (b.py:20)" in output

    def test_format_k_frames_output(self) -> None:
        ctx = DebugContext(
            kleisli_stack=(),
            k_frames=(
                KFrameInfo("KleisliFrame", "test_func", 0),
                KFrameInfo("HandlerFrame", "core_handler", 1),
            ),
            effect_call_tree=None,
            current_effect=None,
        )

        output = ctx.format_k_frames()

        assert "K Frame Stack:" in output
        assert "[0] KleisliFrame(test_func)" in output
        assert "[1] HandlerFrame(core_handler)" in output

    def test_format_all_sections(self) -> None:
        ctx = DebugContext(
            kleisli_stack=(
                KleisliStackEntry("main", "main.py", 5, 0),
            ),
            k_frames=(
                KFrameInfo("KleisliFrame", "main", 0),
            ),
            effect_call_tree=None,
            current_effect="TestEffect",
        )

        output = ctx.format_all()

        assert "Current Effect: TestEffect" in output
        assert "Kleisli Call Stack:" in output
        assert "K Frame Stack:" in output


class TestErrorMessagesIncludeStacks:
    def test_error_captures_kleisli_stack(self) -> None:
        @do
        def failing_inner():
            raise ValueError("test error")

        @do
        def outer():
            return (yield failing_inner())

        result = sync_run(outer(), sync_handlers_preset)

        assert result.is_err()
        assert isinstance(result.error, ValueError)
        assert str(result.error) == "test error"

    def test_error_traceback_shows_effect_chain(self) -> None:
        @do
        def level_2():
            raise RuntimeError("deep error")

        @do
        def level_1():
            return (yield level_2())

        result = sync_run(level_1(), sync_handlers_preset)

        assert result.is_err()
        assert isinstance(result.error, RuntimeError)


class TestDoeffDebugEnvVar:
    def test_doeff_debug_enables_verbose_output(self) -> None:
        from doeff.cesk_traceback import (
            CapturedTraceback,
            KFrameSnapshot,
            KleisliStackFrame,
            format_traceback,
        )

        tb = CapturedTraceback(
            effect_frames=(),
            python_frames=(),
            exception_type="TestError",
            exception_message="test",
            exception_args=("test",),
            exception=Exception("test"),
            kleisli_stack=(
                KleisliStackFrame("func_a", "a.py", 10),
            ),
            k_frame_snapshot=(
                KFrameSnapshot("KleisliFrame", "func_a"),
            ),
        )

        old_env = os.environ.get("DOEFF_DEBUG")
        try:
            os.environ["DOEFF_DEBUG"] = "1"
            output = format_traceback(tb)

            assert "Kleisli Call Stack:" in output
            assert "K Frame Stack:" in output
            assert "func_a" in output
        finally:
            if old_env is None:
                os.environ.pop("DOEFF_DEBUG", None)
            else:
                os.environ["DOEFF_DEBUG"] = old_env

    def test_without_doeff_debug_minimal_output(self) -> None:
        from doeff.cesk_traceback import (
            CapturedTraceback,
            KFrameSnapshot,
            KleisliStackFrame,
            format_traceback,
        )

        tb = CapturedTraceback(
            effect_frames=(),
            python_frames=(),
            exception_type="TestError",
            exception_message="test",
            exception_args=("test",),
            exception=Exception("test"),
            kleisli_stack=(
                KleisliStackFrame("func_a", "a.py", 10),
            ),
            k_frame_snapshot=(
                KFrameSnapshot("KleisliFrame", "func_a"),
            ),
        )

        old_env = os.environ.get("DOEFF_DEBUG")
        try:
            os.environ.pop("DOEFF_DEBUG", None)
            output = format_traceback(tb)

            assert "Kleisli Call Stack:" not in output
            assert "K Frame Stack:" not in output
        finally:
            if old_env is not None:
                os.environ["DOEFF_DEBUG"] = old_env


class TestMinimalOverhead:
    def test_debug_context_not_captured_without_effect(self) -> None:
        @do
        def simple_program():
            yield Put("counter", 0)
            value = yield Get("counter")
            return value + 1

        result = sync_run(simple_program(), sync_handlers_preset)

        assert result.value == 1


class TestIntegrationWithContentAssertions:
    def test_nested_chain_returns_exact_function_names_and_order(self) -> None:
        @do
        def deepest():
            ctx = yield GetDebugContext()
            return ctx

        @do
        def middle():
            return (yield deepest())

        @do
        def outermost():
            return (yield middle())

        result = sync_run(outermost(), sync_handlers_preset)

        assert isinstance(result.value, DebugContext)
        ctx = result.value
        k_frame_types = [f.frame_type for f in ctx.k_frames]
        assert "ReturnFrame" in k_frame_types
        assert "HandlerFrame" in k_frame_types
        return_frames = [f for f in ctx.k_frames if f.frame_type == "ReturnFrame"]
        assert len(return_frames) >= 3
        func_names = [f.description for f in return_frames]
        assert any("outermost" in name for name in func_names)
        assert any("middle" in name for name in func_names)
        assert any("deepest" in name for name in func_names)

    def test_get_debug_context_persists_across_multiple_yields(self) -> None:
        collected_contexts: list[DebugContext] = []

        @do
        def multi_yield_func():
            yield Put("x", 1)
            ctx1 = yield GetDebugContext()
            collected_contexts.append(ctx1)
            yield Put("y", 2)
            ctx2 = yield GetDebugContext()
            collected_contexts.append(ctx2)
            return "done"

        result = sync_run(multi_yield_func(), sync_handlers_preset)

        assert result.value == "done"
        assert len(collected_contexts) == 2
        for ctx in collected_contexts:
            return_frames = [f for f in ctx.k_frames if f.frame_type == "ReturnFrame"]
            assert len(return_frames) >= 1
            assert any("multi_yield_func" in f.description for f in return_frames)

    def test_error_path_captures_traceback_with_kleisli_stack(self) -> None:
        @do
        def inner_fail():
            raise ValueError("intentional failure")

        @do
        def outer_wrapper():
            return (yield inner_fail())

        result = sync_run(outer_wrapper(), sync_handlers_preset)

        assert result.is_err()
        assert isinstance(result.error, ValueError)
        captured_tb = getattr(result, "captured_traceback", None)
        if captured_tb is not None:
            assert hasattr(captured_tb, "kleisli_stack")
            kleisli_stack = captured_tb.kleisli_stack
            if kleisli_stack:
                func_names = [ks.function_name for ks in kleisli_stack]
                assert len(func_names) >= 1

    def test_extract_k_frame_info_handles_handler_frames(self) -> None:
        from doeff.cesk.debug import extract_k_frame_info
        from doeff.cesk.handler_frame import HandlerFrame

        def dummy_handler(effect, ctx):
            yield effect
            return None

        dummy_k: Kontinuation = [
            HandlerFrame(handler=dummy_handler, saved_env={}),
        ]
        infos = extract_k_frame_info(dummy_k)

        assert len(infos) == 1
        assert infos[0].frame_type == "HandlerFrame"
        assert "handler=dummy_handler" in infos[0].description

    def test_extract_k_frame_info_handles_return_frames(self) -> None:
        from doeff.cesk.debug import extract_k_frame_info
        from doeff.cesk.frames import ReturnFrame

        def gen():
            yield
            return

        class MockProgramCall:
            function_name = "test_func"

        dummy_k: Kontinuation = [
            ReturnFrame(gen(), {}, program_call=MockProgramCall()),
        ]
        infos = extract_k_frame_info(dummy_k)

        assert len(infos) == 1
        assert infos[0].frame_type == "ReturnFrame"
        assert "continuation=test_func" in infos[0].description
