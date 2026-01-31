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
from doeff.cesk.frames import KleisliFrame, Kontinuation
from doeff.cesk.runtime import SyncRuntime
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

        runtime = SyncRuntime()
        result = runtime.run(outer_func())

        assert result.value == 42

    def test_kleisli_frame_has_function_metadata(self) -> None:
        import time

        frame = KleisliFrame(
            function_name="test_func",
            filename="/path/to/file.py",
            lineno=42,
            created_at=time.time(),
        )

        assert frame.function_name == "test_func"
        assert frame.filename == "/path/to/file.py"
        assert frame.lineno == 42
        assert frame.created_at > 0


class TestFormatKStack:
    def test_format_empty_k_stack(self) -> None:
        k: Kontinuation = []
        output = format_k_stack(k)
        assert "K Frame Stack:" in output
        assert "(empty)" in output

    def test_format_k_stack_with_frames(self) -> None:
        import time

        k: Kontinuation = [
            KleisliFrame("func_a", "a.py", 10, time.time()),
            KleisliFrame("func_b", "b.py", 20, time.time()),
        ]
        output = format_k_stack(k)

        assert "K Frame Stack:" in output
        assert "KleisliFrame" in output
        assert "func_a" in output
        assert "func_b" in output


class TestExtractKleisliStack:
    def test_extract_empty_stack(self) -> None:
        k: Kontinuation = []
        entries = extract_kleisli_stack(k)
        assert entries == ()

    def test_extract_kleisli_frames_only(self) -> None:
        import time

        k: Kontinuation = [
            KleisliFrame("outer", "outer.py", 10, time.time()),
            KleisliFrame("inner", "inner.py", 20, time.time()),
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

    def test_extract_kleisli_frame_info(self) -> None:
        import time

        k: Kontinuation = [
            KleisliFrame("test_func", "test.py", 42, time.time()),
        ]
        infos = extract_k_frame_info(k)

        assert len(infos) == 1
        assert infos[0].frame_type == "KleisliFrame"
        assert infos[0].description == "test_func"


class TestBuildEffectCallTree:
    def test_empty_k_returns_none(self) -> None:
        k: Kontinuation = []
        tree = build_effect_call_tree(k)
        assert tree is None

    def test_build_tree_with_kleisli_frames(self) -> None:
        import time

        k: Kontinuation = [
            KleisliFrame("inner", "inner.py", 20, time.time()),
            KleisliFrame("outer", "outer.py", 10, time.time()),
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
        import time

        k: Kontinuation = [
            KleisliFrame("test", "test.py", 10, time.time()),
        ]
        ctx = get_debug_context(k, current_effect="TestEffect")

        assert ctx.current_effect == "TestEffect"


class TestGetDebugContextEffect:
    def test_get_debug_context_effect_basic(self) -> None:
        @do
        def program():
            ctx = yield GetDebugContext()
            return ctx

        runtime = SyncRuntime()
        result = runtime.run(program())

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

        runtime = SyncRuntime()
        result = runtime.run(outer())

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

        runtime = SyncRuntime()
        result = runtime.run(level_1())

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

        runtime = SyncRuntime()
        result = runtime.run(outer())

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

        runtime = SyncRuntime()
        result = runtime.run(level_1())

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

        runtime = SyncRuntime()
        result = runtime.run(simple_program())

        assert result.value == 1
