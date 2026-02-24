# doeff-preset Examples

Progressive examples demonstrating doeff-preset usage.

## Overview

| Example | Description | Concepts |
|---------|-------------|----------|
| [01_basic_slog.py](./01_basic_slog.py) | Basic slog display | slog, rich output, log accumulation |
| [02_configuration.py](./02_configuration.py) | Configuration via Ask | preset.* config, custom defaults |
| [03_merge_handlers.py](./03_merge_handlers.py) | Merging with domain handlers | Handler composition, WithHandler stacking |
| [04_granular_handlers.py](./04_granular_handlers.py) | Selective handler usage | log_display_handlers, config_handlers |
| [05_async_runtime.py](./05_async_runtime.py) | Async runtime support | run, async_run, WithHandler |

## Running Examples

```bash
# From the doeff-preset package directory
cd packages/doeff-preset

# Run a specific example
uv run python examples/01_basic_slog.py

# Or run all examples
for f in examples/0*.py; do uv run python "$f"; done
```

## Example 01: Basic slog Display

**Concepts:** slog, WriterTellEffect, rich formatting, log accumulation

Shows how preset_handlers intercepts `slog` calls to:
1. Display them to console using rich formatting
2. Accumulate them in `result.log`

```python
yield slog(step="start", msg="Hello", item_count=42)
# Output: INFO start | Hello | item_count=42
```

## Example 02: Configuration via Ask

**Concepts:** Ask("preset.*"), default config, custom defaults

Shows how to query preset configuration:
```python
show_logs = yield Ask("preset.show_logs")   # Default: True
log_level = yield Ask("preset.log_level")   # Default: "info"
log_format = yield Ask("preset.log_format") # Default: "rich"
```

Override defaults:
```python
handlers = preset_handlers(config_defaults={
    "preset.log_level": "debug",
})
```

## Example 03: Merging Handlers

**Concepts:** Handler composition, domain effects, explicit handler stacking

Shows how to combine preset with domain-specific handlers:
```python
from doeff import WithHandler

program = WithHandler(
    preset_handlers(),
    WithHandler(domain_handler, my_workflow()),
)
```

## Example 04: Granular Handler Selection

**Concepts:** log_display_handlers(), config_handlers(), selective composition

Shows how to use individual exports for fine-grained control:
```python
from doeff_preset import log_display_handlers, config_handlers

# Only slog display
handlers = log_display_handlers()

# Only config with custom defaults
handlers = config_handlers(defaults={"preset.log_level": "debug"})

# Custom combination
program = WithHandler(log_display_handlers(), WithHandler(config_handlers(), my_workflow()))
```

## Example 05: Async Runtime Support

**Concepts:** run, async_run, runtime compatibility

Shows that the same handlers work with both runtimes:
```python
handlers = preset_handlers()

# Sync
result = run(WithHandler(handlers, workflow()), handlers=default_handlers())

# Async
result = await async_run(
    WithHandler(handlers, workflow()),
    handlers=default_async_handlers(),
)
```

## Key Patterns

### Basic Usage

```python
from doeff import WithHandler, default_handlers, do, run, slog
from doeff_preset import preset_handlers

@do
def my_workflow():
    yield slog(step="start", msg="Begin")
    # ... work ...
    yield slog(step="done", msg="Complete")
    return "success"

result = run(
    WithHandler(preset_handlers(), my_workflow()),
    handlers=default_handlers(),
)
# slog messages displayed AND accumulated in result.log
```

### With Domain Handlers

```python
from doeff import WithHandler
from doeff_preset import preset_handlers
from my_app import domain_handler

program = WithHandler(
    preset_handlers(),
    WithHandler(domain_handler, my_workflow()),
)
```

### Custom Configuration

```python
handlers = preset_handlers(config_defaults={
    "preset.show_logs": False,
    "preset.log_level": "debug",
})
```

## Next Steps

1. Read the [README.md](../README.md) for full documentation
2. Check the [test suite](../tests/test_preset_handlers.py) for more usage patterns
3. See how doeff-conductor and doeff-agentic examples integrate preset
