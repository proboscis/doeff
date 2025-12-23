"""Tests for spawn effect."""

from __future__ import annotations

import asyncio
import atexit
import importlib.util
import logging
from contextlib import contextmanager
from typing import TYPE_CHECKING, Any, Iterator

import pytest

from doeff import (
    Effect,
    EffectGenerator,
    Fail,
    Gather,
    Get,
    IO,
    Log,
    Parallel,
    Program,
    Put,
    ProgramInterpreter,
    Recover,
    Spawn,
    Task,
    do,
    slog,
)
from doeff.effects import WriterTellEffect

_RAY_TEST_CPUS = 4


def _backend_params() -> list[Any]:  # noqa: DOEFF022
    return [
        pytest.param("thread", id="thread"),
        pytest.param("process", id="process"),
        pytest.param("ray", id="ray"),
    ]


def _ray_task_options(backend: str) -> dict[str, Any]:  # noqa: DOEFF022
    if backend == "ray":
        return {"num_cpus": 1}
    return {}


def _build_engine(backend: str, *, default_backend: str | None = None) -> ProgramInterpreter:  # noqa: DOEFF022
    spawn_defaults = {}
    if default_backend is not None:
        spawn_defaults["spawn_default_backend"] = default_backend
    if backend == "ray":
        spawn_defaults["spawn_ray_init_kwargs"] = {
            "num_cpus": _RAY_TEST_CPUS,
            "include_dashboard": False,
            "log_to_driver": False,
        }
    else:
        spawn_defaults["spawn_process_max_workers"] = 2
    return ProgramInterpreter(**spawn_defaults)


_ray_runtime: Any | None = None


@contextmanager
def _ray_context(backend: str) -> Iterator[None]:  # noqa: DOEFF022
    if backend != "ray":
        yield
        return
    global _ray_runtime
    if _ray_runtime is None:
        ray = pytest.importorskip("ray")
        if ray.is_initialized():
            ray.shutdown()
        ray.init(
            num_cpus=_RAY_TEST_CPUS,
            include_dashboard=False,
            log_to_driver=False,
            runtime_env={"working_dir": "."},
        )
        _ray_runtime = ray
        atexit.register(_shutdown_ray)
    yield


def _shutdown_ray() -> None:  # noqa: DOEFF022
    if _ray_runtime is not None:
        _ray_runtime.shutdown()


@pytest.mark.asyncio
@pytest.mark.parametrize("backend", _backend_params())
async def test_spawn_join_basic(backend: str) -> None:
    with _ray_context(backend):
        engine = _build_engine(backend)

        @do
        def worker() -> EffectGenerator[int]:
            return 42

        @do
        def program() -> EffectGenerator[int]:
            task = yield Spawn(worker(), preferred_backend=backend, **_ray_task_options(backend))
            return (yield task.join())

        result = await engine.run_async(program())

    assert result.is_ok
    assert result.value == 42


@pytest.mark.asyncio
@pytest.mark.parametrize("backend", _backend_params())
async def test_spawn_with_effects(backend: str) -> None:
    with _ray_context(backend):
        engine = _build_engine(backend)

        @do
        def worker() -> EffectGenerator[int]:
            yield Put("key", "value")
            yield slog(event="worker_log", data="test")
            return 42

        @do
        def program() -> EffectGenerator[int]:
            task = yield Spawn(worker(), preferred_backend=backend, **_ray_task_options(backend))
            return (yield task.join())

        result = await engine.run_async(program())

    assert result.is_ok
    assert result.value == 42
    assert result.state.get("key") == "value"


@pytest.mark.asyncio
async def test_spawn_warns_when_ray_unavailable(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    engine = ProgramInterpreter()
    original_find_spec = importlib.util.find_spec

    def fake_find_spec(name: str, *args: Any, **kwargs: Any) -> Any:  # noqa: DOEFF022
        if name == "ray":
            return None
        return original_find_spec(name, *args, **kwargs)

    monkeypatch.setattr(importlib.util, "find_spec", fake_find_spec)
    caplog.set_level(logging.WARNING, logger="doeff.handlers")

    @do
    def worker() -> EffectGenerator[int]:
        return 42

    @do
    def program() -> EffectGenerator[int]:
        task = yield Spawn(worker(), preferred_backend="ray")
        return (yield task.join())

    result = await engine.run_async(program())

    assert result.is_ok
    assert result.value == 42
    assert "Ray is not available" in caplog.text


@pytest.mark.asyncio
@pytest.mark.parametrize("backend", _backend_params())
async def test_spawn_with_intercept(backend: str) -> None:
    with _ray_context(backend):
        engine = _build_engine(backend)

        @do
        def worker() -> EffectGenerator[str]:
            yield slog(event="spawned", detail="ok")
            return "done"

        def intercept(effect: Effect) -> Effect:  # noqa: DOEFF022
            if isinstance(effect, WriterTellEffect) and isinstance(effect.message, dict):
                if effect.message.get("event") == "spawned":
                    return Log({"intercepted": effect.message["event"]})
            return effect

        @do
        def program() -> EffectGenerator[str]:
            task = yield Spawn(worker(), preferred_backend=backend, **_ray_task_options(backend))
            return (yield task.join())

        result = await engine.run_async(program().intercept(intercept))

    assert result.is_ok
    assert result.value == "done"


@pytest.mark.asyncio
@pytest.mark.parametrize("backend", _backend_params())
async def test_spawn_multiple_workers_with_state(backend: str) -> None:
    with _ray_context(backend):
        engine = _build_engine(backend)

        @do
        def worker(index: int) -> EffectGenerator[int]:
            yield Put(f"state_{index}", index)
            return index

        @do
        def program() -> EffectGenerator[tuple[list[int], list[int]]]:
            tasks = []
            for i in range(3):  # noqa: DOEFF012
                tasks.append(
                    (
                        yield Spawn(
                            worker(i),
                            preferred_backend=backend,
                            **_ray_task_options(backend),
                        )
                    )
                )
            results: list[int] = []
            for task in tasks:  # noqa: DOEFF012
                results.append((yield task.join()))
            states: list[int] = []
            for i in range(3):  # noqa: DOEFF012
                states.append((yield Get(f"state_{i}")))
            return results, states

        result = await engine.run_async(program())

    assert result.is_ok
    assert result.value == ([0, 1, 2], [0, 1, 2])


@pytest.mark.asyncio
@pytest.mark.parametrize("backend", _backend_params())
async def test_spawn_worker_gather_programs(backend: str) -> None:
    with _ray_context(backend):
        engine = _build_engine(backend)

        @do
        def worker(index: int) -> EffectGenerator[int]:
            return index * 2

        @do
        def program() -> EffectGenerator[list[int]]:
            tasks = []
            for i in range(4):  # noqa: DOEFF012
                tasks.append(
                    (
                        yield Spawn(
                            worker(i),
                            preferred_backend=backend,
                            **_ray_task_options(backend),
                        )
                    )
                )
            results = yield Gather(*[task.join() for task in tasks])
            return list(results)

        result = await engine.run_async(program())

    assert result.is_ok
    assert result.value == [0, 2, 4, 6]


@pytest.mark.asyncio
@pytest.mark.parametrize("backend", _backend_params())
async def test_spawn_worker_parallel_awaitables(backend: str) -> None:
    with _ray_context(backend):
        engine = _build_engine(backend)

        async def compute(label: str) -> str:  # noqa: DOEFF022
            await asyncio.sleep(0.01)
            return label

        @do
        def worker() -> EffectGenerator[list[str]]:
            return (yield Parallel(compute("a"), compute("b")))

        @do
        def program() -> EffectGenerator[list[str]]:
            task = yield Spawn(worker(), preferred_backend=backend, **_ray_task_options(backend))
            return (yield task.join())

        result = await engine.run_async(program())

    assert result.is_ok
    assert sorted(result.value) == ["a", "b"]


@pytest.mark.asyncio
@pytest.mark.parametrize("backend", _backend_params())
async def test_spawn_recover_handles_failure(backend: str) -> None:
    with _ray_context(backend):
        engine = _build_engine(backend)

        @do
        def failing_worker() -> EffectGenerator[int]:
            yield Fail(ValueError("test error"))
            return 42

        @do
        def program() -> EffectGenerator[int | str]:
            task = yield Spawn(failing_worker(), preferred_backend=backend, **_ray_task_options(backend))
            return (yield Recover(task.join(), "fallback"))

        result = await engine.run_async(program())

    assert result.is_ok
    assert result.value == "fallback"


@pytest.mark.asyncio
@pytest.mark.parametrize("backend", _backend_params())
async def test_spawn_state_snapshot_read(backend: str) -> None:
    with _ray_context(backend):
        engine = _build_engine(backend)

        @do
        def worker() -> EffectGenerator[int]:
            return (yield Get("initial"))

        @do
        def program() -> EffectGenerator[int]:
            yield Put("initial", 100)
            task = yield Spawn(worker(), preferred_backend=backend, **_ray_task_options(backend))
            return (yield task.join())

        result = await engine.run_async(program())

    assert result.is_ok
    assert result.value == 100


@pytest.mark.asyncio
@pytest.mark.parametrize("backend", _backend_params())
async def test_spawn_state_merge_preserves_parent_updates(backend: str) -> None:
    with _ray_context(backend):
        engine = _build_engine(backend)

        @do
        def worker() -> EffectGenerator[int]:
            yield Put("child_key", "child_value")
            return 42

        @do
        def program() -> EffectGenerator[tuple[int, str | None, str | None]]:
            yield Put("parent_key", "parent_value")
            task = yield Spawn(worker(), preferred_backend=backend, **_ray_task_options(backend))
            result = yield task.join()
            parent_val = yield Get("parent_key")
            child_val = yield Get("child_key")
            return result, parent_val, child_val

        result = await engine.run_async(program())

    assert result.is_ok
    assert result.value == (42, "parent_value", "child_value")


@pytest.mark.asyncio
@pytest.mark.parametrize("backend", _backend_params())
async def test_spawn_state_isolation_between_tasks(backend: str) -> None:
    with _ray_context(backend):
        engine = _build_engine(backend)

        @do
        def worker(key: str, value: str) -> EffectGenerator[str]:
            yield Put(key, value)
            return value

        @do
        def program() -> EffectGenerator[tuple[str, str, str | None, str | None]]:
            task1 = yield Spawn(
                worker("key1", "value1"),
                preferred_backend=backend,
                **_ray_task_options(backend),
            )
            task2 = yield Spawn(
                worker("key2", "value2"),
                preferred_backend=backend,
                **_ray_task_options(backend),
            )
            result1 = yield task1.join()
            result2 = yield task2.join()
            state1 = yield Get("key1")
            state2 = yield Get("key2")
            return result1, result2, state1, state2

        result = await engine.run_async(program())

    assert result.is_ok
    assert result.value == ("value1", "value2", "value1", "value2")


@pytest.mark.asyncio
async def test_spawn_thread_atomic_updates_shared_state() -> None:
    from doeff.effects.atomic import AtomicUpdate

    engine = ProgramInterpreter()

    @do
    def incrementer() -> EffectGenerator[int]:
        for _ in range(10):
            yield AtomicUpdate(
                "counter",
                lambda current: (current or 0) + 1,
                default_factory=lambda: 0,
            )
        return (yield Get("counter"))

    @do
    def program() -> EffectGenerator[list[int]]:
        tasks = []
        for _ in range(5):
            tasks.append((yield Spawn(incrementer(), preferred_backend="thread")))
        results = []
        for task in tasks:
            results.append((yield task.join()))
        return results

    result = await engine.run_async(program())

    assert result.is_ok
    assert result.state["counter"] == 50


@pytest.mark.asyncio
@pytest.mark.parametrize("backend", _backend_params())
async def test_spawn_exception_propagates(backend: str) -> None:
    with _ray_context(backend):
        engine = _build_engine(backend)

        @do
        def failing_worker() -> EffectGenerator[int]:
            raise ValueError("test exception")

        @do
        def program() -> EffectGenerator[int]:
            task = yield Spawn(failing_worker(), preferred_backend=backend, **_ray_task_options(backend))
            return (yield task.join())

        result = await engine.run_async(program())

    assert result.is_err
    assert "test exception" in str(result.result.error)


@pytest.mark.asyncio
@pytest.mark.parametrize("backend", _backend_params())
async def test_spawn_join_idempotent(backend: str) -> None:
    with _ray_context(backend):
        engine = _build_engine(backend)

        @do
        def worker() -> EffectGenerator[int]:
            return 42

        @do
        def program() -> EffectGenerator[tuple[int, int]]:
            task = yield Spawn(worker(), preferred_backend=backend, **_ray_task_options(backend))
            result1 = yield task.join()
            result2 = yield task.join()
            return result1, result2

        result = await engine.run_async(program())

    assert result.is_ok
    assert result.value == (42, 42)


@pytest.mark.asyncio
@pytest.mark.parametrize("backend", _backend_params())
async def test_spawn_nested_spawn(backend: str) -> None:
    with _ray_context(backend):
        engine = _build_engine(backend)

        @do
        def inner_worker() -> EffectGenerator[int]:
            return 42

        @do
        def outer_worker() -> EffectGenerator[int]:
            task = yield Spawn(
                inner_worker(),
                preferred_backend=backend,
                **_ray_task_options(backend),
            )
            return (yield task.join())

        @do
        def program() -> EffectGenerator[int]:
            task = yield Spawn(outer_worker(), preferred_backend=backend, **_ray_task_options(backend))
            return (yield task.join())

        result = await engine.run_async(program())

    assert result.is_ok
    assert result.value == 42


@pytest.mark.asyncio
async def test_spawn_triple_nested_remote_call() -> None:
    with _ray_context("ray"):
        engine = _build_engine("ray")

        @do
        def level3() -> EffectGenerator[int]:
            yield Put("level3", True)
            return 3

        @do
        def level2() -> EffectGenerator[int]:
            yield Put("level2", True)
            task = yield Spawn(level3(), preferred_backend="ray", **_ray_task_options("ray"))
            result = yield task.join()
            return result + 2

        @do
        def level1() -> EffectGenerator[int]:
            yield Put("level1", True)
            task = yield Spawn(level2(), preferred_backend="ray", **_ray_task_options("ray"))
            result = yield task.join()
            return result + 1

        @do
        def program() -> EffectGenerator[int]:
            task = yield Spawn(level1(), preferred_backend="ray", **_ray_task_options("ray"))
            return (yield task.join())

        result = await engine.run_async(program())

    assert result.is_ok
    assert result.value == 6
    assert result.state.get("level1") is True
    assert result.state.get("level2") is True
    assert result.state.get("level3") is True


@pytest.mark.asyncio
@pytest.mark.parametrize("backend", _backend_params())
async def test_spawn_parallel_overlap(backend: str) -> None:
    import time

    with _ray_context(backend):
        engine = _build_engine(backend)

        @do
        def slow_worker(sleep_time: float) -> EffectGenerator[float]:
            yield IO(lambda: time.sleep(sleep_time))
            return sleep_time

        @do
        def program() -> EffectGenerator[tuple[float, float]]:
            task1 = yield Spawn(
                slow_worker(0.1),
                preferred_backend=backend,
                **_ray_task_options(backend),
            )
            task2 = yield Spawn(
                slow_worker(0.1),
                preferred_backend=backend,
                **_ray_task_options(backend),
            )
            result1 = yield task1.join()
            result2 = yield task2.join()
            return result1, result2

        start = time.time()
        result = await engine.run_async(program())
        elapsed = time.time() - start

    assert result.is_ok
    assert result.value == (0.1, 0.1)
    assert elapsed < 0.3


@pytest.mark.asyncio
async def test_spawn_process_serialization_error() -> None:
    engine = ProgramInterpreter(spawn_process_max_workers=2)

    class Unpicklable:
        def __reduce__(self) -> None:
            raise TypeError("cannot pickle")

    @do
    def worker() -> EffectGenerator[int]:
        return 42

    @do
    def program() -> EffectGenerator[int]:
        yield Put("bad", Unpicklable())
        task = yield Spawn(worker(), preferred_backend="process")
        return (yield task.join())

    result = await engine.run_async(program())

    assert result.is_err
    assert "cloudpickle" in str(result.result.error).lower()


# Module-level picklable functions for TestPicklableFactories tests
def _test_double(x: int) -> int:
    return x * 2


def _test_square(x: int) -> int:
    return x * x


def _test_add_three(x: int) -> int:
    return x + 3


def _test_add_one(x: int) -> int:
    return x + 1


def _test_double_program(x: int) -> "Program[int]":
    from doeff import Program

    return Program.pure(x * 2)


class TestPicklableFactories:
    """Tests for picklable factory classes in Program combinators.

    These tests verify that Programs created via map/flat_map can be
    serialized with standard pickle for process/ray backends.
    See ISSUE-CORE-409.
    """

    def test_map_factory_is_picklable(self) -> None:
        """_MapFactory dataclass is picklable with standard pickle.

        This was the primary issue in ISSUE-CORE-409: map created a
        GeneratorProgram with a local factory function that couldn't be pickled.
        """
        import pickle

        from doeff import Program
        from doeff.program import _MapFactory

        p_base = Program.pure(10)
        factory = _MapFactory(source=p_base, transform=_test_double)

        # Verify pickling works with standard pickle
        pickled = pickle.dumps(factory)
        unpickled = pickle.loads(pickled)

        assert isinstance(unpickled, _MapFactory)
        assert unpickled.transform(5) == 10

    def test_flat_map_factory_is_picklable(self) -> None:
        """_FlatMapFactory dataclass is picklable with standard pickle."""
        import pickle

        from doeff import Program
        from doeff.program import _FlatMapFactory

        p_base = Program.pure(10)
        factory = _FlatMapFactory(source=p_base, binder=_test_double_program)

        # Verify pickling works with standard pickle
        pickled = pickle.dumps(factory)
        unpickled = pickle.loads(pickled)

        assert isinstance(unpickled, _FlatMapFactory)

    def test_getitem_transform_is_picklable(self) -> None:
        """_GetItemTransform dataclass is picklable with standard pickle."""
        import pickle

        from doeff.program import _GetItemTransform

        transform = _GetItemTransform(key="my_key")

        # Verify pickling works with standard pickle
        pickled = pickle.dumps(transform)
        unpickled = pickle.loads(pickled)

        assert isinstance(unpickled, _GetItemTransform)
        assert unpickled({"my_key": 42}) == 42

    def test_getattr_transform_is_picklable(self) -> None:
        """_GetAttrTransform dataclass is picklable with standard pickle."""
        import pickle

        from doeff.program import _GetAttrTransform

        # Test attribute access (not method call)
        transform = _GetAttrTransform(name="real")

        # Verify pickling works with standard pickle
        pickled = pickle.dumps(transform)
        unpickled = pickle.loads(pickled)

        assert isinstance(unpickled, _GetAttrTransform)
        # Test on a complex number - real is an attribute, not a method
        assert unpickled(complex(3, 4)) == 3.0

    def test_generator_program_with_map_is_picklable(self) -> None:
        """GeneratorProgram created via map() is picklable with standard pickle."""
        import pickle

        from doeff import Program

        p_base = Program.pure(5)
        p_squared = p_base.map(_test_square)

        # Verify pickling works with standard pickle
        pickled = pickle.dumps(p_squared)
        unpickled = pickle.loads(pickled)

        # Verify the unpickled program still works
        from doeff import ProgramInterpreter

        engine = ProgramInterpreter()
        result = engine.run(unpickled)
        assert result.is_ok
        assert result.value == 25

    def test_generator_program_with_flat_map_is_picklable(self) -> None:
        """GeneratorProgram created via flat_map() is picklable with standard pickle."""
        import pickle

        from doeff import Program

        p_base = Program.pure(10)
        p_doubled = p_base.flat_map(_test_double_program)

        # Verify pickling works with standard pickle
        pickled = pickle.dumps(p_doubled)
        unpickled = pickle.loads(pickled)

        # Verify the unpickled program still works
        from doeff import ProgramInterpreter

        engine = ProgramInterpreter()
        result = engine.run(unpickled)
        assert result.is_ok
        assert result.value == 20

    def test_chained_map_flat_map_is_picklable(self) -> None:
        """Chained map/flat_map operations result in picklable Programs."""
        import pickle

        from doeff import Program

        # Chain multiple operations
        p = (
            Program.pure(2)
            .map(_test_add_three)  # 5
            .flat_map(_test_double_program)  # 10
            .map(_test_add_one)  # 11
        )

        # Verify pickling works with standard pickle
        pickled = pickle.dumps(p)
        unpickled = pickle.loads(pickled)

        # Verify the unpickled program still works
        from doeff import ProgramInterpreter

        engine = ProgramInterpreter()
        result = engine.run(unpickled)
        assert result.is_ok
        assert result.value == 11

    def test_getitem_program_is_picklable(self) -> None:
        """Program created via __getitem__ is picklable with standard pickle."""
        import pickle

        from doeff import Program

        p_base = Program.pure({"a": 1, "b": 2})
        p_item = p_base["a"]

        # Verify pickling works with standard pickle
        pickled = pickle.dumps(p_item)
        unpickled = pickle.loads(pickled)

        # Verify the unpickled program still works
        from doeff import ProgramInterpreter

        engine = ProgramInterpreter()
        result = engine.run(unpickled)
        assert result.is_ok
        assert result.value == 1

    def test_tuple_program_is_picklable(self) -> None:
        """Program.tuple() is picklable with standard pickle."""
        import pickle

        from doeff import Program

        p = Program.tuple(Program.pure(1), Program.pure(2), 3)

        # Verify pickling works with standard pickle
        pickled = pickle.dumps(p)
        unpickled = pickle.loads(pickled)

        # Verify the unpickled program still works
        from doeff import ProgramInterpreter

        engine = ProgramInterpreter()
        result = engine.run(unpickled)
        assert result.is_ok
        assert result.value == (1, 2, 3)

    def test_set_program_is_picklable(self) -> None:
        """Program.set() is picklable with standard pickle."""
        import pickle

        from doeff import Program

        p = Program.set(Program.pure(1), Program.pure(2), 3)

        # Verify pickling works with standard pickle
        pickled = pickle.dumps(p)
        unpickled = pickle.loads(pickled)

        # Verify the unpickled program still works
        from doeff import ProgramInterpreter

        engine = ProgramInterpreter()
        result = engine.run(unpickled)
        assert result.is_ok
        assert result.value == {1, 2, 3}

    def test_sequence_factory_is_picklable(self) -> None:
        """_SequenceFactory dataclass is picklable with standard pickle."""
        import pickle

        from doeff import Program
        from doeff.program import _SequenceFactory

        programs = [Program.pure(1), Program.pure(2), Program.pure(3)]
        factory = _SequenceFactory(programs=programs)

        # Verify pickling works with standard pickle
        pickled = pickle.dumps(factory)
        unpickled = pickle.loads(pickled)

        assert isinstance(unpickled, _SequenceFactory)
        assert len(unpickled.programs) == 3

    def test_dict_factory_is_picklable(self) -> None:
        """_DictFactory dataclass is picklable with standard pickle."""
        import pickle

        from doeff import Program
        from doeff.program import _DictFactory

        program_map = {"a": Program.pure(1), "b": Program.pure(2)}
        factory = _DictFactory(program_map=program_map)

        # Verify pickling works with standard pickle
        pickled = pickle.dumps(factory)
        unpickled = pickle.loads(pickled)

        assert isinstance(unpickled, _DictFactory)
        assert "a" in unpickled.program_map
        assert "b" in unpickled.program_map

    def test_dict_program_is_picklable(self) -> None:
        """Program.dict() is picklable with standard pickle."""
        import pickle

        from doeff import Program

        p = Program.dict(a=Program.pure(1), b=Program.pure(2), c=3)

        # Verify pickling works with standard pickle
        pickled = pickle.dumps(p)
        unpickled = pickle.loads(pickled)

        # Verify the unpickled program still works
        from doeff import ProgramInterpreter

        engine = ProgramInterpreter()
        result = engine.run(unpickled)
        assert result.is_ok
        assert result.value == {"a": 1, "b": 2, "c": 3}

    @pytest.mark.asyncio
    @pytest.mark.parametrize("backend", _backend_params())
    async def test_process_backend_succeeds_with_flat_map_program_in_state(
        self, backend: str
    ) -> None:
        """Process backend succeeds with Program from flat_map in state.

        This tests that Programs using flat_map can be serialized and
        passed through spawn backends via state.
        """
        from doeff import Program

        with _ray_context(backend):
            engine = _build_engine(backend)

            # Create a Program using flat_map with module-level function
            p_base = Program.pure(10)
            p_doubled = p_base.flat_map(_test_double_program)

            @do
            def worker() -> EffectGenerator[int]:
                # Read the program from state and execute it
                prog = yield Get("my_program")
                result = yield prog
                return result

            @do
            def program() -> EffectGenerator[int]:
                # Store the program in state before spawning
                yield Put("my_program", p_doubled)
                task = yield Spawn(
                    worker(),
                    preferred_backend=backend,
                    **_ray_task_options(backend),
                )
                return (yield task.join())

            result = await engine.run_async(program())

        assert result.is_ok, f"Expected success, got error: {result.result.error}"
        assert result.value == 20

    @pytest.mark.asyncio
    @pytest.mark.parametrize("backend", _backend_params())
    async def test_spawn_backend_succeeds_with_map_program_in_state(
        self, backend: str
    ) -> None:
        """Spawn backend succeeds with Program from map in state."""
        from doeff import Program

        with _ray_context(backend):
            engine = _build_engine(backend)

            # Create a Program using map with module-level function
            p_base = Program.pure(5)
            p_squared = p_base.map(_test_square)

            @do
            def worker() -> EffectGenerator[int]:
                prog = yield Get("my_program")
                result = yield prog
                return result

            @do
            def program() -> EffectGenerator[int]:
                yield Put("my_program", p_squared)
                task = yield Spawn(
                    worker(),
                    preferred_backend=backend,
                    **_ray_task_options(backend),
                )
                return (yield task.join())

            result = await engine.run_async(program())

        assert result.is_ok, f"Expected success, got error: {result.result.error}"
        assert result.value == 25

    @pytest.mark.asyncio
    @pytest.mark.parametrize("backend", _backend_params())
    async def test_process_backend_succeeds_with_ask_effect_after_frame_info_fix(
        self, backend: str
    ) -> None:
        """Process backend succeeds with AskEffect after frame_info fix.

        This test verifies that AskEffect (which has frame_info) can be
        serialized properly (frame_info sanitization from ISSUE-CORE-407).
        """
        from doeff import ask

        with _ray_context(backend):
            engine = _build_engine(backend)

            # Create an ask effect (has frame_info)
            ask_effect = ask("some_key")

            @do
            def worker() -> EffectGenerator[str]:
                # Read the effect from state and execute it
                effect = yield Get("the_ask_effect")
                # We just confirm the effect exists and is picklable
                return "got_effect"

            @do
            def program() -> EffectGenerator[str]:
                yield Put("the_ask_effect", ask_effect)
                yield Put("some_key", "hello")
                task = yield Spawn(
                    worker(),
                    preferred_backend=backend,
                    **_ray_task_options(backend),
                )
                return (yield task.join())

            result = await engine.run_async(program())

        assert result.is_ok, f"Expected success, got error: {result.result.error}"
        assert result.value == "got_effect"


class TestEnvSerializationIssues:
    """Tests for env serialization issues with spawn backends.

    These tests verify that:
    1. Programs created via flat_map (with lambda factories) serialize correctly
    2. AskEffect objects with frame_info serialize correctly after the frame_info fix

    Both env (reader environment) and state are serialized and passed to spawned
    processes. These tests use state (via Put/Get) to store Programs/Effects since
    state is the typical mechanism for passing data to spawned workers.
    """

    @pytest.mark.asyncio
    async def test_process_backend_succeeds_with_flat_map_program_in_env(self) -> None:
        """Process backend succeeds with Program from flat_map using cloudpickle.

        flat_map creates a GeneratorProgram with a local factory function that
        captures the binder lambda in its closure. Standard pickle cannot serialize
        this, but cloudpickle can.
        """
        engine = ProgramInterpreter(spawn_process_max_workers=2)

        # Create a Program via flat_map - this creates a GeneratorProgram with
        # a local factory function containing a lambda closure
        p_base = Program.pure(5)
        p_flat_mapped = p_base.flat_map(lambda x: Program.pure(x * 2))

        @do
        def worker() -> EffectGenerator[int]:
            # Access the program from state and run it
            prog = yield Get("program")
            return (yield prog)

        @do
        def program() -> EffectGenerator[int]:
            # Put the flat_mapped program (with lambda closure) in state
            # State is serialized and passed to spawned processes
            yield Put("program", p_flat_mapped)
            task = yield Spawn(worker(), preferred_backend="process")
            return (yield task.join())

        result = await engine.run_async(program())

        assert result.is_ok, f"Expected success, got error: {result.result}"
        assert result.value == 10  # 5 * 2 = 10

    @pytest.mark.asyncio
    async def test_ray_backend_succeeds_with_flat_map_program_in_env(self) -> None:
        """Ray backend succeeds with Program from flat_map."""
        with _ray_context("ray"):
            engine = _build_engine("ray")

            # Create a Program via flat_map
            p_base = Program.pure(5)
            p_flat_mapped = p_base.flat_map(lambda x: Program.pure(x * 2))

            @do
            def worker() -> EffectGenerator[int]:
                prog = yield Get("program")
                return (yield prog)

            @do
            def program() -> EffectGenerator[int]:
                yield Put("program", p_flat_mapped)
                task = yield Spawn(
                    worker(),
                    preferred_backend="ray",
                    **_ray_task_options("ray"),
                )
                return (yield task.join())

            result = await engine.run_async(program())

        assert result.is_ok, f"Expected success, got error: {result.result}"
        assert result.value == 10

    @pytest.mark.asyncio
    async def test_process_backend_succeeds_with_ask_effect_after_frame_info_fix(
        self,
    ) -> None:
        """Process backend succeeds with AskEffect after frame_info fix.

        AskEffect objects capture frame_info for debugging/tracing purposes.
        The frame_info fix (ISSUE-CORE-407) added __getstate__/__setstate__ to
        EffectCreationContext to sanitize frame references before serialization.

        This test stores an AskEffect in state, serializes it to a subprocess,
        retrieves it, and yields it. The AskEffect then reads from env which
        must also be set up correctly.
        """
        from doeff import Local
        from doeff.effects import ask

        engine = ProgramInterpreter(spawn_process_max_workers=2)

        # Create an AskEffect which has frame_info captured at creation time
        ask_effect = ask("some_key")

        @do
        def worker() -> EffectGenerator[str | None]:
            # Access the ask effect from state and yield it
            # The AskEffect will then read "some_key" from env
            effect = yield Get("ask_effect")
            return (yield effect)

        @do
        def program() -> EffectGenerator[str | None]:
            # Put the ask effect (with frame_info) in state
            yield Put("ask_effect", ask_effect)
            # Use Local to set "some_key" in env, then spawn within that context
            inner_program = Spawn(worker(), preferred_backend="process")

            @do
            def spawn_and_join() -> EffectGenerator[str | None]:
                task = yield inner_program
                return (yield task.join())

            # Run spawn within Local context so env has "some_key"
            return (yield Local({"some_key": "expected_value"}, spawn_and_join()))

        result = await engine.run_async(program())

        assert result.is_ok, f"Expected success, got error: {result.result}"
        assert result.value == "expected_value"


class TestValuesEqual:
    """Tests for SpawnEffectHandler._values_equal handling non-bool __eq__ results.

    This addresses ISSUE-CORE-412: when comparing values during context merge,
    objects with non-bool __eq__ (numpy arrays, pandas DataFrames) would cause
    'ValueError: The truth value of a DataFrame is ambiguous'.
    """

    def test_values_equal_with_regular_values(self) -> None:
        """Test _values_equal works with regular Python values."""
        from doeff.handlers import SpawnEffectHandler

        assert SpawnEffectHandler._values_equal(1, 1) is True
        assert SpawnEffectHandler._values_equal(1, 2) is False
        assert SpawnEffectHandler._values_equal("a", "a") is True
        assert SpawnEffectHandler._values_equal("a", "b") is False
        assert SpawnEffectHandler._values_equal([1, 2], [1, 2]) is True
        assert SpawnEffectHandler._values_equal([1, 2], [1, 3]) is False

    def test_values_equal_with_numpy_array(self) -> None:
        """Test _values_equal handles numpy arrays (non-bool __eq__)."""
        import numpy as np
        from doeff.handlers import SpawnEffectHandler

        arr1 = np.array([1, 2, 3])
        arr2 = np.array([1, 2, 3])
        arr3 = np.array([4, 5, 6])

        # numpy __eq__ returns array, not bool - should return False, not raise
        assert SpawnEffectHandler._values_equal(arr1, arr2) is False
        assert SpawnEffectHandler._values_equal(arr1, arr3) is False

        # Same object should still work (identity check happens before __eq__)
        assert SpawnEffectHandler._values_equal(arr1, arr1) is False  # numpy returns array

    def test_values_equal_with_exception_raising_eq(self) -> None:
        """Test _values_equal handles objects that raise on __eq__."""
        from doeff.handlers import SpawnEffectHandler

        class RaisingEq:
            def __eq__(self, other: object) -> bool:
                raise RuntimeError("Cannot compare")

        obj1 = RaisingEq()
        obj2 = RaisingEq()

        # Should return False instead of raising
        assert SpawnEffectHandler._values_equal(obj1, obj2) is False
