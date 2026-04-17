"""Shared workload definitions for doeff-vm benchmarks."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

import doeff_vm

from doeff import Program, default_handlers, do, run
from doeff.effects import Ask, Get, Put, Tell


@dataclass(frozen=True)
class CallableBenchmarkCase:
    name: str
    runner: str
    workload: str
    expected_value: int
    invoke: Callable[[], int]


@dataclass(frozen=True)
class WorkloadSpec:
    workload: str
    expected_value: int
    env: dict[object, object]
    program_factory: Callable[[], Program[int]]


def _wrap_handlers(program: Program[int], *handlers: object) -> Program[int]:
    wrapped = program
    for handler in reversed(handlers):
        wrapped = doeff_vm.WithHandler(handler, wrapped)
    return wrapped


@do
def _pure_program() -> Program[int]:
    return 42
    yield


@do
def _state_program(iterations: int) -> Program[int]:
    yield Put("counter", 0)
    total = 0
    for _ in range(iterations):
        current = yield Get("counter")
        total += current
        yield Put("counter", current + 1)
    return total


@do
def _state_writer_program(iterations: int) -> Program[int]:
    seed = yield Ask("seed")
    yield Put("counter", seed)
    total = 0
    for index in range(iterations):
        current = yield Get("counter")
        yield Tell(f"iteration:{index}")
        total += current
        yield Put("counter", current + 1)
    return total


def build_workload_specs(iterations: int) -> list[WorkloadSpec]:
    return [
        WorkloadSpec(
            workload="pure",
            expected_value=42,
            env={},
            program_factory=_pure_program,
        ),
        WorkloadSpec(
            workload="state",
            expected_value=iterations * (iterations - 1) // 2,
            env={},
            program_factory=lambda: _state_program(iterations),
        ),
        WorkloadSpec(
            workload="state_writer",
            expected_value=iterations + (iterations * (iterations - 1) // 2),
            env={"seed": 1},
            program_factory=lambda: _state_writer_program(iterations),
        ),
    ]


def build_public_benchmark_cases(iterations: int) -> list[CallableBenchmarkCase]:
    cases: list[CallableBenchmarkCase] = []
    for spec in build_workload_specs(iterations):
        env = dict(spec.env)

        def invoke(
            program_factory: Callable[[], Program[int]] = spec.program_factory,
            env_values: dict[object, object] = env,
        ) -> int:
            return run(
                program_factory(),
                handlers=default_handlers(),
                env=dict(env_values),
            ).value

        cases.append(
            CallableBenchmarkCase(
                name=f"public_run:{spec.workload}",
                runner="public_run",
                workload=spec.workload,
                expected_value=spec.expected_value,
                invoke=invoke,
            )
        )
    return cases


def build_raw_vm_benchmark_cases(iterations: int) -> list[CallableBenchmarkCase]:
    cases: list[CallableBenchmarkCase] = []
    for spec in build_workload_specs(iterations):
        env = dict(spec.env)

        def module_invoke(
            program_factory: Callable[[], Program[int]] = spec.program_factory,
            env_values: dict[object, object] = env,
        ) -> int:
            wrapped = _wrap_handlers(program_factory(), *default_handlers())
            return doeff_vm.run(wrapped, env=dict(env_values)).value

        def pyvm_invoke(
            program_factory: Callable[[], Program[int]] = spec.program_factory,
            env_values: dict[object, object] = env,
        ) -> int:
            vm = doeff_vm.PyVM()
            for key, value in env_values.items():
                vm.put_env(key, value)
            return vm.run(_wrap_handlers(program_factory(), *default_handlers()))

        cases.extend(
            [
                CallableBenchmarkCase(
                    name=f"module_run:{spec.workload}",
                    runner="module_run",
                    workload=spec.workload,
                    expected_value=spec.expected_value,
                    invoke=module_invoke,
                ),
                CallableBenchmarkCase(
                    name=f"pyvm_fresh:{spec.workload}",
                    runner="pyvm_fresh",
                    workload=spec.workload,
                    expected_value=spec.expected_value,
                    invoke=pyvm_invoke,
                ),
            ]
        )
    return cases


def benchmark_case_names(iterations: int, *, include_raw_vm: bool = False) -> list[str]:
    cases = build_public_benchmark_cases(iterations)
    if include_raw_vm:
        cases.extend(build_raw_vm_benchmark_cases(iterations))
    return [case.name for case in cases]


def benchmark_case_map(iterations: int, *, include_raw_vm: bool = False) -> dict[str, Callable[[], int]]:
    cases = build_public_benchmark_cases(iterations)
    if include_raw_vm:
        cases.extend(build_raw_vm_benchmark_cases(iterations))
    return {case.name: case.invoke for case in cases}
