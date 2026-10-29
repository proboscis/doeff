# Doeff Marker Comments

> **Needs a component outside `pip install doeff`.** Markers are read by `doeff-indexer` (for
> `doeff run` auto-discovery) and by the IDE plugins; `doeff` alone does not read them.
> `doeff-indexer` is on PyPI:

```bash
pip install doeff doeff-indexer
```

> The 0.1.3 wheels on PyPI (every platform) have two faults. Both are fixed in this repository and
> ship with the release after 0.1.3:
>
> - The wheel puts the `doeff-indexer` command where its entry point does not look, so the command
>   under [CLI Usage](#cli-usage) prints "binary not found". Until then, build it from a checkout as
>   described in [IDE Plugins](ide-plugins.md).
> - The indexer skips a Python file that mentions none of `Program`, `@do`, `ProgramInterpreter`
>   and `KleisliProgram`, so a marker in such a file is not found. The first example below only
>   imports `run`; until then, add `from doeff import Program` to such a file. `doeff run`
>   auto-discovery has the same limit.

## Overview

Doeff markers are special comments that allow precise categorization of functions in your codebase. They help the doeff-indexer and IDE plugins (like the PyCharm plugin) better understand the role of each function, enabling more accurate navigation and execution.

## Basic Syntax

Markers use the format `# doeff: <marker_name>` and can be placed on or near function definitions:

```python
from doeff import run

def my_interpreter(program):  # doeff: interpreter
    return run(program)
```

## Supported Markers

### Core Markers

1. **`interpreter`** - Functions that execute/interpret Program objects
   ```python
   from doeff import run

   def run_program(program):  # doeff: interpreter
       return run(program)
   ```

2. **`transform`** / **`transformer`** - Functions that transform Program objects
   ```python
   def add_logging(program):  # doeff: transform
       return writer(program)
   ```

3. **`kleisli`** - Composable effect-handling functions (usually with `@do` decorator)
   ```python
   from dataclasses import dataclass

   from doeff import EffectBase, Resume, do, handler, run


   @dataclass(frozen=True)
   class Fetch(EffectBase):
       url: str


   @do
   def fetch_data():  # doeff: kleisli
       return (yield Fetch(url="https://api.example.com/users"))


   @handler
   def canned_fetch(effect: Fetch, k):
       return (yield Resume(k, f"response from {effect.url}"))


   print(run(canned_fetch(fetch_data())))  # response from https://api.example.com/users
   ```

   The arguments of an effect are the fields of its class, so the handler reads `effect.url`.
   `Effect(...)` (`EffectBase`) has no fields: `Effect("fetch", url="...")` raises `TypeError` in the
   release after 0.5.0, and 0.5.0 drops the arguments without a word.

### CLI Auto-Discovery Markers

4. **`default`** - Mark default interpreters and environments for CLI auto-discovery

   For interpreters (requires both `interpreter` and `default`):
   ```python
   from doeff import run

   def my_interpreter(program):
       """# doeff: interpreter, default"""
       return run(program)
   ```

   For environments:
   ```python
   from doeff import Program, Pure

   # doeff: default
   base_env: Program[dict] = Pure({
       'db_host': 'localhost',
       'timeout': 10
   })
   ```

   The indexer reads a variable only when it is an annotated assignment (`name: Program[dict] = ...`);
   a plain `name = ...` is not indexed.

   See **[CLI Auto-Discovery Guide](14-cli-auto-discovery.md)** for complete documentation.

### Multiple Markers

Functions can have multiple roles specified with comma-separated markers:

```python
@do
def hybrid_function(program):  # doeff: kleisli, transform
    transformed = yield program
    return transformed
```

## Placement Rules

Markers can be placed in several positions:

### Same Line as Function Definition
```python
def interpreter(program):  # doeff: interpreter
    pass
```

### On Multi-line Function Signatures
```python
def interpreter(  # doeff: interpreter
    program,
    config: dict = None
):
    pass
```

### Inline with Parameters
```python
def interpreter(
    program,  # doeff: interpreter
    verbose: bool = False
):
    pass
```

## How It Works

### 1. Indexer Extraction
The doeff-indexer scans Python files and extracts markers from comments:
- Looks for `# doeff:` pattern
- Extracts marker names (interpreter, transform, kleisli, etc.)
- Associates markers with the function in the index

### 2. IDE Plugin Filtering
The PyCharm plugin uses markers for intelligent filtering:
- **Priority**: Marker-based filtering takes precedence over type analysis
- **Fallback**: If no markers exist, falls back to parameter type detection
- **Precision**: Reduces false positives in function detection

### 3. Execution Flow
```
Source Code → Indexer Extracts Markers → Index Entry with Markers → IDE Plugin Filters → User Selection
```

## Benefits

### 1. **Precision**
- Explicitly declare function intent
- Avoid false positives from type-based detection
- Clear semantic meaning in code

### 2. **Documentation**
- Markers serve as inline documentation
- Immediately visible function purpose
- Searchable via grep/indexer

### 3. **IDE Integration**
- Better navigation in PyCharm
- Accurate function categorization
- Improved execution suggestions

### 4. **Flexibility**
- Works with any function signature
- Compatible with decorators
- Supports class methods and properties

## Best Practices

### 1. Be Explicit
Mark functions when their role is clear:
```python
from doeff import run

# Good - explicit marking
def my_interpreter(program):  # doeff: interpreter
    return run(program)

# Less ideal - relies on type detection
def my_interpreter(program):
    return run(program)
```

### 2. Use Consistent Placement
Choose a consistent marker placement style in your codebase:
```python
# Style 1: Same line
def func(program):  # doeff: interpreter

# Style 2: Multi-line signature
def func(  # doeff: interpreter
    program
):
```

### 3. Mark Factory Functions
Mark functions that create interpreters/transforms:
```python
from doeff import run
from doeff_core_effects import reader

def create_interpreter(config: dict):  # doeff: interpreter
    def inner(program):
        return run(reader(config)(program))
    return inner
```

### 4. Document Complex Cases
Add docstrings explaining complex marker usage:
```python
@do
def complex_function(  # doeff: kleisli, transform
    program
):
    """
    This function acts as both a Kleisli arrow and a transformer.
    It transforms the program and yields effects during execution.
    """
    pass
```

## Examples

### Basic Interpreter
```python
from doeff import run

def simple_interpreter(program):  # doeff: interpreter
    """Execute a program with default settings."""
    return run(program)
```

### Interpreter with Handlers
```python
from doeff import run
from doeff_core_effects import reader, state, writer
from doeff_core_effects import scheduled

def full_interpreter(  # doeff: interpreter
    program,
    env: dict = None
):
    """Execute program with reader, state, writer, and scheduler."""
    wrapped = scheduled(state()(writer(reader(env or {})(program))))
    return run(wrapped)
```

### Transform Chain
```python
from doeff_core_effects import state, writer

def optimization_pipeline(  # doeff: transform
    program,
    with_state: bool = True
):
    """Apply handler wrapping as a transform."""
    wrapped = writer(program)
    if with_state:
        wrapped = state()(wrapped)
    return wrapped
```

### Kleisli Composition
Each step is its own effect class, so the handler tells the three apart by class and reads their fields.

```python
from dataclasses import dataclass

from doeff import EffectBase, Resume, do, handler, run


@dataclass(frozen=True)
class Fetch(EffectBase):
    url: str


@dataclass(frozen=True)
class Process(EffectBase):
    data: str


@dataclass(frozen=True)
class Save(EffectBase):
    data: str


@do
def data_pipeline():  # doeff: kleisli
    """Fetch, process, and save data."""
    data = yield Fetch(url="api.example.com")
    processed = yield Process(data=data)
    yield Save(data=processed)
    return processed


saved = []


@handler
def in_memory(effect: Fetch | Process | Save, k):
    match effect:
        case Fetch(url=url):
            return (yield Resume(k, f"rows from {url}"))
        case Process(data=data):
            return (yield Resume(k, data.upper()))
        case Save(data=data):
            saved.append(data)
            return (yield Resume(k, None))


print(run(in_memory(data_pipeline())))  # ROWS FROM API.EXAMPLE.COM
print(saved)  # ['ROWS FROM API.EXAMPLE.COM']
```

### Class Method Interpreter
```python
from doeff import run
from doeff_core_effects import reader

class Executor:
    def execute(self, program):  # doeff: interpreter
        """Execute program with executor context."""
        return run(reader(self.context)(program))
```

## Troubleshooting

### Markers Not Detected

1. **Check Syntax**: Ensure exact format `# doeff: marker_name`
2. **Verify Indexer**: Run indexer with `--marker` flag to test
3. **Rebuild Index**: Clear and rebuild the index cache

### IDE Not Filtering

1. **Update Plugin**: Ensure latest PyCharm plugin version
2. **Refresh Index**: Force index refresh in IDE
3. **Check Logs**: Review IDE logs for indexer errors

### Multiple Markers Not Working

Ensure comma-separated format:
```python
# Correct
def func(program: Program):  # doeff: interpreter, transform

# Incorrect (missing comma)
def func(program: Program):  # doeff: interpreter transform
```

## CLI Usage

### Indexer Commands

Find the functions that carry a marker (one command per marker):
```bash
doeff-indexer find-interpreters --root .
doeff-indexer find-transforms --root .
doeff-indexer find-kleisli --root .
doeff-indexer find-interceptors --root .
```

List everything the indexer found; each entry has a `markers` list:
```bash
doeff-indexer index --root . --pretty
```

### Verification

Test marker extraction: put the file in a directory of its own and index that directory, then read
the `markers` of its entries:
```bash
doeff-indexer --root path/to/dir --pretty
```

## Migration Guide

### Adding Markers to Existing Code

1. **Identify Functions**: Find interpreter/transform/kleisli functions
2. **Add Markers**: Add appropriate `# doeff:` comments
3. **Rebuild Index**: Run indexer to update
4. **Test in IDE**: Verify improved filtering

### Gradual Adoption

- Start with critical functions
- Add markers as you work on code
- Keep fallback type detection active

## Future Extensions

Potential future markers:
- `# doeff: middleware` - Middleware functions
- `# doeff: handler` - Event/error handlers
- `# doeff: decorator` - Decorator functions
- `# doeff: test` - Test-specific interpreters

## See Also

- [examples/marker_demo.py](../examples/marker_demo.py) - Runnable marker usage examples for each placement
- [packages/doeff-indexer/src/test_markers.rs](../packages/doeff-indexer/src/test_markers.rs) - Test cases for marker extraction
