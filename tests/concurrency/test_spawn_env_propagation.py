"""Tests for env effect propagation to spawned sub programs.

These tests verify that:
1. Env effects (via Ask) are properly propagated to spawned sub programs
2. Nested spawns (A->B->C) receive env recursively
3. Protocol-based dependency injection works in spawned programs

ISSUE-CORE-415
"""

from __future__ import annotations

import atexit
from contextlib import contextmanager
from typing import Any, Iterator, Protocol

import pytest

pytestmark = pytest.mark.skip(
    reason="ProgramInterpreter removed; CESKInterpreter doesn't support spawn configuration"
)

from doeff import (
    Ask,
    EffectGenerator,
    Local,
    ProgramInterpreter,
    Spawn,
    do,
)


_RAY_TEST_CPUS = 4


def _backend_params() -> list[Any]:
    return [
        pytest.param("thread", id="thread"),
        pytest.param("process", id="process"),
        pytest.param("ray", id="ray"),
    ]


def _ray_task_options(backend: str) -> dict[str, Any]:
    if backend == "ray":
        return {"num_cpus": 1}
    return {}


def _build_engine(backend: str, *, default_backend: str | None = None) -> ProgramInterpreter:
    spawn_defaults = {}
    if default_backend is not None:
        spawn_defaults["spawn_default_backend"] = default_backend
    if backend == "ray":
        spawn_defaults["spawn_ray_init_kwargs"] = {
            "num_cpus": _RAY_TEST_CPUS,
            "include_dashboard": False,
            "log_to_driver": False,
        }
    if backend == "process":
        spawn_defaults["spawn_process_max_workers"] = 2
    return ProgramInterpreter(**spawn_defaults)


_ray_runtime: Any | None = None


@contextmanager
def _ray_context(backend: str) -> Iterator[None]:
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


def _shutdown_ray() -> None:
    if _ray_runtime is not None:
        _ray_runtime.shutdown()


class TestEnvPropagationToSpawnedPrograms:
    """Tests for env effect propagation to spawned sub programs."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize("backend", _backend_params())
    async def test_spawn_receives_env_from_parent(self, backend: str) -> None:
        """Verify that a spawned program can access env values set by Local.

        This tests the basic case: parent sets env via Local, spawns a child,
        and the child should be able to Ask for the env value.
        """
        with _ray_context(backend):
            engine = _build_engine(backend)

            @do
            def worker() -> EffectGenerator[str]:
                # Spawned program should be able to access env via Ask
                value = yield Ask("config_key")
                return f"received:{value}"

            @do
            def program() -> EffectGenerator[str]:
                task = yield Spawn(
                    worker(),
                    preferred_backend=backend,
                    **_ray_task_options(backend),
                )
                return (yield task.join())

            # Run program within Local context that sets env
            result = await engine.run_async(
                Local({"config_key": "config_value"}, program())
            )

        assert result.is_ok, f"Expected success, got error: {result.result}"
        assert result.value == "received:config_value"

    @pytest.mark.asyncio
    @pytest.mark.parametrize("backend", _backend_params())
    async def test_spawn_receives_multiple_env_values(self, backend: str) -> None:
        """Verify that spawned programs can access multiple env values."""
        with _ray_context(backend):
            engine = _build_engine(backend)

            @do
            def worker() -> EffectGenerator[tuple[str, int, bool]]:
                name = yield Ask("name")
                count = yield Ask("count")
                enabled = yield Ask("enabled")
                return name, count, enabled

            @do
            def program() -> EffectGenerator[tuple[str, int, bool]]:
                task = yield Spawn(
                    worker(),
                    preferred_backend=backend,
                    **_ray_task_options(backend),
                )
                return (yield task.join())

            env = {
                "name": "test_service",
                "count": 42,
                "enabled": True,
            }
            result = await engine.run_async(Local(env, program()))

        assert result.is_ok
        assert result.value == ("test_service", 42, True)


class TestNestedSpawnEnvPropagation:
    """Tests for recursive env propagation through nested spawns.

    Note: Inner spawns use thread backend to avoid cloudpickle serialization
    issues when the outer spawn uses process/ray backends. The test module
    isn't importable in subprocess/ray workers.
    """

    @pytest.mark.asyncio
    @pytest.mark.parametrize("backend", _backend_params())
    async def test_nested_spawn_env_propagation_two_levels(self, backend: str) -> None:
        """Verify env propagates through two levels of spawn (A spawns B).

        Program A spawns Program B, and B should have access to env set at root.
        """
        with _ray_context(backend):
            engine = _build_engine(backend)

            @do
            def inner_worker() -> EffectGenerator[str]:
                # Inner worker (level 2) should access env from root
                value = yield Ask("root_config")
                return f"inner:{value}"

            @do
            def outer_worker() -> EffectGenerator[str]:
                # Outer worker (level 1) spawns inner worker
                # Use thread backend for inner spawn to avoid cloudpickle issues
                task = yield Spawn(inner_worker(), preferred_backend="thread")
                inner_result = yield task.join()
                outer_value = yield Ask("root_config")
                return f"outer:{outer_value},{inner_result}"

            @do
            def program() -> EffectGenerator[str]:
                task = yield Spawn(
                    outer_worker(),
                    preferred_backend=backend,
                    **_ray_task_options(backend),
                )
                return (yield task.join())

            result = await engine.run_async(
                Local({"root_config": "from_root"}, program())
            )

        assert result.is_ok, f"Expected success, got error: {result.result}"
        assert result.value == "outer:from_root,inner:from_root"

    @pytest.mark.asyncio
    @pytest.mark.parametrize("backend", _backend_params())
    async def test_nested_spawn_env_propagation_three_levels(self, backend: str) -> None:
        """Verify env propagates through three levels of spawn (A->B->C).

        This tests the acceptance criteria: "Program A spawns B, B spawns C"
        and all should have access to env.
        """
        with _ray_context(backend):
            engine = _build_engine(backend)

            @do
            def level_c() -> EffectGenerator[str]:
                # Level C (deepest) should access env
                value = yield Ask("deep_config")
                return f"C:{value}"

            @do
            def level_b() -> EffectGenerator[str]:
                # Level B spawns C - use thread backend for inner spawns
                task = yield Spawn(level_c(), preferred_backend="thread")
                c_result = yield task.join()
                b_value = yield Ask("deep_config")
                return f"B:{b_value},{c_result}"

            @do
            def level_a() -> EffectGenerator[str]:
                # Level A spawns B - use thread backend for inner spawns
                task = yield Spawn(level_b(), preferred_backend="thread")
                b_result = yield task.join()
                a_value = yield Ask("deep_config")
                return f"A:{a_value},{b_result}"

            @do
            def program() -> EffectGenerator[str]:
                task = yield Spawn(
                    level_a(),
                    preferred_backend=backend,
                    **_ray_task_options(backend),
                )
                return (yield task.join())

            result = await engine.run_async(
                Local({"deep_config": "recursive_value"}, program())
            )

        assert result.is_ok, f"Expected success, got error: {result.result}"
        assert result.value == "A:recursive_value,B:recursive_value,C:recursive_value"


class TestProtocolBasedDependencyInjection:
    """Tests for Protocol-based dependency injection in spawned programs.

    doeff uses Protocol classes as env keys for type-safe dependency injection.
    This tests that such dependencies are accessible in spawned programs.
    """

    @pytest.mark.asyncio
    @pytest.mark.parametrize("backend", _backend_params())
    async def test_protocol_dependency_in_spawned_program(self, backend: str) -> None:
        """Verify Protocol-based dependencies are accessible in spawned programs."""
        with _ray_context(backend):
            engine = _build_engine(backend)

            class ConfigProvider(Protocol):
                def get_value(self) -> str: ...

            class ActualConfigProvider:
                def get_value(self) -> str:
                    return "injected_value"

            @do
            def worker() -> EffectGenerator[str]:
                # Ask for dependency using Protocol as key
                provider: ConfigProvider = yield Ask(ConfigProvider)
                return provider.get_value()

            @do
            def program() -> EffectGenerator[str]:
                task = yield Spawn(
                    worker(),
                    preferred_backend=backend,
                    **_ray_task_options(backend),
                )
                return (yield task.join())

            # Inject implementation using Protocol as key
            result = await engine.run_async(
                Local({ConfigProvider: ActualConfigProvider()}, program())
            )

        assert result.is_ok, f"Expected success, got error: {result.result}"
        assert result.value == "injected_value"

    @pytest.mark.asyncio
    @pytest.mark.parametrize("backend", _backend_params())
    async def test_multiple_protocol_dependencies_in_spawned_program(
        self, backend: str
    ) -> None:
        """Verify multiple Protocol-based dependencies work in spawned programs."""
        with _ray_context(backend):
            engine = _build_engine(backend)

            class Logger(Protocol):
                def log(self, msg: str) -> str: ...

            class Fetcher(Protocol):
                def fetch(self) -> int: ...

            class TestLogger:
                def log(self, msg: str) -> str:
                    return f"logged:{msg}"

            class TestFetcher:
                def fetch(self) -> int:
                    return 42

            @do
            def worker() -> EffectGenerator[tuple[str, int]]:
                logger: Logger = yield Ask(Logger)
                fetcher: Fetcher = yield Ask(Fetcher)
                return logger.log("test"), fetcher.fetch()

            @do
            def program() -> EffectGenerator[tuple[str, int]]:
                task = yield Spawn(
                    worker(),
                    preferred_backend=backend,
                    **_ray_task_options(backend),
                )
                return (yield task.join())

            env = {
                Logger: TestLogger(),
                Fetcher: TestFetcher(),
            }
            result = await engine.run_async(Local(env, program()))

        assert result.is_ok
        assert result.value == ("logged:test", 42)

    @pytest.mark.asyncio
    @pytest.mark.parametrize("backend", _backend_params())
    async def test_protocol_dependency_in_nested_spawn(self, backend: str) -> None:
        """Verify Protocol dependencies propagate through nested spawns.

        Uses thread backend for inner spawn to avoid cloudpickle issues.
        """
        with _ray_context(backend):
            engine = _build_engine(backend)

            class Service(Protocol):
                def process(self, x: int) -> int: ...

            class ActualService:
                def process(self, x: int) -> int:
                    return x * 2

            @do
            def inner_worker() -> EffectGenerator[int]:
                service: Service = yield Ask(Service)
                return service.process(10)

            @do
            def outer_worker() -> EffectGenerator[int]:
                # Use thread backend for inner spawn to avoid cloudpickle issues
                task = yield Spawn(inner_worker(), preferred_backend="thread")
                inner_result = yield task.join()
                service: Service = yield Ask(Service)
                return service.process(inner_result)

            @do
            def program() -> EffectGenerator[int]:
                task = yield Spawn(
                    outer_worker(),
                    preferred_backend=backend,
                    **_ray_task_options(backend),
                )
                return (yield task.join())

            result = await engine.run_async(
                Local({Service: ActualService()}, program())
            )

        assert result.is_ok
        # 10 * 2 = 20 in inner, 20 * 2 = 40 in outer
        assert result.value == 40


class TestEnvPropagationEdgeCases:
    """Edge case tests for env propagation."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize("backend", _backend_params())
    async def test_local_env_override_in_spawned_program(self, backend: str) -> None:
        """Verify that Local inside spawned program can override parent env."""
        with _ray_context(backend):
            engine = _build_engine(backend)

            @do
            def worker() -> EffectGenerator[tuple[str, str]]:
                # First get the inherited value
                inherited = yield Ask("key")

                # Then override with Local and get the new value
                @do
                def inner() -> EffectGenerator[str]:
                    return (yield Ask("key"))

                overridden = yield Local({"key": "overridden"}, inner())
                return inherited, overridden

            @do
            def program() -> EffectGenerator[tuple[str, str]]:
                task = yield Spawn(
                    worker(),
                    preferred_backend=backend,
                    **_ray_task_options(backend),
                )
                return (yield task.join())

            result = await engine.run_async(
                Local({"key": "original"}, program())
            )

        assert result.is_ok
        assert result.value == ("original", "overridden")

    @pytest.mark.asyncio
    @pytest.mark.parametrize("backend", _backend_params())
    async def test_env_isolation_between_parallel_spawns(self, backend: str) -> None:
        """Verify that env is properly isolated between parallel spawns.

        Each spawn should get a snapshot of env at spawn time.
        """
        with _ray_context(backend):
            engine = _build_engine(backend)

            @do
            def worker(worker_id: int) -> EffectGenerator[tuple[int, str]]:
                value = yield Ask("shared_key")
                return worker_id, value

            @do
            def program() -> EffectGenerator[list[tuple[int, str]]]:
                from doeff import Gather

                task1 = yield Spawn(
                    worker(1),
                    preferred_backend=backend,
                    **_ray_task_options(backend),
                )
                task2 = yield Spawn(
                    worker(2),
                    preferred_backend=backend,
                    **_ray_task_options(backend),
                )
                return (yield Gather(task1.join(), task2.join()))

            result = await engine.run_async(
                Local({"shared_key": "shared_value"}, program())
            )

        assert result.is_ok
        assert set(result.value) == {(1, "shared_value"), (2, "shared_value")}

    @pytest.mark.asyncio
    @pytest.mark.parametrize("backend", _backend_params())
    async def test_missing_env_key_raises_in_spawned_program(
        self, backend: str
    ) -> None:
        """Verify that asking for missing env key raises KeyError in spawn."""
        with _ray_context(backend):
            engine = _build_engine(backend)

            @do
            def worker() -> EffectGenerator[str]:
                return (yield Ask("nonexistent_key"))

            @do
            def program() -> EffectGenerator[str]:
                task = yield Spawn(
                    worker(),
                    preferred_backend=backend,
                    **_ray_task_options(backend),
                )
                return (yield task.join())

            result = await engine.run_async(program())

        assert result.is_err
        assert "nonexistent_key" in str(result.result.error)
