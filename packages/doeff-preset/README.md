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
from doeff import WithHandler, default_handlers, do, run, slog
from doeff_preset import preset_handlers

@do
def my_workflow():
    yield slog(step="start", msg="Beginning workflow")
    # ... workflow logic
    yield slog(step="done", msg="Workflow complete")
    return "success"

result = run(
    WithHandler(preset_handlers(), my_workflow()),
    handlers=default_handlers(),
)
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
result = run(
    WithHandler(custom, configurable_workflow()),
    handlers=default_handlers(),
)
```

## API

### `preset_handlers(config_defaults=None)`

Backward-compatible alias for `production_handlers(config_defaults=...)`.

```python
handlers = preset_handlers()
# Contains handlers for WriterTellEffect (slog) and AskEffect (preset.* config)
```

### `production_handlers(config_defaults=None)`

Returns the canonical production protocol handler for this package.

```python
from doeff_preset import production_handlers

handlers = production_handlers()
```

### `mock_handlers(config_defaults=None)`

Returns the canonical test protocol handler. Structured slog display is disabled.

```python
from doeff_preset import mock_handlers

handlers = mock_handlers(config_defaults={"preset.show_logs": False})
```

### `log_display_handlers()`

Returns just the slog display protocol handler.

```python
from doeff_preset import log_display_handlers

handler = log_display_handlers()
```

### `config_handlers(defaults=None)`

Returns just the preset.* Ask handlers.

```python
from doeff_preset import config_handlers

handlers = config_handlers(defaults={"preset.log_level": "debug"})
```

## Handler Stacking Semantics

`WithHandler` nesting defines precedence: inner handlers shadow outer handlers.

Typically no conflict since they handle different effect types:

| preset_handlers() | Domain handlers |
|-------------------|-----------------|
| `WriterTellEffect` (slog) | `CreateWorktree` |
| `Ask` (preset.* config) | `RunAgent`, `Commit`, etc. |

### Granular Control Pattern

Pick exactly what you need by stacking only the handlers you want.

## Default Configuration

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `preset.show_logs` | bool | `True` | Whether to display slog messages |
| `preset.log_level` | str | `"info"` | Default log level |
| `preset.log_format` | str | `"rich"` | Output format: "simple", "rich", "json" |

## Works with Both Entrypoints

```python
from doeff import WithHandler, async_run, default_async_handlers, default_handlers, run
from doeff_preset import preset_handlers

handlers = preset_handlers()

# Synchronous
result = run(WithHandler(handlers, my_workflow()), handlers=default_handlers())

# Asynchronous
result = await async_run(
    WithHandler(handlers, my_workflow()),
    handlers=default_async_handlers(),
)
```

## Package Layout

`doeff_preset` maintains canonical `effects/` + `handlers/` submodules.
This package intentionally does not define new domain-specific effect classes;
it composes core doeff effects into preset handler bundles.

## License

MIT
