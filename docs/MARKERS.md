# Doeff Marker Comments

## Overview

Doeff markers are special comments that allow precise categorization of functions in your codebase. They help the doeff-indexer and IDE plugins (like the PyCharm plugin) better understand the role of each function, enabling more accurate navigation and execution.

## Basic Syntax

Markers use the format `# doeff: <marker_name>` and can be placed on or near function definitions:

```python
def my_interpreter(program: Program):  # doeff: interpreter
    return program.run()
```

## Supported Markers

### Core Markers

1. **`interpreter`** - Functions that execute/interpret Program objects
   ```python
   def run_program(program: Program):  # doeff: interpreter
       return program.run()
   ```

2. **`transform`** / **`transformer`** - Functions that transform Program objects
   ```python
   @do
   def optimize(program: Program) -> Program:  # doeff: transform
       return program.optimize()
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
   def my_interpreter(program: Program):
       """# doeff: interpreter, default"""
       return program.run()
   ```

   For environments:
   ```python
   # doeff: default
   base_env: Program[dict] = Program.pure({
       'db_host': 'localhost',
       'timeout': 10
   })
   ```

   See **[CLI Auto-Discovery Guide](14-cli-auto-discovery.md)** for complete documentation.

### Multiple Markers

Functions can have multiple roles specified with comma-separated markers:

```python
@do
def hybrid_function(program: Program):  # doeff: kleisli, transform
    transformed = yield program.transform()
    return transformed
```

## Placement Rules

Markers can be placed in several positions:

### Same Line as Function Definition
```python
def interpreter(program: Program):  # doeff: interpreter
    pass
```

### On Multi-line Function Signatures
```python
def interpreter(  # doeff: interpreter
    program: Program,
    config: dict = None
):
    pass
```

### Inline with Parameters
```python
def interpreter(
    program: Program,  # doeff: interpreter
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
# Good - explicit marking
def my_interpreter(program: Program):  # doeff: interpreter
    return program.run()

# Less ideal - relies on type detection
def my_interpreter(program: Program):
    return program.run()
```

### 2. Use Consistent Placement
Choose a consistent marker placement style in your codebase:
```python
# Style 1: Same line
def func(program: Program):  # doeff: interpreter

# Style 2: Multi-line signature
def func(  # doeff: interpreter
    program: Program
):
```

### 3. Mark Factory Functions
Mark functions that create interpreters/transforms:
```python
def create_interpreter(config: dict):  # doeff: interpreter
    def inner(program: Program):
        return program.run_with_config(config)
    return inner
```

### 4. Document Complex Cases
Add docstrings explaining complex marker usage:
```python
@do
def complex_function(  # doeff: kleisli, transform
    program: Program
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
def simple_interpreter(program: Program):  # doeff: interpreter
    """Execute a program with default settings."""
    return program.run()
```

### Async Interpreter
```python
async def async_interpreter(  # doeff: interpreter
    program: Program,
    timeout: float = None
):
    """Execute program asynchronously with optional timeout."""
    return await program.arun(timeout=timeout)
```

### Transform Chain
```python
@do
def optimization_pipeline(  # doeff: transform
    program: Program,
    level: int = 1
) -> Program:
    """Apply multiple optimization passes."""
    for _ in range(level):
        program = program.optimize()
    return program
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
class Executor:
    def execute(self, program: Program):  # doeff: interpreter
        """Execute program with executor context."""
        return program.run_with_context(self.context)
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