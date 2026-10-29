"""Sample doeff entrypoints for the CLI and the IDE plugins (doeff.nvim and others).

In doeff, an ENTRYPOINT is a global variable with type Program[T].
The behavior is fixed at definition time - no runtime args or env vars.

Run one from the repository root with:

    uv run doeff run --program examples.sample_entrypoints.hello_world_program

Entrypoints that Ask for services need SAMPLE_ENV. `--hy` runs the Program its last form
returns without adding handlers, so install them with `with_sample_env`:

    uv run doeff run --hy '(import examples.sample_entrypoints [fetch_user_1_program
        with_sample_env]) (with_sample_env fetch_user_1_program)'

Run every entrypoint at once with: uv run python examples/sample_entrypoints.py
"""

from pathlib import Path
from typing import Protocol, runtime_checkable

from doeff_core_effects import reader, scheduled, slog_handler, state, try_handler, writer

from doeff import Ask, EffectGenerator, Gather, Program, Spawn, do, run, slog

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
    """Fetch user data. Services are injected via Ask."""
    source = yield Ask("user_source")  # Service injection is OK
    yield slog(msg=f"Fetching user {user_id} from {source}")
    return {"id": user_id, "name": f"User {user_id}"}


@do
def process_item(item: dict) -> EffectGenerator[dict]:
    """Process a single item."""
    yield slog(msg=f"Processing item: {item}")
    return {"processed": True, **item}


@do
def process_items(items: list[dict]) -> EffectGenerator[list[dict]]:
    """Process multiple items concurrently: Spawn a task per item, then Gather them."""
    tasks = []
    for item in items:
        tasks = [*tasks, (yield Spawn(process_item(item)))]
    return list((yield Gather(*tasks)))


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


@do
def metrics_pipeline(path: Path) -> EffectGenerator[dict]:
    """load -> process -> compute metrics. Each step's value is taken with `yield`."""
    loaded = yield load_data(path=path)
    processed = yield process_items(items=loaded)
    return (yield compute_metrics(data=processed))


# ============================================================
# PROGRAM ENTRYPOINTS - Global variables with fixed behavior
# These are what you run with `uv run doeff run --program ...`
# ============================================================

# Simple entrypoint - greet with fixed name
hello_world_program: Program[str] = greet(name="World")

# Another instance with different fixed parameter
hello_doeff_program: Program[str] = greet(name="Doeff")

# Fetch a specific user (behavior is fixed; the user source comes from the env)
fetch_user_1_program: Program[dict] = fetch_user(user_id=1)
fetch_user_42_program: Program[dict] = fetch_user(user_id=42)

# Pipeline: load -> process -> compute metrics
sample_data_path: Path = Path("data/sample.json")
full_pipeline_program: Program[dict] = metrics_pipeline(path=sample_data_path)


# ============================================================
# PLAIN FUNCTIONS IN PROGRAMS
# ============================================================


def double(x: int) -> int:
    """Pure function - no effects needed."""
    return x * 2


def format_result(value: int) -> str:
    """Format a numeric result."""
    return f"Result: {value}"


@do
def doubled(x: int) -> EffectGenerator[int]:
    """A @do function may simply return a value: calling it builds a Program."""
    return double(x)


@do
def formatted(program: Program[int]) -> EffectGenerator[str]:
    """Run another Program with `yield`, then pass its value to a plain function."""
    return format_result((yield program))


double_10_program: Program[int] = doubled(x=10)
double_100_program: Program[int] = doubled(x=100)
formatted_result: Program[str] = formatted(program=double_10_program)


# ============================================================
# PROTOCOL-BASED INJECTION EXAMPLE
# ============================================================


@runtime_checkable
class ProcessorFn(Protocol):
    """Injection point for item processor."""

    def __call__(self, item: dict) -> dict: ...


def mark_processed(item: dict) -> dict:
    """The ProcessorFn that SAMPLE_ENV injects."""
    return {**item, "processed": True}


@do
def process_with_strategy(items: list[dict]) -> EffectGenerator[list[dict]]:
    """Process items using injected strategy."""
    processor: ProcessorFn = yield Ask(ProcessorFn)
    results = [processor(item) for item in items]
    return results


# Entrypoint that uses protocol-based injection
strategy_pipeline: Program[list[dict]] = process_with_strategy(
    items=[{"id": 1}, {"id": 2}, {"id": 3}]
)


# ============================================================
# INTERPRETER - runs an entrypoint with the services it Asks for
# ============================================================

SAMPLE_ENV = {"user_source": "in-memory sample users", ProcessorFn: mark_processed}


def with_sample_env(program: Program) -> Program:  # doeff: transform
    """Install SAMPLE_ENV and the core handlers, under the scheduler."""
    program = slog_handler(program)
    program = try_handler(program)
    program = writer(program)
    program = state()(program)
    program = reader(env=SAMPLE_ENV)(program)
    return scheduled()(program)


def sample_interpreter(program: Program):  # doeff: interpreter, default
    """Run a Program of this module with SAMPLE_ENV and the core handlers."""
    return run(with_sample_env(program))


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


ENTRYPOINT_NAMES = (
    "hello_world_program",
    "hello_doeff_program",
    "fetch_user_1_program",
    "fetch_user_42_program",
    "full_pipeline_program",
    "double_10_program",
    "double_100_program",
    "formatted_result",
    "strategy_pipeline",
)


if __name__ == "__main__":
    print("Sample doeff entrypoints, each run through sample_interpreter:")
    for name in ENTRYPOINT_NAMES:
        # Looked up by name, as `doeff run --program examples.sample_entrypoints.<name>` does.
        print(f"  {name} -> {sample_interpreter(globals()[name])!r}", flush=True)
    print(
        "  with_logging(hello_world_program) -> "
        f"{sample_interpreter(with_logging(hello_world_program))!r}",
        flush=True,
    )
    print("\nRun one with: uv run doeff run --program examples.sample_entrypoints.<name>")
