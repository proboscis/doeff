"""Tests for CESK kontinuation module."""


from doeff._vendor import FrozenDict
from doeff.cesk.frames import (
    ContinueError,
    ContinueValue,
    InterceptFrame,
    Kontinuation,
    LocalFrame,
    SafeFrame,
)
from doeff.cesk.kontinuation import (
    apply_intercept_chain,
    continuation_depth,
    find_frame,
    find_intercept_frame_index,
    find_safe_frame_index,
    get_intercept_transforms,
    has_frame,
    has_intercept_frame,
    has_safe_frame,
    pop_frame,
    push_frame,
    split_at_safe,
    unwind_error,
    unwind_value,
)
from doeff.cesk.types import Environment, Store
from doeff.program import Program


class TestStackOperations:
    """Tests for stack operations."""

    def test_push_frame(self) -> None:
        """push_frame adds frame to front of continuation."""
        env: Environment = FrozenDict()
        k: Kontinuation = [LocalFrame(restore_env=env)]

        new_frame = SafeFrame(saved_env=env)
        new_k = push_frame(k, new_frame)

        assert len(new_k) == 2
        assert new_k[0] is new_frame
        assert new_k[1] is k[0]

    def test_push_frame_empty(self) -> None:
        """push_frame works with empty continuation."""
        env: Environment = FrozenDict()
        k: Kontinuation = []

        new_frame = LocalFrame(restore_env=env)
        new_k = push_frame(k, new_frame)

        assert len(new_k) == 1
        assert new_k[0] is new_frame

    def test_pop_frame(self) -> None:
        """pop_frame removes and returns first frame."""
        env: Environment = FrozenDict()
        frame1 = LocalFrame(restore_env=env)
        frame2 = SafeFrame(saved_env=env)
        k: Kontinuation = [frame1, frame2]

        popped, rest = pop_frame(k)

        assert popped is frame1
        assert len(rest) == 1
        assert rest[0] is frame2

    def test_pop_frame_empty(self) -> None:
        """pop_frame returns None for empty continuation."""
        k: Kontinuation = []

        popped, rest = pop_frame(k)

        assert popped is None
        assert rest == []


class TestUnwinding:
    """Tests for unwinding operations."""

    def test_unwind_value_empty_k(self) -> None:
        """unwind_value with empty k returns ContinueValue."""
        env: Environment = FrozenDict()
        store: Store = {}
        k: Kontinuation = []

        result = unwind_value(42, env, store, k)

        assert isinstance(result, ContinueValue)
        assert result.value == 42
        assert result.k == []

    def test_unwind_value_with_frame(self) -> None:
        """unwind_value processes through frame's on_value."""
        original_env: Environment = FrozenDict({"original": True})
        current_env: Environment = FrozenDict({"current": True})
        store: Store = {}

        frame = LocalFrame(restore_env=original_env)
        k: Kontinuation = [frame]

        result = unwind_value(42, current_env, store, k)

        assert isinstance(result, ContinueValue)
        assert result.value == 42
        assert result.env == original_env  # Restored by LocalFrame

    def test_unwind_error_empty_k(self) -> None:
        """unwind_error with empty k returns ContinueError."""
        env: Environment = FrozenDict()
        store: Store = {}
        k: Kontinuation = []
        error = ValueError("test")

        result = unwind_error(error, env, store, k)

        assert isinstance(result, ContinueError)
        assert result.error is error
        assert result.k == []

    def test_unwind_error_with_safe_frame(self) -> None:
        """unwind_error with SafeFrame converts to ContinueValue(Err)."""
        env: Environment = FrozenDict()
        store: Store = {}
        error = ValueError("test")

        frame = SafeFrame(saved_env=env)
        k: Kontinuation = [frame]

        result = unwind_error(error, env, store, k)

        # SafeFrame converts errors to Err results
        assert isinstance(result, ContinueValue)
        assert result.value.is_err()
        assert result.value.error is error


class TestFrameFinding:
    """Tests for frame finding operations."""

    def test_find_frame_found(self) -> None:
        """find_frame returns index and frame when found."""
        env: Environment = FrozenDict()
        local = LocalFrame(restore_env=env)
        safe = SafeFrame(saved_env=env)
        k: Kontinuation = [local, safe]

        idx, frame = find_frame(k, SafeFrame)

        assert idx == 1
        assert frame is safe

    def test_find_frame_not_found(self) -> None:
        """find_frame returns (-1, None) when not found."""
        env: Environment = FrozenDict()
        local = LocalFrame(restore_env=env)
        k: Kontinuation = [local]

        idx, frame = find_frame(k, SafeFrame)

        assert idx == -1
        assert frame is None

    def test_has_frame_true(self) -> None:
        """has_frame returns True when frame type exists."""
        env: Environment = FrozenDict()
        k: Kontinuation = [LocalFrame(restore_env=env)]

        assert has_frame(k, LocalFrame)

    def test_has_frame_false(self) -> None:
        """has_frame returns False when frame type not found."""
        env: Environment = FrozenDict()
        k: Kontinuation = [LocalFrame(restore_env=env)]

        assert not has_frame(k, SafeFrame)


class TestInterceptHelpers:
    """Tests for intercept-related helpers."""

    def test_find_intercept_frame_index(self) -> None:
        """find_intercept_frame_index finds InterceptFrame."""
        env: Environment = FrozenDict()
        k: Kontinuation = [
            LocalFrame(restore_env=env),
            InterceptFrame(transforms=()),
        ]

        idx = find_intercept_frame_index(k)

        assert idx == 1

    def test_find_intercept_frame_index_not_found(self) -> None:
        """find_intercept_frame_index returns -1 when not found."""
        env: Environment = FrozenDict()
        k: Kontinuation = [LocalFrame(restore_env=env)]

        idx = find_intercept_frame_index(k)

        assert idx == -1

    def test_has_intercept_frame(self) -> None:
        """has_intercept_frame works correctly."""
        env: Environment = FrozenDict()

        k_with = [InterceptFrame(transforms=())]
        k_without = [LocalFrame(restore_env=env)]

        assert has_intercept_frame(k_with)
        assert not has_intercept_frame(k_without)

    def test_get_intercept_transforms(self) -> None:
        """get_intercept_transforms collects all transforms."""
        env: Environment = FrozenDict()

        def transform1(e):
            return e

        def transform2(e):
            return e

        k: Kontinuation = [
            InterceptFrame(transforms=(transform1,)),
            LocalFrame(restore_env=env),
            InterceptFrame(transforms=(transform2,)),
        ]

        transforms = get_intercept_transforms(k)

        assert len(transforms) == 2
        assert transforms[0] is transform1
        assert transforms[1] is transform2

    def test_apply_intercept_chain_no_transforms(self) -> None:
        """apply_intercept_chain returns effect unchanged with no transforms."""
        from doeff.effects import PureEffect

        effect = PureEffect(value=42)
        k: Kontinuation = []

        result = apply_intercept_chain(k, effect)

        assert result is effect

    def test_apply_intercept_chain_with_transform(self) -> None:
        """apply_intercept_chain applies transforms."""
        from doeff.effects import PureEffect

        effect = PureEffect(value=42)

        def double_transform(e):
            if isinstance(e, PureEffect):
                return PureEffect(value=e.value * 2)
            return e

        k: Kontinuation = [InterceptFrame(transforms=(double_transform,))]

        result = apply_intercept_chain(k, effect)

        assert isinstance(result, PureEffect)
        assert result.value == 84

    def test_apply_intercept_chain_transform_returns_program(self) -> None:
        """apply_intercept_chain handles transform returning Program."""
        from doeff.effects import PureEffect

        effect = PureEffect(value=42)
        replacement = Program.pure(100)

        def program_transform(e):
            return replacement

        k: Kontinuation = [InterceptFrame(transforms=(program_transform,))]

        result = apply_intercept_chain(k, effect)

        assert result is replacement


class TestSafeHelpers:
    """Tests for safe-frame-related helpers."""

    def test_find_safe_frame_index(self) -> None:
        """find_safe_frame_index finds SafeFrame."""
        env: Environment = FrozenDict()
        k: Kontinuation = [
            LocalFrame(restore_env=env),
            SafeFrame(saved_env=env),
        ]

        idx = find_safe_frame_index(k)

        assert idx == 1

    def test_has_safe_frame(self) -> None:
        """has_safe_frame works correctly."""
        env: Environment = FrozenDict()

        k_with = [SafeFrame(saved_env=env)]
        k_without = [LocalFrame(restore_env=env)]

        assert has_safe_frame(k_with)
        assert not has_safe_frame(k_without)

    def test_split_at_safe(self) -> None:
        """split_at_safe splits at SafeFrame."""
        env: Environment = FrozenDict()
        local1 = LocalFrame(restore_env=env)
        safe = SafeFrame(saved_env=env)
        local2 = LocalFrame(restore_env=env)

        k: Kontinuation = [local1, safe, local2]

        before, after = split_at_safe(k)

        assert len(before) == 2
        assert before[0] is local1
        assert before[1] is safe
        assert len(after) == 1
        assert after[0] is local2

    def test_split_at_safe_no_safe_frame(self) -> None:
        """split_at_safe returns full k when no SafeFrame."""
        env: Environment = FrozenDict()
        local = LocalFrame(restore_env=env)
        k: Kontinuation = [local]

        before, after = split_at_safe(k)

        assert before == k
        assert after == []


class TestUtilities:
    """Tests for utility functions."""

    def test_continuation_depth(self) -> None:
        """continuation_depth returns number of frames."""
        env: Environment = FrozenDict()

        k0: Kontinuation = []
        k1: Kontinuation = [LocalFrame(restore_env=env)]
        k2: Kontinuation = [LocalFrame(restore_env=env), SafeFrame(saved_env=env)]

        assert continuation_depth(k0) == 0
        assert continuation_depth(k1) == 1
        assert continuation_depth(k2) == 2
