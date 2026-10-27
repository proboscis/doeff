"""
Demonstration of doeff marker comments for precise function categorization.

This file shows how to use # doeff: markers to explicitly categorize functions
for better IDE integration and indexing (see docs/MARKERS.md).

Run with: uv run python examples/marker_demo.py
"""

import asyncio
from typing import Any

from doeff_core_effects import ReadWriterLog
from doeff_core_effects.handlers import reader, slog_handler, state, writer
from doeff_core_effects.scheduler import scheduled

from doeff import Ask, Program, Pure, Tell, do, run, slog

# ============================================================================
# INTERPRETERS - Functions that execute/interpret Program objects
# ============================================================================


def simple_interpreter(program: Program):  # doeff: interpreter
    """
    A basic interpreter marked with doeff comment.
    This will be recognized as an interpreter by the IDE plugin.
    """
    return run(program)


def scheduled_interpreter(  # doeff: interpreter
    program: Program, env: dict[str, Any] | None = None
):
    """
    An interpreter with the marker on the function definition line.
    Supports multi-line function signatures.
    """
    return run(scheduled(slog_handler(reader(env=env or {})(program))))


def debug_interpreter(
    program: Program,  # doeff: interpreter
    verbose: bool = False,
    trace: bool = False,
):
    """
    Debug interpreter with marker inline with parameters.
    Provides detailed execution tracking.
    """
    if verbose:
        print(f"Executing program: {program}")
    result = run(slog_handler(program))
    if trace:
        print(f"Result: {result}")
    return result


# ============================================================================
# TRANSFORMERS - Functions that transform Program objects
# ============================================================================


def with_demo_env(  # doeff: transform
    target: Program,
) -> Program:
    """
    A transform function that installs a reader handler around a Program.
    Marked with # doeff: transform for explicit categorization.
    """
    return reader(env={"data_url": "https://api.example.com/data"})(target)


def with_log(
    program: Program,  # doeff: transform
    initial: dict[str, Any] | None = None,
) -> Program:
    """
    Transform that collects Tell messages: writer keeps its log in state.
    The marker helps IDE distinguish this from regular functions.
    """
    return state(initial=initial)(writer(program))


@do
def chain_transform(  # doeff: transform
    *programs: Program,
):
    """
    Chains multiple programs together and returns the last result.
    Variadic arguments with transform marker.
    """
    result = None
    for prog in programs:
        result = yield prog
    return result


# ============================================================================
# KLEISLI FUNCTIONS - Composable effect-handling functions
# ============================================================================


@do
def fetch_data():  # doeff: kleisli
    """
    A Kleisli function for fetching data.
    Marked explicitly as kleisli for better IDE support.
    """
    url = yield Ask("data_url")
    yield slog(msg=f"fetching {url}")
    return {"a": 1, "b": 2.5, "label": "sample"}


@do
def process_data(  # doeff: kleisli
    data: dict[str, Any],
):
    """
    Processes fetched data in a Kleisli composition.
    Multi-line signature with kleisli marker.
    """
    processed = {k: v * 2 for k, v in data.items() if isinstance(v, (int, float))}
    yield Tell(f"Processed {len(processed)} items")
    return processed


@do
def save_results(
    results: dict[str, Any],  # doeff: kleisli
    path: str = "/tmp/results.json",
):
    """
    Records where the results would be saved, as part of a Kleisli chain.
    Marker placed with parameters for clarity.
    """
    yield Tell(f"would write {len(results)} results to {path}")
    return path


# ============================================================================
# MIXED MARKERS - Functions with multiple roles
# ============================================================================


@do
def hybrid_function(  # doeff: kleisli, transform
    program: Program,
):
    """
    A function that acts as both a Kleisli function and a transformer.
    Multiple markers can be specified comma-separated.
    """
    transformed = yield program
    yield Tell("Transformation complete")
    return transformed


# ============================================================================
# UNMARKED FUNCTIONS - Rely on type analysis for detection
# ============================================================================


def unmarked_interpreter(program: Program):
    """
    An interpreter without explicit marker.
    Will be detected by parameter type analysis as fallback.
    """
    return run(program)


@do
def unmarked_kleisli():
    """
    A Kleisli function without marker.
    Detected by @do decorator when no markers present.
    """
    value = yield Pure(42)
    return value


def regular_function(x: int, y: int) -> int:
    """
    A regular function that won't be indexed by doeff.
    No Program parameter, no @do decorator, no markers.
    """
    return x + y


# ============================================================================
# COMPLEX EXAMPLES
# ============================================================================


class ProgramExecutor:
    """Class containing methods with doeff markers."""

    def execute(self, program: Program):  # doeff: interpreter
        """Class method interpreter with marker."""
        return run(self.transform(program))

    def transform(  # doeff: transform
        self, program: Program
    ) -> Program:
        """Class method transformer with marker."""
        return reader(env=self.get_context())(program)

    @staticmethod
    @do
    def static_kleisli():  # doeff: kleisli
        """Static method Kleisli function with marker."""
        executor = yield Ask("executor")
        return f"run by {executor}"

    def get_context(self) -> dict[str, Any]:
        """Regular method without doeff functionality."""
        return {"executor": self.__class__.__name__}


# ============================================================================
# EDGE CASES AND SPECIAL PATTERNS
# ============================================================================


def interpreter_with_comment(
    program: Program,  # This is a regular comment, not a marker
):  # doeff: interpreter
    """
    Shows that only special marker comments are recognized.
    Other comments are ignored by the indexer.
    """
    return run(program)


async def async_marked_interpreter(  # doeff: interpreter
    program: Program,
):
    """
    Async function with doeff marker.
    The marker system works with async functions too.
    """
    return await asyncio.to_thread(run, program)


def factory_interpreter(config: dict[str, Any]):  # doeff: interpreter
    """
    A factory that returns an interpreter function.
    The marker indicates this factory produces interpreters.
    """

    def inner(program: Program):
        return run(reader(env=config)(program))

    return inner


# ============================================================================
# USAGE EXAMPLES
# ============================================================================


@do
def data_pipeline():
    """fetch -> process -> save, with the Tell log read back at the end."""
    data = yield fetch_data()
    processed = yield hybrid_function(process_data(data))
    path = yield save_results(processed)
    return {"processed": processed, "path": path, "log": (yield ReadWriterLog())}


if __name__ == "__main__":
    # Create a sample program
    prog = Pure(42)

    # Use marked interpreters
    print(f"Simple interpreter result: {simple_interpreter(prog)}")
    print(f"Debug interpreter result: {debug_interpreter(unmarked_kleisli())}")
    print(f"Unmarked interpreter result: {unmarked_interpreter(prog)}")
    print(f"Interpreter with comment: {interpreter_with_comment(prog)}")
    print(f"Async interpreter result: {asyncio.run(async_marked_interpreter(prog))}")
    configured = factory_interpreter({"executor": "factory_interpreter"})
    print(f"Factory interpreter result: {configured(ProgramExecutor.static_kleisli())}")

    # Use marked transformers and Kleisli functions together
    pipeline = with_demo_env(with_log(data_pipeline()))
    print(f"Pipeline result: {scheduled_interpreter(pipeline)}")
    print(f"Chained programs: {simple_interpreter(chain_transform(Pure(1), Pure(2), prog))}")

    # Use class-based executor
    executor = ProgramExecutor()
    print(f"Executor result: {executor.execute(ProgramExecutor.static_kleisli())}")
    print(f"Regular function: {regular_function(1, 2)}")

    print("\nMarker demonstration complete!")
    print("The IDE plugin will recognize and categorize these functions")
    print("based on their # doeff: markers for better navigation and execution.")
