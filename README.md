# doeff - Do-notation and Effects for Python

A pragmatic free monad implementation that prioritizes usability and Python idioms over theoretical purity. Uses generators for do-notation and supports comprehensive effects including Reader, State, Writer, Future, Result, and IO.

## Features

- **Generator-based do-notation**: Write monadic code that looks like regular Python
- **Comprehensive effects system**: Reader, State, Writer, Future, Result, IO, and more
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
from doeff import do, Program, Put, Get, Log

@do
def counter_program() -> Program[int]:
    yield Put("counter", 0)
    yield Log("Starting computation")
    count = yield Get("counter")
    yield Put("counter", count + 1)
    return count + 1

# Run the program
import asyncio
from doeff import ProgramInterpreter

async def main():
    interpreter = ProgramInterpreter()
    result = await interpreter.run(counter_program())
    print(f"Result: {result.result}")  # Ok(1)
    print(f"Final state: {result.state}")  # {'counter': 1}
    print(f"Log: {result.log}")  # ['Starting computation']

asyncio.run(main())
```

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

### Result (Fail, Catch)
```python
@do
def with_error_handling():
    try:
        result = yield risky_operation()
    except Exception as e:
        result = yield Catch(
            risky_operation(),
            lambda exc: f"Failed: {exc}"
        )
    return result
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

## Pinjected Integration

```python
from doeff import do, Dep
from doeff_pinjected import program_to_injected

@do
def service_program():
    db = yield Dep("database")
    cache = yield Dep("cache")
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