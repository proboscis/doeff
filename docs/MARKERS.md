# Doeff Marker Comments

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
   @do
   def fetch_data():  # doeff: kleisli
       yield Effect("fetch", url="...")
   ```

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
   from doeff import Pure

   # doeff: default
   base_env = Pure({
       'db_host': 'localhost',
       'timeout': 10
   })
   ```

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
from doeff_core_effects.handlers import reader

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
from doeff_core_effects.handlers import reader, state, writer
from doeff_core_effects.scheduler import scheduled

def full_interpreter(  # doeff: interpreter
    program,
    env: dict = None
):
    """Execute program with reader, state, writer, and scheduler."""
    wrapped = scheduled(writer(state()(reader(env or {})(program))))
    return run(wrapped)
```

### Transform Chain
```python
from doeff_core_effects.handlers import state, writer

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
```python
@do
def data_pipeline():  # doeff: kleisli
    """Fetch, process, and save data."""
    data = yield Effect("fetch", url="api.example.com")
    processed = yield Effect("process", data=data)
    yield Effect("save", data=processed)
    return processed
```

### Class Method Interpreter
```python
from doeff import run
from doeff_core_effects.handlers import reader

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

Filter by marker:
```bash
doeff-indexer --marker interpreter
```

List all marked functions:
```bash
doeff-indexer list --show-markers
```

### Verification

Test marker extraction:
```bash
doeff-indexer test-file.py --verbose
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

- [examples/marker_demo.py](../examples/marker_demo.py) - Basic marker usage examples
- [examples/marker_patterns.py](../examples/marker_patterns.py) - Advanced patterns and best practices
- [tests/test_doeff_markers.py](../tests/test_doeff_markers.py) - Test cases for marker functionality
