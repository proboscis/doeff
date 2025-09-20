"""
Demonstration of doeff marker comments for precise function categorization.

This file shows how to use # doeff: markers to explicitly categorize functions
for better IDE integration and indexing.
"""

from doeff import Program, do
from typing import Any, Dict, Optional


# ============================================================================
# INTERPRETERS - Functions that execute/interpret Program objects
# ============================================================================

def simple_interpreter(program: Program):  # doeff: interpreter
    """
    A basic interpreter marked with doeff comment.
    This will be recognized as an interpreter by the IDE plugin.
    """
    return program.run()


def async_interpreter(  # doeff: interpreter
    program: Program,
    timeout: Optional[float] = None
):
    """
    An async interpreter with the marker on the function definition line.
    Supports multi-line function signatures.
    """
    import asyncio
    return asyncio.run(program.async_run(timeout=timeout))


def debug_interpreter(
    program: Program,  # doeff: interpreter
    verbose: bool = False,
    trace: bool = False
):
    """
    Debug interpreter with marker inline with parameters.
    Provides detailed execution tracking.
    """
    if verbose:
        print(f"Executing program: {program}")
    result = program.run()
    if trace:
        print(f"Result: {result}")
    return result


# ============================================================================
# TRANSFORMERS - Functions that transform Program objects
# ============================================================================

@do
def optimize_transform(  # doeff: transform
    target: Program
) -> Program:
    """
    A transform function that optimizes a Program.
    Marked with # doeff: transform for explicit categorization.
    """
    # Optimization logic here
    return target.optimize()


@do
def parallelize_transform(
    program: Program,  # doeff: transform
    num_workers: int = 4
) -> Program:
    """
    Transform that parallelizes program execution.
    The marker helps IDE distinguish this from regular functions.
    """
    return program.parallelize(num_workers)


def chain_transform(  # doeff: transform
    *programs: Program
) -> Program:
    """
    Chains multiple programs together.
    Variadic arguments with transform marker.
    """
    result = programs[0]
    for prog in programs[1:]:
        result = result.chain(prog)
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
    yield Effect("fetch", url="https://api.example.com/data")


@do
def process_data(  # doeff: kleisli
    data: Dict[str, Any]
):
    """
    Processes fetched data in a Kleisli composition.
    Multi-line signature with kleisli marker.
    """
    processed = {k: v * 2 for k, v in data.items() if isinstance(v, (int, float))}
    yield Effect("log", message=f"Processed {len(processed)} items")
    return processed


@do
def save_results(
    results: Dict[str, Any],  # doeff: kleisli
    path: str = "/tmp/results.json"
):
    """
    Saves results to disk as part of Kleisli chain.
    Marker placed with parameters for clarity.
    """
    import json
    yield Effect("write_file", path=path, content=json.dumps(results))
    return path


# ============================================================================
# MIXED MARKERS - Functions with multiple roles
# ============================================================================

@do
def hybrid_function(  # doeff: kleisli, transform
    program: Program
):
    """
    A function that acts as both a Kleisli function and a transformer.
    Multiple markers can be specified comma-separated.
    """
    transformed = yield program.transform()
    yield Effect("log", message="Transformation complete")
    return transformed


# ============================================================================
# UNMARKED FUNCTIONS - Rely on type analysis for detection
# ============================================================================

def unmarked_interpreter(program: Program):
    """
    An interpreter without explicit marker.
    Will be detected by parameter type analysis as fallback.
    """
    return program.run()


@do
def unmarked_kleisli():
    """
    A Kleisli function without marker.
    Detected by @do decorator when no markers present.
    """
    yield Effect("action", value=42)


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
        return program.run()
    
    @do
    def transform(  # doeff: transform
        self,
        program: Program
    ) -> Program:
        """Class method transformer with marker."""
        return program.with_context(self.get_context())
    
    @staticmethod
    @do
    def static_kleisli():  # doeff: kleisli
        """Static method Kleisli function with marker."""
        yield Effect("static_action")
    
    def get_context(self) -> Dict[str, Any]:
        """Regular method without doeff functionality."""
        return {"executor": self.__class__.__name__}


# ============================================================================
# EDGE CASES AND SPECIAL PATTERNS
# ============================================================================

def interpreter_with_comment(
    program: Program  # This is a regular comment, not a marker
):  # doeff: interpreter - This IS the marker
    """
    Shows that only comments starting with '# doeff:' are markers.
    Other comments are ignored by the indexer.
    """
    return program.run()


async def async_marked_interpreter(  # doeff: interpreter
    program: Program
):
    """
    Async function with doeff marker.
    The marker system works with async functions too.
    """
    return await program.async_run()


def factory_interpreter(
    config: Dict[str, Any]
):  # doeff: interpreter
    """
    A factory that returns an interpreter function.
    The marker indicates this factory produces interpreters.
    """
    def inner(program: Program):
        return program.run_with_config(config)
    return inner


# ============================================================================
# USAGE EXAMPLES
# ============================================================================

if __name__ == "__main__":
    # Example usage of marked functions
    from doeff import Program, Effect
    
    # Create a sample program
    prog = Program.of(lambda: 42)
    
    # Use marked interpreter
    result = simple_interpreter(prog)
    print(f"Simple interpreter result: {result}")
    
    # Use marked transformer
    optimized = optimize_transform(prog)
    print(f"Optimized program: {optimized}")
    
    # Use class-based executor
    executor = ProgramExecutor()
    executor.execute(prog)
    
    print("\nMarker demonstration complete!")
    print("The IDE plugin will recognize and categorize these functions")
    print("based on their # doeff: markers for better navigation and execution.")