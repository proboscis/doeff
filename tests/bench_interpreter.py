"""Benchmark coverage for the trampolined interpreter.

These tests require pytest-benchmark. Skip if the plugin is unavailable.
"""

from __future__ import annotations

import pytest

pytest.importorskip("pytest_benchmark")

from doeff.interpreter_v2 import TrampolinedInterpreter
from doeff.program import Program


def _build_chain(depth: int) -> Program[int]:
    program: Program[int] = Program.pure(0)
    for _ in range(depth):
        program = program.flat_map(lambda value: Program.pure(value + 1))
    return program


@pytest.mark.benchmark
def test_shallow_effects(benchmark) -> None:
    engine = TrampolinedInterpreter()
    program = _build_chain(100)
    benchmark(lambda: engine.run(program))


@pytest.mark.benchmark
def test_deep_flat_map(benchmark) -> None:
    engine = TrampolinedInterpreter()
    program = _build_chain(1000)
    benchmark(lambda: engine.run(program))
