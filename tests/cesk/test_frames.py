"""Tests for CESK frames module."""

from collections.abc import Generator

from doeff._vendor import Err, FrozenDict, Ok
from doeff.cesk.frames import (
    ContinueError,
    ContinueGenerator,
    ContinueProgram,
    ContinueValue,
    Frame,
    GatherFrame,
    InterceptFrame,
    Kontinuation,
    ListenFrame,
    LocalFrame,
    RaceFrame,
    ReturnFrame,
    SafeFrame,
)
from doeff.cesk.types import Environment, Store, TaskId
from doeff.program import Program


class TestFrameResult:
    """Tests for FrameResult types."""

    def test_continue_value(self) -> None:
        """ContinueValue holds value and continuation state."""
        env: Environment = FrozenDict({"key": "value"})
        store: Store = {"state": 1}
        k: Kontinuation = []

        result = ContinueValue(value=42, env=env, store=store, k=k)

        assert result.value == 42
        assert result.env == env
        assert result.store == store
        assert result.k == k

    def test_continue_error(self) -> None:
        """ContinueError holds error and continuation state."""
        env: Environment = FrozenDict()
        store: Store = {}
        k: Kontinuation = []
        error = ValueError("test error")

        result = ContinueError(error=error, env=env, store=store, k=k)

        assert result.error is error
        assert result.env == env

    def test_continue_program(self) -> None:
        """ContinueProgram holds program and continuation state."""
        env: Environment = FrozenDict()
        store: Store = {}
        k: Kontinuation = []
        program = Program.pure(42)

        result = ContinueProgram(program=program, env=env, store=store, k=k)

        assert result.program is program

    def test_continue_generator(self) -> None:
        """ContinueGenerator holds generator and resume state."""

        def gen() -> Generator[int, int, int]:
            x = yield 1
            return x

        g = gen()
        next(g)  # Start generator

        env: Environment = FrozenDict()
        store: Store = {}
        k: Kontinuation = []

        result = ContinueGenerator(
            generator=g,
            send_value=42,
            throw_error=None,
            env=env,
            store=store,
            k=k,
        )

        assert result.generator is g
        assert result.send_value == 42
        assert result.throw_error is None


class TestLocalFrame:
    """Tests for LocalFrame."""

    def test_on_value_restores_env(self) -> None:
        """on_value restores original environment."""
        original_env: Environment = FrozenDict({"original": "env"})
        current_env: Environment = FrozenDict({"current": "env"})
        store: Store = {}
        k: Kontinuation = []

        frame = LocalFrame(restore_env=original_env)
        result = frame.on_value(42, current_env, store, k)

        assert isinstance(result, ContinueValue)
        assert result.value == 42
        assert result.env == original_env

    def test_on_error_restores_env(self) -> None:
        """on_error restores original environment."""
        original_env: Environment = FrozenDict({"original": "env"})
        current_env: Environment = FrozenDict({"current": "env"})
        store: Store = {}
        k: Kontinuation = []
        error = ValueError("test")

        frame = LocalFrame(restore_env=original_env)
        result = frame.on_error(error, current_env, store, k)

        assert isinstance(result, ContinueError)
        assert result.error is error
        assert result.env == original_env


class TestInterceptFrame:
    """Tests for InterceptFrame."""

    def test_on_value_passes_through(self) -> None:
        """Values pass through InterceptFrame unchanged."""
        env: Environment = FrozenDict()
        store: Store = {}
        k: Kontinuation = []

        frame = InterceptFrame(transforms=())
        result = frame.on_value(42, env, store, k)

        assert isinstance(result, ContinueValue)
        assert result.value == 42

    def test_on_error_passes_through(self) -> None:
        """Errors pass through InterceptFrame unchanged."""
        env: Environment = FrozenDict()
        store: Store = {}
        k: Kontinuation = []
        error = ValueError("test")

        frame = InterceptFrame(transforms=())
        result = frame.on_error(error, env, store, k)

        assert isinstance(result, ContinueError)
        assert result.error is error


class TestListenFrame:
    """Tests for ListenFrame."""

    def test_on_value_captures_log(self) -> None:
        """on_value captures log entries from sub-computation."""
        env: Environment = FrozenDict()
        store: Store = {"__log__": ["entry1", "entry2", "entry3"]}
        k: Kontinuation = []

        frame = ListenFrame(log_start_index=1)  # Capture from index 1
        result = frame.on_value(42, env, store, k)

        assert isinstance(result, ContinueValue)
        # Result should be ListenResult with value and captured log
        listen_result = result.value
        assert listen_result.value == 42
        assert list(listen_result.log) == ["entry2", "entry3"]

    def test_on_error_passes_through(self) -> None:
        """Errors pass through ListenFrame unchanged."""
        env: Environment = FrozenDict()
        store: Store = {"__log__": ["entry1", "entry2"]}
        k: Kontinuation = []
        error = ValueError("test")

        frame = ListenFrame(log_start_index=0)
        result = frame.on_error(error, env, store, k)

        assert isinstance(result, ContinueError)
        assert result.error is error


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

        assert isinstance(result, ContinueProgram)
        assert result.program is program2
        # New frame should have one less program and one more result
        new_frame = result.k[0]
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

        assert isinstance(result, ContinueValue)
        assert result.value == [1, 2, 3]

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

        assert isinstance(result, ContinueError)
        assert result.error is error


class TestSafeFrame:
    """Tests for SafeFrame."""

    def test_on_value_wraps_in_ok(self) -> None:
        """on_value wraps successful value in Ok."""
        env: Environment = FrozenDict()
        store: Store = {}
        k: Kontinuation = []

        frame = SafeFrame(saved_env=env)
        result = frame.on_value(42, env, store, k)

        assert isinstance(result, ContinueValue)
        assert isinstance(result.value, Ok)
        assert result.value.value == 42

    def test_on_error_wraps_in_err(self) -> None:
        """on_error converts error to Err result."""
        env: Environment = FrozenDict()
        store: Store = {}
        k: Kontinuation = []
        error = ValueError("test error")

        frame = SafeFrame(saved_env=env)
        result = frame.on_error(error, env, store, k)

        assert isinstance(result, ContinueValue)
        assert isinstance(result.value, Err)
        assert result.value.error is error


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

        assert isinstance(result, ContinueValue)
        assert result.value == 42

    def test_on_error_propagates(self) -> None:
        """on_error propagates the error."""
        env: Environment = FrozenDict()
        store: Store = {}
        k: Kontinuation = []
        task_ids = (TaskId.new(),)
        error = ValueError("race failed")

        frame = RaceFrame(task_ids=task_ids, saved_env=env)
        result = frame.on_error(error, env, store, k)

        assert isinstance(result, ContinueError)
        assert result.error is error


class TestReturnFrame:
    """Tests for ReturnFrame."""

    def test_on_value_creates_continue_generator(self) -> None:
        """on_value creates ContinueGenerator with send_value."""

        def gen() -> Generator[int, int, int]:
            x = yield 1
            return x

        g = gen()
        next(g)  # Start generator

        env: Environment = FrozenDict()
        store: Store = {}
        k: Kontinuation = []

        frame = ReturnFrame(generator=g, saved_env=env)
        result = frame.on_value(42, env, store, k)

        assert isinstance(result, ContinueGenerator)
        assert result.generator is g
        assert result.send_value == 42
        assert result.throw_error is None

    def test_on_error_creates_continue_generator_with_error(self) -> None:
        """on_error creates ContinueGenerator with throw_error."""

        def gen() -> Generator[int, int, int]:
            try:
                x = yield 1
            except ValueError:
                return -1
            return x

        g = gen()
        next(g)  # Start generator

        env: Environment = FrozenDict()
        store: Store = {}
        k: Kontinuation = []
        error = ValueError("test")

        frame = ReturnFrame(generator=g, saved_env=env)
        result = frame.on_error(error, env, store, k)

        assert isinstance(result, ContinueGenerator)
        assert result.generator is g
        assert result.send_value is None
        assert result.throw_error is error


class TestFrameProtocol:
    """Tests that frame types implement the Frame protocol."""

    def test_local_frame_is_frame(self) -> None:
        """LocalFrame implements Frame protocol."""
        frame = LocalFrame(restore_env=FrozenDict())
        assert isinstance(frame, Frame)

    def test_intercept_frame_is_frame(self) -> None:
        """InterceptFrame implements Frame protocol."""
        frame = InterceptFrame(transforms=())
        assert isinstance(frame, Frame)

    def test_listen_frame_is_frame(self) -> None:
        """ListenFrame implements Frame protocol."""
        frame = ListenFrame(log_start_index=0)
        assert isinstance(frame, Frame)

    def test_gather_frame_is_frame(self) -> None:
        """GatherFrame implements Frame protocol."""
        frame = GatherFrame(
            remaining_programs=[],
            collected_results=[],
            saved_env=FrozenDict(),
        )
        assert isinstance(frame, Frame)

    def test_safe_frame_is_frame(self) -> None:
        """SafeFrame implements Frame protocol."""
        frame = SafeFrame(saved_env=FrozenDict())
        assert isinstance(frame, Frame)

    def test_race_frame_is_frame(self) -> None:
        """RaceFrame implements Frame protocol."""
        frame = RaceFrame(task_ids=(), saved_env=FrozenDict())
        assert isinstance(frame, Frame)

    def test_return_frame_is_frame(self) -> None:
        """ReturnFrame implements Frame protocol."""

        def gen() -> Generator[int, int, int]:
            x = yield 1
            return x

        g = gen()
        frame = ReturnFrame(generator=g, saved_env=FrozenDict())
        assert isinstance(frame, Frame)
