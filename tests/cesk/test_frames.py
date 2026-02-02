"""Tests for CESK frames module.

Per SPEC-CESK-003: Tests for deprecated Frame types (LocalFrame, SafeFrame,
ListenFrame, InterceptFrame) have been removed. These patterns are now
implemented in user-space via WithHandler in doeff.cesk.handlers.patterns.
"""

from collections.abc import Generator

from doeff._vendor import FrozenDict
from doeff.cesk.frames import (
    Frame,
    GatherFrame,
    Kontinuation,
    RaceFrame,
    ReturnFrame,
)
from doeff.cesk.state import CESKState, Error, ProgramControl, Value
from doeff.cesk.types import Environment, Store, TaskId
from doeff.program import Program


class TestCESKStateHelpers:
    """Tests for CESKState helper class methods."""

    def test_with_value(self) -> None:
        """CESKState.with_value creates state with Value control."""
        env: Environment = FrozenDict({"key": "value"})
        store: Store = {"state": 1}
        k: Kontinuation = []

        state = CESKState.with_value(42, env, store, k)

        assert isinstance(state.C, Value)
        assert state.C.v == 42
        assert state.E == env
        assert state.S == store
        assert state.K == k

    def test_with_error(self) -> None:
        """CESKState.with_error creates state with Error control."""
        env: Environment = FrozenDict()
        store: Store = {}
        k: Kontinuation = []
        error = ValueError("test error")

        state = CESKState.with_error(error, env, store, k)

        assert isinstance(state.C, Error)
        assert state.C.ex is error
        assert state.E == env

    def test_with_program(self) -> None:
        """CESKState.with_program creates state with ProgramControl."""
        env: Environment = FrozenDict()
        store: Store = {}
        k: Kontinuation = []
        program = Program.pure(42)

        state = CESKState.with_program(program, env, store, k)

        assert isinstance(state.C, ProgramControl)
        assert state.C.program is program


class TestGatherFrame:
    """Tests for GatherFrame."""

    def test_on_value_collects_and_continues(self) -> None:
        """on_value collects result and continues with next program."""
        env: Environment = FrozenDict()
        store: Store = {}
        k: Kontinuation = []

        program2 = Program.pure(2)
        program3 = Program.pure(3)

        frame = GatherFrame(
            remaining_programs=[program2, program3],
            collected_results=[],
            saved_env=env,
        )

        result = frame.on_value(1, env, store, k)

        assert isinstance(result, CESKState)
        assert isinstance(result.C, ProgramControl)
        assert result.C.program is program2
        # New frame should have one less program and one more result
        new_frame = result.K[0]
        assert isinstance(new_frame, GatherFrame)
        assert len(new_frame.remaining_programs) == 1
        assert new_frame.collected_results == [1]

    def test_on_value_returns_all_when_done(self) -> None:
        """on_value returns all results when no programs remain."""
        env: Environment = FrozenDict()
        store: Store = {}
        k: Kontinuation = []

        frame = GatherFrame(
            remaining_programs=[],
            collected_results=[1, 2],
            saved_env=env,
        )

        result = frame.on_value(3, env, store, k)

        assert isinstance(result, CESKState)
        assert isinstance(result.C, Value)
        assert result.C.v == [1, 2, 3]

    def test_on_error_aborts(self) -> None:
        """on_error aborts the gather."""
        env: Environment = FrozenDict()
        store: Store = {}
        k: Kontinuation = []
        error = ValueError("test")

        frame = GatherFrame(
            remaining_programs=[Program.pure(2)],
            collected_results=[1],
            saved_env=env,
        )

        result = frame.on_error(error, env, store, k)

        assert isinstance(result, CESKState)
        assert isinstance(result.C, Error)
        assert result.C.ex is error


class TestRaceFrame:
    """Tests for RaceFrame."""

    def test_on_value_wins_race(self) -> None:
        """on_value returns the winning value."""
        env: Environment = FrozenDict()
        store: Store = {}
        k: Kontinuation = []
        task_ids = (TaskId.new(), TaskId.new())

        frame = RaceFrame(task_ids=task_ids, saved_env=env)
        result = frame.on_value(42, env, store, k)

        assert isinstance(result, CESKState)
        assert isinstance(result.C, Value)
        assert result.C.v == 42

    def test_on_error_propagates(self) -> None:
        """on_error propagates the error."""
        env: Environment = FrozenDict()
        store: Store = {}
        k: Kontinuation = []
        task_ids = (TaskId.new(),)
        error = ValueError("race failed")

        frame = RaceFrame(task_ids=task_ids, saved_env=env)
        result = frame.on_error(error, env, store, k)

        assert isinstance(result, CESKState)
        assert isinstance(result.C, Error)
        assert result.C.ex is error


class TestReturnFrame:
    """Tests for ReturnFrame.

    Note: ReturnFrame doesn't implement on_value/on_error as step.py handles
    ReturnFrame directly by calling generator.send/throw.
    """

    def test_return_frame_holds_generator(self) -> None:
        """ReturnFrame holds generator and saved environment."""

        def gen() -> Generator[int, int, int]:
            x = yield 1
            return x

        g = gen()
        next(g)  # Start generator

        env: Environment = FrozenDict()

        frame = ReturnFrame(generator=g, saved_env=env)

        assert frame.generator is g
        assert frame.saved_env == env


class TestFrameProtocol:
    """Tests that frame types implement the Frame protocol."""

    def test_gather_frame_is_frame(self) -> None:
        """GatherFrame implements Frame protocol."""
        frame = GatherFrame(
            remaining_programs=[],
            collected_results=[],
            saved_env=FrozenDict(),
        )
        assert isinstance(frame, Frame)

    def test_race_frame_is_frame(self) -> None:
        """RaceFrame implements Frame protocol."""
        frame = RaceFrame(task_ids=(), saved_env=FrozenDict())
        assert isinstance(frame, Frame)

    def test_return_frame_structure(self) -> None:
        """ReturnFrame is a data holder (not a Frame protocol implementer).

        Note: ReturnFrame doesn't implement the Frame protocol because step.py
        handles it directly without calling on_value/on_error.
        """

        def gen() -> Generator[int, int, int]:
            x = yield 1
            return x

        g = gen()
        frame = ReturnFrame(generator=g, saved_env=FrozenDict())
        # ReturnFrame is a dataclass, not a Frame protocol implementer
        assert frame.generator is g
        assert frame.saved_env == FrozenDict()
