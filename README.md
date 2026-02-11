# doeff - Algebraic Effects for Python

An algebraic effects system with one-shot continuations, backed by a Rust VM. Uses generators for do-notation and ships with batteries-included effect handlers: Reader, State, Writer, Future, Result, IO, Cache, and more.

## Documentation

**[📚 Full Documentation](docs/index.md)** - Comprehensive guides, tutorials, and API reference

- [Getting Started](docs/01-getting-started.md) - Installation and first program
- [Core Concepts](docs/02-core-concepts.md) - Understanding Program and Effect
- [Effect Guides](docs/index.md#effect-types) - Detailed guides for all effect types
- [API Reference](docs/13-api-reference.md) - Complete API documentation
- [Program Architecture Overview](docs/program-architecture-overview.md) - Runtime design and interpreter internals

## Features

- **Algebraic effects with one-shot continuations**: Effects are first-class operations handled by composable handlers
- **Rust VM runtime**: High-performance interpreter for effect handling and continuation management
- **Generator-based do-notation**: Write effectful code that looks like regular Python
- **Batteries-included handlers**: Reader, State, Writer, Future, Result, IO, Cache, and more — ready to use
- **Stack-safe execution**: Trampolining prevents stack overflow in deeply nested computations
- **Pinjected integration**: Optional bridge to pinjected dependency injection framework
- **Type hints**: Full type annotation support with `.pyi` files
- **Python 3.10+**: Modern Python with full async/await support

## Installation

```bash
pip install doeff
```

For pinjected integration:
```bash
pip install doeff-pinjected
```

## Quick Start

```python
from doeff import do, Program, Put, Get, Tell, run

@do
def counter_program() -> Program[int]:
    yield Put("counter", 0)
    yield Tell("Starting computation")
    count = yield Get("counter")
    yield Put("counter", count + 1)
    return count + 1

result = run(counter_program())
print(result.value)  # 1
```

## Runtimes

doeff now uses the Rust VM entrypoints:

| Entrypoint | Use Case |
|------------|----------|
| `run(program, handlers=None, env=None, store=None)` | Synchronous execution |
| `arun(program, handlers=None, env=None, store=None)` | Async execution with event-loop yielding |

Default handlers are installed automatically (`state`, `reader`, `writer`).

## Migration from ProgramInterpreter

`ProgramInterpreter` is deprecated in favor of the new runtime system. Here's how to migrate:

| ProgramInterpreter (deprecated) | AsyncRuntime (new) |
|--------------------------------|---------------------|
| `engine = ProgramInterpreter()` | `runtime = AsyncRuntime()` |
| `engine.run(program)` | `runtime.run(program)` (async) |
| `await engine.run_async(program)` | `await runtime.run(program)` |
| `result.context.state` | Direct value return |

## Effects

### State (Get, Put, Modify)
```python
@do
def stateful_computation():
    value = yield Get("key")
    yield Put("key", value + 10)
    yield Modify("counter", lambda x: x + 1)
```

### Reader (Ask, Local)
```python
@do
def with_config():
    config = yield Ask("database_url")
    result = yield Local({"timeout": 30}, sub_program())
    return result
```

### Writer (Log, Tell, Listen)
```python
@do
def with_logging():
    yield Log("Starting operation")
    result = yield computation()
    yield Tell(["Additional", "messages"])
    return result
```

### Result (Safe)
```python
@do
def with_error_handling():
    safe_result = yield Safe(risky_operation())
    if safe_result.is_ok():
        return safe_result.value
    else:
        return f"Failed: {safe_result.error}"
```

### Future (Await, Parallel)
```python
@do
def async_operations():
    result1 = yield Await(async_function_1())
    results = yield Parallel([
        async_function_2(),
        async_function_3()
    ])
    return (result1, results)
```

### Cache (CacheGet, CachePut)
```python
@do
def cached_call():
    try:
        return (yield CacheGet("expensive"))
    except KeyError:
        value = yield do_expensive_work()
        yield CachePut("expensive", value, ttl=60)
        return value
```

See `docs/cache.md` for accepted policy fields (`ttl`, lifecycle/storage hints, metadata) and the
behaviour of the bundled sqlite-backed handler.

## Pinjected Integration

```python
from doeff import do, Ask
from doeff_pinjected import program_to_injected

@do
def service_program():
    db = yield Ask("database")
    cache = yield Ask("cache")
    result = yield process_data(db, cache)
    return result

# Convert to pinjected Injected
injected = program_to_injected(service_program())

# Use with pinjected's dependency injection
from pinjected import design

bindings = design(
    database=Database(),
    cache=Cache()
)
result = await bindings.provide(injected)
```

## CLI Auto-Discovery

The `doeff` CLI can automatically discover default interpreters and environments based on markers in your code, eliminating the need to specify them manually.

**📖 [Full CLI Auto-Discovery Guide](docs/14-cli-auto-discovery.md)** - Comprehensive documentation with examples, troubleshooting, and best practices.

### Quick Example

```bash
# Auto-discovers interpreter and environments
doeff run --program myapp.features.auth.login_program

# Equivalent to:
doeff run --program myapp.features.auth.login_program \
  --interpreter myapp.features.auth.auth_interpreter \
  --env myapp.base_env \
  --env myapp.features.features_env \
  --env myapp.features.auth.auth_env
```

### Marking Default Interpreters

Add `# doeff: interpreter, default` marker to your interpreter function:

```python
def my_interpreter(prog: Program[Any]) -> Any:
    """
    Custom interpreter for myapp.
    # doeff: interpreter, default
    """
    from doeff.runtimes import SyncRuntime
    runtime = SyncRuntime()
    return runtime.run(prog)
```

**Discovery Rules:**
- CLI searches from program module up to root
- Selects the **closest** interpreter in the module hierarchy
- Explicit `--interpreter` overrides auto-discovery

### Marking Default Environments

Add `# doeff: default` marker above environment variables:

```python
# doeff: default
base_env: Program[dict] = Program.pure({
    'db_host': 'localhost',
    'api_key': 'xxx',
    'timeout': 10
})
```

**Accumulation Rules:**
- CLI discovers **all** environments in hierarchy (root → program)
- Later values override earlier values
- Environments are merged automatically

### Example Structure

```
myapp/
  __init__.py          # base_interpreter, base_env
  features/
    __init__.py        # features_env (overrides base)
    auth/
      __init__.py      # auth_interpreter (closer), auth_env
      login.py         # login_program uses discovered resources
```

When running `doeff run --program myapp.features.auth.login.login_program`:
1. Discovers `auth_interpreter` (closest match)
2. Discovers and merges: `base_env` → `features_env` → `auth_env`
3. Injects merged environment into program
4. Executes with discovered interpreter

### Debugging and Profiling

Profiling and discovery logging is **enabled by default**. To disable it, use the `DOEFF_DISABLE_PROFILE` environment variable:

```bash
export DOEFF_DISABLE_PROFILE=1
doeff run --program myapp.features.auth.login.login_program
```

When enabled, profiling shows:
- **Performance metrics**: Time spent on indexing, discovery, symbol loading, and execution
- **Discovery details**: Which interpreter and environments were discovered and selected
- **Symbol loading**: Which symbols are being imported and when

Example output:
```
[DOEFF][PROFILE] Profiling enabled. To disable, set: export DOEFF_DISABLE_PROFILE=1
[DOEFF][PROFILE]   Import doeff_indexer: 3.45ms
[DOEFF][PROFILE]   Initialize discovery services: 3.48ms
[DOEFF][PROFILE]   Find default interpreter: 74.74ms
[DOEFF][DISCOVERY] Interpreter: myapp.features.auth.auth_interpreter
[DOEFF][PROFILE]   Find default environments: 57.51ms
[DOEFF][DISCOVERY] Environments (3):
[DOEFF][DISCOVERY]   - myapp.base_env
[DOEFF][DISCOVERY]   - myapp.features.features_env
[DOEFF][DISCOVERY]   - myapp.features.auth.auth_env
[DOEFF][PROFILE]   Merge environments: 0.13ms
[DOEFF][PROFILE]   Load and run interpreter: 0.83ms
[DOEFF][PROFILE] CLI discovery and execution: 141.23ms
```

Profiling output goes to **stderr**, so it won't interfere with JSON output or stdout.

### RunResult Reports & Effect Call Tree

Use `--report` to print the annotated `RunResult.display()` output after command execution. The report includes:

- final status (success/error)
- captured logs, state, and environment
- the **effect call tree** showing which `@do` functions produced each effect
- (with `--report-verbose`) the full creation stack traces and verbose sections

```bash
doeff run --program myapp.features.auth.login.login_program --report
```

For JSON output the report and call tree appear as additional fields when `--report` is provided:

```bash
doeff run --program myapp.features.auth.login.login_program --format json --report
```

This returns:

```json
{
  "status": "ok",
  "result": "Login via oauth2 (timeout: 10s)",
  "report": "... RunResult report ...",
  "call_tree": "outer()\n└─ inner()\n   └─ Ask('value')"
}
```

## Development

```bash
# Clone the repository
git clone https://github.com/proboscis/doeff.git
cd doeff

# Install with development dependencies
uv sync --group dev

# Run tests
uv run pytest

# Run type checking
uv run pyright

# Run linting
uv run ruff check
```

## License

MIT License - see LICENSE file for details.

## Credits

Originally extracted from the `sge-hub` project's `pragmo` module.