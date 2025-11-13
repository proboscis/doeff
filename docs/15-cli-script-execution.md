# 15. CLI Script Execution

Execute Python scripts with access to program execution results, enabling interactive debugging and program manipulation.

## Table of Contents

1. [Overview](#overview)
2. [Quick Start](#quick-start)
3. [Usage Examples](#usage-examples)
4. [Available Variables](#available-variables)
5. [Use Cases](#use-cases)
6. [Best Practices](#best-practices)

---

## Overview

The `doeff run` command supports executing Python scripts after running a program. This allows you to:

- **Inspect execution results**: Access the program, value, and interpreter used
- **Re-run programs**: Execute the same program multiple times with different configurations
- **Debug and experiment**: Manipulate programs and results interactively
- **Automate workflows**: Build scripts that process program results

### How It Works

1. The program is executed first (with auto-discovery if enabled)
2. Execution results are injected into the script's global namespace
3. Your script runs with access to these variables
4. Script output is printed to stdout

---

## Quick Start

### Basic Usage

```bash
uv run doeff run --program <module.path.to.program> - <<'PY'
print(f"Program: {program}")
print(f"Value: {value}")
print(f"Interpreter: {interpreter}")
PY
```

### With Auto-Discovery

```bash
# No need to specify --interpreter or --env - they're auto-discovered!
uv run doeff run --program myapp.features.auth.login_program - <<'PY'
print(f"Auto-discovered interpreter: {interpreter}")
print(f"Execution result: {value}")
print(f"Program with envs: {type(program).__name__}")
PY
```

---

## Usage Examples

### Example 1: Inspect Execution Results

```bash
uv run doeff run --program tests.cli_assets.sample_program --interpreter tests.cli_assets.sync_interpreter - <<'PY'
print("=== Execution Summary ===")
print(f"Program type: {type(program).__name__}")
print(f"Result value: {value}")
print(f"Result type: {type(value).__name__}")
print(f"Interpreter: {interpreter}")
PY
```

### Example 2: Re-run Program

```bash
uv run doeff run --program tests.cli_assets.sample_program --interpreter tests.cli_assets.sync_interpreter - <<'PY'
print(f"Initial execution: {value}")

# Re-run using the injected interpreter
if isinstance(interpreter, ProgramInterpreter):
    run_again = interpreter.run(program)
    print(f"Re-run result: {run_again.value}")
else:
    # Interpreter is a function
    run_again_value = interpreter(program)
    print(f"Re-run result: {run_again_value}")
PY
```

### Example 3: With Auto-Discovery

```bash
uv run doeff run --program tests.fixtures_discovery.myapp.features.auth.login.login_program - <<'PY'
print("=== Auto-Discovery Results ===")
print(f"Interpreter: {interpreter}")
print(f"Value: {value}")
print(f"Program type: {type(program).__name__}")

# Verify environments were auto-discovered and merged
print(f"Program has Local effect (envs applied): {'Local' in type(program).__name__}")

# Re-run with same interpreter
run_again_value = interpreter(program)
print(f"Re-run value: {run_again_value}")
PY
```

### Example 4: Process Results

```bash
uv run doeff run --program myapp.data.processor --interpreter myapp.interpreter - <<'PY'
import json

# Process the result
if isinstance(value, dict):
    print("Processing dictionary result:")
    print(json.dumps(value, indent=2))
    
    # Extract specific fields
    if "status" in value:
        print(f"Status: {value['status']}")
elif isinstance(value, list):
    print(f"Processing list with {len(value)} items")
    for item in value:
        print(f"  - {item}")
else:
    print(f"Result: {value}")
PY
```

### Example 5: Conditional Re-execution

```bash
uv run doeff run --program myapp.validator --interpreter myapp.interpreter - <<'PY'
print(f"Initial result: {value}")

# Only re-run if validation failed
if value.get("status") == "failed":
    print("Validation failed, re-running with debug mode...")
    # You could modify the program here if needed
    run_again = interpreter(program)
    print(f"Re-run result: {run_again}")
else:
    print("Validation passed!")
PY
```

---

## Available Variables

The following variables are automatically injected into your script's global namespace:

### Core Variables

| Variable | Type | Description |
|----------|------|-------------|
| `program` | `Program[T]` | The executed program (with envs, transforms, etc. applied) |
| `value` | `T` | The final execution result (unwrapped) |
| `interpreter` | `ProgramInterpreter \| Callable` | The interpreter used (instance or function) |

### Utility Classes

| Variable | Type | Description |
|----------|------|-------------|
| `RunResult` | `Type[RunResult]` | RunResult class for type checking |
| `Program` | `Type[Program]` | Program class for type checking |
| `ProgramInterpreter` | `Type[ProgramInterpreter]` | ProgramInterpreter class for creating new instances |

### Standard Library

| Variable | Type | Description |
|----------|------|-------------|
| `sys` | `Module` | Python sys module |
| `json` | `Module` | Python json module |

### Example: Using All Variables

```bash
uv run doeff run --program myapp.program --interpreter myapp.interpreter - <<'PY'
import json

# Check types
print(f"Program is Program instance: {isinstance(program, Program)}")
print(f"Interpreter is ProgramInterpreter: {isinstance(interpreter, ProgramInterpreter)}")

# Access result
print(f"Value: {value}")

# Use utility classes
if isinstance(interpreter, ProgramInterpreter):
    new_result = interpreter.run(program)
    print(f"New result: {new_result.value}")

# Use standard library
print(f"Python version: {sys.version}")
PY
```

---

## Use Cases

### 1. Interactive Debugging

Debug program execution by inspecting intermediate results:

```bash
uv run doeff run --program myapp.complex_workflow - <<'PY'
print("=== Debugging Complex Workflow ===")
print(f"Program structure: {type(program).__name__}")
print(f"Final value: {value}")

# Check if result is what we expect
if isinstance(value, dict) and "error" in value:
    print("ERROR DETECTED!")
    print(f"Error details: {value['error']}")
PY
```

### 2. Result Processing

Transform or process execution results:

```bash
uv run doeff run --program myapp.data_processor - <<'PY'
import json

# Process the result
if isinstance(value, list):
    summary = {
        "count": len(value),
        "total": sum(item.get("value", 0) for item in value),
        "items": value
    }
    print(json.dumps(summary, indent=2))
PY
```

### 3. Program Manipulation

Modify and re-execute programs:

```bash
uv run doeff run --program myapp.base_program - <<'PY'
# Re-run with different interpreter
new_interpreter = ProgramInterpreter()
result = new_interpreter.run(program)
print(f"Re-run with new interpreter: {result.value}")
PY
```

### 4. Testing and Validation

Verify program behavior:

```bash
uv run doeff run --program myapp.validator - <<'PY'
# Validate result
assert isinstance(value, dict), "Result must be a dictionary"
assert "status" in value, "Result must have 'status' field"
assert value["status"] == "success", f"Expected success, got {value['status']}"

print("✓ Validation passed!")
PY
```

### 5. Automation Scripts

Build scripts that automate workflows:

```bash
uv run doeff run --program myapp.workflow - <<'PY'
import json

# Process and save result
result_data = {
    "timestamp": __import__("datetime").datetime.now().isoformat(),
    "value": value,
    "program_type": type(program).__name__
}

with open("result.json", "w") as f:
    json.dump(result_data, f, indent=2)

print("Result saved to result.json")
PY
```

---

## Best Practices

### 1. Use Heredoc Syntax

Prefer the heredoc syntax (`<<'PY'`) for multi-line scripts:

```bash
# ✅ Good: Heredoc syntax
uv run doeff run --program myapp.program - <<'PY'
print(f"Value: {value}")
PY

# ❌ Avoid: Single-line with echo
echo "print(value)" | uv run doeff run --program myapp.program -
```

### 2. Quote the Delimiter

Use `<<'PY'` (with quotes) to prevent shell variable expansion:

```bash
# ✅ Good: Prevents shell expansion
uv run doeff run --program myapp.program - <<'PY'
print(f"Value: {value}")  # {value} is not expanded by shell
PY

# ❌ Bad: Shell might expand variables
uv run doeff run --program myapp.program - <<PY
print(f"Value: {value}")  # Shell might try to expand {value}
PY
```

### 3. Check Interpreter Type

Handle both `ProgramInterpreter` instances and functions:

```bash
uv run doeff run --program myapp.program - <<'PY'
if isinstance(interpreter, ProgramInterpreter):
    # Interpreter is an instance
    result = interpreter.run(program)
    print(f"Result: {result.value}")
else:
    # Interpreter is a function
    result = interpreter(program)
    print(f"Result: {result}")
PY
```

### 4. Leverage Auto-Discovery

Let the CLI auto-discover interpreters and environments when possible:

```bash
# ✅ Good: Auto-discovery
uv run doeff run --program myapp.features.auth.login_program - <<'PY'
print(f"Auto-discovered: {interpreter}")
PY

# ❌ Less ideal: Manual specification (unless needed)
uv run doeff run --program myapp.features.auth.login_program \
  --interpreter myapp.features.auth.auth_interpreter \
  --env myapp.base_env - <<'PY'
print(f"Manual: {interpreter}")
PY
```

### 5. Error Handling

Handle potential errors in your scripts:

```bash
uv run doeff run --program myapp.program - <<'PY'
try:
    if isinstance(interpreter, ProgramInterpreter):
        result = interpreter.run(program)
        print(f"Success: {result.value}")
    else:
        result = interpreter(program)
        print(f"Success: {result}")
except Exception as e:
    print(f"Error: {e}", file=sys.stderr)
    sys.exit(1)
PY
```

### 6. Use Type Checking

Verify types before operations:

```bash
uv run doeff run --program myapp.program - <<'PY'
# Type checking
if not isinstance(program, Program):
    print("Error: program is not a Program instance", file=sys.stderr)
    sys.exit(1)

if isinstance(value, dict):
    # Safe to access dictionary methods
    print(f"Keys: {list(value.keys())}")
else:
    print(f"Value is not a dict: {type(value).__name__}")
PY
```

---

## Integration with Auto-Discovery

Script execution works seamlessly with auto-discovery:

```bash
# Auto-discovery finds interpreter and environments
uv run doeff run --program myapp.features.auth.login_program - <<'PY'
print("=== Auto-Discovery Results ===")
print(f"Interpreter: {interpreter}")
print(f"Value: {value}")

# Program has environments merged (Local effect)
print(f"Program type: {type(program).__name__}")
assert "Local" in type(program).__name__, "Program should have Local effect"
PY
```

The script receives:
- **Auto-discovered interpreter**: The closest interpreter in the module hierarchy
- **Auto-discovered environments**: All environments merged in hierarchy order
- **Prepared program**: With all envs, transforms, and effects applied

---

## Troubleshooting

### Script Not Executing

If your script doesn't run, check:

1. **Script is provided**: Make sure `-` is the last argument
2. **Heredoc syntax**: Use `<<'PY'` correctly
3. **Script content**: Ensure the script is not empty

```bash
# ✅ Correct
uv run doeff run --program myapp.program - <<'PY'
print("Hello")
PY

# ❌ Wrong: Missing -
uv run doeff run --program myapp.program <<'PY'
print("Hello")
PY
```

### Variables Not Available

If variables are not available:

1. **Check variable names**: Use `program`, `value`, `interpreter` (lowercase)
2. **Import if needed**: Some classes need explicit import in the script
3. **Check execution**: Ensure the program executed successfully

### Interpreter Type Issues

If you get errors with the interpreter:

```bash
uv run doeff run --program myapp.program - <<'PY'
# Always check type first
print(f"Interpreter type: {type(interpreter).__name__}")

if isinstance(interpreter, ProgramInterpreter):
    # It's an instance
    result = interpreter.run(program)
elif callable(interpreter):
    # It's a function
    result = interpreter(program)
else:
    print(f"Unknown interpreter type: {type(interpreter)}")
PY
```

---

## See Also

- **[CLI Auto-Discovery](14-cli-auto-discovery.md)** - Automatic interpreter and environment discovery
- **[CLI Run Command Architecture](cli-run-command-architecture.md)** - Internal architecture details
- **[Getting Started](01-getting-started.md)** - Basic doeff concepts
- **[Core Concepts](02-core-concepts.md)** - Program and Effect types

