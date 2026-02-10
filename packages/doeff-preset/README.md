# doeff-preset

Batteries-included handlers for doeff: slog display, preset configuration, and development utilities.

## Overview

doeff-preset provides opinionated, pre-configured handlers for common use cases. It keeps `doeff` core minimal while offering a convenient "just works" experience for examples, demos, and rapid development.

## Installation

```bash
pip install doeff-preset
# or
uv add doeff-preset
```

## Quick Start

```python
from doeff import do, run_with_handler_map, slog
from doeff_preset import preset_handlers

@do
def my_workflow():
    yield slog(step="start", msg="Beginning workflow")
    # ... workflow logic
    yield slog(step="done", msg="Workflow complete")
    return "success"

result = run_with_handler_map(my_workflow(), preset_handlers())
# slog messages are displayed to console AND accumulated in log
```

## Features

### slog Display

Structured log messages (`slog`) are automatically displayed to the console using rich formatting:

```python
yield slog(step="processing", msg="Handling request", user_id=123)
# Output:    INFO | processing | Handling request | user_id=123
```

### Configuration via Ask

Query configuration through the effect system:

```python
from doeff import do, Ask

@do
def configurable_workflow():
    show_logs = yield Ask("preset.show_logs")  # Default: True
    log_level = yield Ask("preset.log_level")  # Default: "info"
    log_format = yield Ask("preset.log_format")  # Default: "rich"
    
    if show_logs:
        yield slog(level=log_level, msg="Starting...")
```

Override defaults by composing handlers:

```python
from doeff_preset import preset_handlers

custom = preset_handlers(config_defaults={
    "preset.show_logs": False,
    "preset.log_level": "debug",
})
result = run_with_handler_map(configurable_workflow(), custom)
```

## API

### `preset_handlers(config_defaults=None)`

Returns all preset handlers combined.

```python
handlers = preset_handlers()
# Contains handlers for WriterTellEffect (slog) and AskEffect (preset.* config)
```

### `log_display_handlers()`

Returns just the slog display handlers.

```python
from doeff_preset import log_display_handlers

handlers = {
    **log_display_handlers(),
    **my_other_handlers,
}
```

### `config_handlers(defaults=None)`

Returns just the preset.* Ask handlers.

```python
from doeff_preset import config_handlers

handlers = config_handlers(defaults={"preset.log_level": "debug"})
```

## Handler Merge Semantics

Python dict merge - **later wins**:

```python
# Domain handlers override preset (domain handlers win)
handlers = {**preset_handlers(), **mock_handlers()}

# Preset overrides domain (preset wins)
handlers = {**mock_handlers(), **preset_handlers()}
```

Typically no conflict since they handle different effect types:

| preset_handlers() | Domain handlers |
|-------------------|-----------------|
| `WriterTellEffect` (slog) | `CreateWorktree` |
| `Ask` (preset.* config) | `RunAgent`, `Commit`, etc. |

### Granular Control Pattern

Pick exactly what you need:

```python
from doeff_preset import log_display_handlers, config_handlers
from my_domain import domain_handlers

# Only slog display, skip config handlers
handlers = {
    **log_display_handlers(),
    **domain_handlers(),
}

# Custom Ask handling instead of preset's
handlers = {
    **log_display_handlers(),
    **domain_handlers(),
    **my_custom_ask_handlers(),  # Override Ask handling
}

# Everything from preset, but override specific handler
from doeff import WriterTellEffect

handlers = {
    **preset_handlers(),
    **domain_handlers(),
    WriterTellEffect: my_custom_slog_handler,  # Override slog display
}
```

## Default Configuration

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `preset.show_logs` | bool | `True` | Whether to display slog messages |
| `preset.log_level` | str | `"info"` | Default log level |
| `preset.log_format` | str | `"rich"` | Output format: "simple", "rich", "json" |

## Works with Both Entrypoints

```python
from doeff import async_run_with_handler_map, run_with_handler_map
from doeff_preset import preset_handlers

handlers = preset_handlers()

# Synchronous
result = run_with_handler_map(my_workflow(), handlers)

# Asynchronous
result = await async_run_with_handler_map(my_workflow(), handlers)
```

## License

MIT
