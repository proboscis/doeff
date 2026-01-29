"""Sample doeff entrypoints for testing doeff.nvim plugin.

In doeff, an ENTRYPOINT is a global variable with type Program[T].
The behavior is fixed at definition time - no runtime args or env vars.

Run with: uv run doeff run --program examples.sample_entrypoints.hello_world_program
"""

from pathlib import Path
from typing import Protocol, runtime_checkable

from doeff import EffectGenerator, Program, do
from doeff.effects import ask, gather
from doeff.effects.log import slog

# ============================================================
# KLEISLI FUNCTIONS - Building blocks that create Programs
# ============================================================

@do
def greet(name: str) -> EffectGenerator[str]:
    """Greet someone by name."""
    yield slog(msg=f"Greeting {name}...")
    return f"Hello, {name}!"


@do
def fetch_user(user_id: int) -> EffectGenerator[dict]:
    """Fetch user data. Services are injected via ask."""
    logger = yield ask("logger")  # Service injection is OK
    yield slog(msg=f"Fetching user {user_id}")
    return {"id": user_id, "name": f"User {user_id}"}


@do
def process_item(item: dict) -> EffectGenerator[dict]:
    """Process a single item."""
    yield slog(msg=f"Processing item: {item}")
    return {"processed": True, **item}


@do
def process_items(items: list[dict]) -> EffectGenerator[list[dict]]:
    """Process multiple items using gather."""
    @do
    def process_single(item: dict) -> EffectGenerator[dict]:
        return (yield process_item(item))

    programs = [process_single(item) for item in items]
    results = list((yield gather(*programs)))
    return results


@do
def load_data(path: Path) -> EffectGenerator[list[dict]]:
    """Load data from a path. Only data loading functions should accept paths."""
    yield slog(msg=f"Loading data from {path}")
    # Simulated data loading
    return [{"id": 1, "value": "a"}, {"id": 2, "value": "b"}]


@do
def compute_metrics(data: list[dict]) -> EffectGenerator[dict]:
    """Compute metrics from data. Pure processing - no paths."""
    yield slog(msg=f"Computing metrics for {len(data)} items")
    return {
        "count": len(data),
        "ids": [d["id"] for d in data],
    }


# ============================================================
# PROGRAM ENTRYPOINTS - Global variables with fixed behavior
# These are what you run with `uv run doeff run --program ...`
# ============================================================

# Simple entrypoint - greet with fixed name
hello_world_program: Program[str] = greet(name="World")

# Another instance with different fixed parameter
hello_doeff_program: Program[str] = greet(name="Doeff")

# Fetch a specific user (behavior is fixed)
fetch_user_1_program: Program[dict] = fetch_user(user_id=1)
fetch_user_42_program: Program[dict] = fetch_user(user_id=42)

# Pipeline: load -> process -> compute metrics
# Each step is a Program variable that can be composed
sample_data_path: Path = Path("data/sample.json")
loaded_data: Program[list[dict]] = load_data(path=sample_data_path)
processed_data: Program[list[dict]] = process_items(items=loaded_data)
data_metrics: Program[dict] = compute_metrics(data=processed_data)

# The top-level pipeline entrypoint
full_pipeline_program: Program[dict] = data_metrics


# ============================================================
# USING Program.pure FOR SIMPLE FUNCTIONS
# ============================================================

def double(x: int) -> int:
    """Pure function - no effects needed."""
    return x * 2


def format_result(value: int) -> str:
    """Format a numeric result."""
    return f"Result: {value}"


# Create Program constants from pure functions
double_10_program: Program[int] = Program.pure(double)(x=10)
double_100_program: Program[int] = Program.pure(double)(x=100)

# Chain with Program proxy feature
formatted_result: Program[str] = Program.pure(format_result)(value=double_10_program)


# ============================================================
# PROTOCOL-BASED INJECTION EXAMPLE
# ============================================================

@runtime_checkable
class ProcessorFn(Protocol):
    """Injection point for item processor."""
    def __call__(self, item: dict) -> dict: ...


@do
def process_with_strategy(items: list[dict]) -> EffectGenerator[list[dict]]:
    """Process items using injected strategy."""
    processor: ProcessorFn = yield ask(ProcessorFn)
    results = [processor(item) for item in items]
    return results


# Entrypoint that uses protocol-based injection
strategy_pipeline: Program[list[dict]] = process_with_strategy(
    items=[{"id": 1}, {"id": 2}, {"id": 3}]
)


# ============================================================
# TRANSFORMS - Program -> Program modifications
# ============================================================

@do
def with_logging(  # doeff: transform
    program: Program,
) -> EffectGenerator:
    """Add logging around program execution."""
    yield slog(msg="=== Starting ===")
    result = yield program
    yield slog(msg=f"=== Completed: {result} ===")
    return result


@do
def with_retry(  # doeff: transform
    program: Program,
    max_attempts: int = 3,
) -> EffectGenerator:
    """Add retry logic to program execution."""
    for attempt in range(max_attempts):
        try:
            return (yield program)
        except Exception as e:
            yield slog(msg=f"Attempt {attempt + 1} failed: {e}")
            if attempt == max_attempts - 1:
                raise
    raise RuntimeError("Should not reach here")


# ============================================================
# KLEISLI TOOLS - Post-processing tools for IDE
# ============================================================

@do
def visualize_metrics(  # doeff: kleisli
    metrics: dict,
) -> EffectGenerator[None]:
    """Visualize computed metrics. Marked as kleisli tool for IDE."""
    yield slog(msg="=== Metrics Visualization ===")
    for key, value in metrics.items():
        yield slog(msg=f"  {key}: {value}")
    yield slog(msg="=============================")


@do
def export_to_json(  # doeff: kleisli
    data: dict,
    output_path: Path,
) -> EffectGenerator[Path]:
    """Export data to JSON file. Kleisli tool for IDE."""
    import json
    yield slog(msg=f"Exporting to {output_path}")
    output_path.write_text(json.dumps(data, indent=2))
    return output_path


if __name__ == "__main__":
    print("Sample doeff entrypoints for testing doeff.nvim")
    print("\nProgram Entrypoints (global variables):")
    print("  - hello_world_program")
    print("  - hello_doeff_program")
    print("  - fetch_user_1_program")
    print("  - fetch_user_42_program")
    print("  - full_pipeline_program")
    print("  - double_10_program")
    print("  - formatted_result")
    print("\nRun with: uv run doeff run --program examples.sample_entrypoints.<name>")
