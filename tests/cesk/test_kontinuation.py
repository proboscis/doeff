"""Tests for CESK kontinuation module.

Per SPEC-CESK-003: Tests for deprecated Frame types (LocalFrame, SafeFrame,
InterceptFrame) have been updated. The intercept/safe helper functions now
return trivial values for backwards compatibility.
"""

from doeff._vendor import FrozenDict
from doeff.cesk.frames import (
    GatherFrame,
    Kontinuation,
    RaceFrame,
)
from doeff.cesk.state import CESKState, Error, Value
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
from doeff.cesk.types import Environment, Store, TaskId
from doeff.program import Program


class TestStackOperations:
    """Tests for stack operations."""

    def test_push_frame(self) -> None:
        """push_frame adds frame to front of continuation."""
        env: Environment = FrozenDict()
        k: Kontinuation = [GatherFrame(remaining_programs=[], collected_results=[], saved_env=env)]

        new_frame = RaceFrame(task_ids=(), saved_env=env)
        new_k = push_frame(k, new_frame)

        assert len(new_k) == 2
        assert new_k[0] is new_frame
        assert new_k[1] is k[0]

    def test_push_frame_empty(self) -> None:
        """push_frame works with empty continuation."""
        env: Environment = FrozenDict()
        k: Kontinuation = []

        new_frame = GatherFrame(remaining_programs=[], collected_results=[], saved_env=env)
        new_k = push_frame(k, new_frame)

        assert len(new_k) == 1
        assert new_k[0] is new_frame

    def test_pop_frame(self) -> None:
        """pop_frame removes and returns first frame."""
        env: Environment = FrozenDict()
        frame1 = GatherFrame(remaining_programs=[], collected_results=[], saved_env=env)
        frame2 = RaceFrame(task_ids=(), saved_env=env)
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
        """unwind_value with empty k returns CESKState with value."""
        env: Environment = FrozenDict()
        store: Store = {}
        k: Kontinuation = []

        result = unwind_value(42, env, store, k)

        assert isinstance(result, CESKState)
        assert isinstance(result.C, Value)
        assert result.C.v == 42
        assert result.K == []

    def test_unwind_value_with_frame(self) -> None:
        """unwind_value processes through frame's on_value."""
        env: Environment = FrozenDict()
        store: Store = {}

        # GatherFrame with no remaining programs returns collected results
        frame = GatherFrame(remaining_programs=[], collected_results=[1, 2], saved_env=env)
        k: Kontinuation = [frame]

        result = unwind_value(3, env, store, k)

        assert isinstance(result, CESKState)
        assert isinstance(result.C, Value)
        assert result.C.v == [1, 2, 3]

    def test_unwind_error_empty_k(self) -> None:
        """unwind_error with empty k returns CESKState with error."""
        env: Environment = FrozenDict()
        store: Store = {}
        k: Kontinuation = []
        error = ValueError("test")

        result = unwind_error(error, env, store, k)

        assert isinstance(result, CESKState)
        assert isinstance(result.C, Error)
        assert result.C.ex is error
        assert result.K == []


class TestFrameFinding:
    """Tests for frame finding operations."""

    def test_find_frame_found(self) -> None:
        """find_frame returns index and frame when found."""
        env: Environment = FrozenDict()
        gather = GatherFrame(remaining_programs=[], collected_results=[], saved_env=env)
        race = RaceFrame(task_ids=(), saved_env=env)
        k: Kontinuation = [gather, race]

        idx, frame = find_frame(k, RaceFrame)

        assert idx == 1
        assert frame is race

    def test_find_frame_not_found(self) -> None:
        """find_frame returns (-1, None) when not found."""
        env: Environment = FrozenDict()
        gather = GatherFrame(remaining_programs=[], collected_results=[], saved_env=env)
        k: Kontinuation = [gather]

        idx, frame = find_frame(k, RaceFrame)

        assert idx == -1
        assert frame is None

    def test_has_frame_true(self) -> None:
        """has_frame returns True when frame type exists."""
        env: Environment = FrozenDict()
        k: Kontinuation = [GatherFrame(remaining_programs=[], collected_results=[], saved_env=env)]

        assert has_frame(k, GatherFrame)

    def test_has_frame_false(self) -> None:
        """has_frame returns False when frame type not found."""
        env: Environment = FrozenDict()
        k: Kontinuation = [GatherFrame(remaining_programs=[], collected_results=[], saved_env=env)]

        assert not has_frame(k, RaceFrame)


class TestDeprecatedInterceptHelpers:
    """Tests for deprecated intercept-related helpers.

    Per SPEC-CESK-003: InterceptFrame has been removed. These functions
    now return trivial values for backwards compatibility.
    """

    def test_find_intercept_frame_index_always_returns_minus_one(self) -> None:
        """find_intercept_frame_index always returns -1 (deprecated)."""
        env: Environment = FrozenDict()
        k: Kontinuation = [GatherFrame(remaining_programs=[], collected_results=[], saved_env=env)]

        idx = find_intercept_frame_index(k)

        assert idx == -1

    def test_has_intercept_frame_always_returns_false(self) -> None:
        """has_intercept_frame always returns False (deprecated)."""
        env: Environment = FrozenDict()
        k: Kontinuation = [GatherFrame(remaining_programs=[], collected_results=[], saved_env=env)]

        assert not has_intercept_frame(k)

    def test_get_intercept_transforms_always_returns_empty(self) -> None:
        """get_intercept_transforms always returns empty list (deprecated)."""
        env: Environment = FrozenDict()
        k: Kontinuation = [GatherFrame(remaining_programs=[], collected_results=[], saved_env=env)]

        transforms = get_intercept_transforms(k)

        assert transforms == []

    def test_apply_intercept_chain_returns_effect_unchanged(self) -> None:
        """apply_intercept_chain returns effect unchanged (deprecated)."""
        from doeff.effects import PureEffect

        effect = PureEffect(value=42)
        k: Kontinuation = []

        result = apply_intercept_chain(k, effect)

        assert result is effect


class TestDeprecatedSafeHelpers:
    """Tests for deprecated safe-frame-related helpers.

    Per SPEC-CESK-003: SafeFrame has been removed. These functions
    now return trivial values for backwards compatibility.
    """

    def test_find_safe_frame_index_always_returns_minus_one(self) -> None:
        """find_safe_frame_index always returns -1 (deprecated)."""
        env: Environment = FrozenDict()
        k: Kontinuation = [GatherFrame(remaining_programs=[], collected_results=[], saved_env=env)]

        idx = find_safe_frame_index(k)

        assert idx == -1

    def test_has_safe_frame_always_returns_false(self) -> None:
        """has_safe_frame always returns False (deprecated)."""
        env: Environment = FrozenDict()
        k: Kontinuation = [GatherFrame(remaining_programs=[], collected_results=[], saved_env=env)]

        assert not has_safe_frame(k)

    def test_split_at_safe_returns_full_k(self) -> None:
        """split_at_safe returns full k when no SafeFrame (always now)."""
        env: Environment = FrozenDict()
        gather = GatherFrame(remaining_programs=[], collected_results=[], saved_env=env)
        k: Kontinuation = [gather]

        before, after = split_at_safe(k)

        assert before == k
        assert after == []


class TestUtilities:
    """Tests for utility functions."""

    def test_continuation_depth(self) -> None:
        """continuation_depth returns number of frames."""
        env: Environment = FrozenDict()

        k0: Kontinuation = []
        k1: Kontinuation = [GatherFrame(remaining_programs=[], collected_results=[], saved_env=env)]
        k2: Kontinuation = [
            GatherFrame(remaining_programs=[], collected_results=[], saved_env=env),
            RaceFrame(task_ids=(), saved_env=env),
        ]

        assert continuation_depth(k0) == 0
        assert continuation_depth(k1) == 1
        assert continuation_depth(k2) == 2
