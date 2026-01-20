# doeff-omni-converter

Kleisli-based auto-conversion with doeff effects for the omni-converter library.

## Overview

This package integrates doeff's effect system with omni-converter's auto-conversion architecture, enabling effectful conversion rules (IO, logging, config lookup) while preserving A* solver compatibility.

## Installation

```bash
pip install doeff-omni-converter
```

Or with uv:
```bash
uv add doeff-omni-converter
```

## Features

- **Effectful Conversions**: Rules can do IO, logging, config lookup
- **Observable Solver**: Can log/trace the A* search process
- **Deferred Execution**: Conversion plan is a Program, user controls when/how to run
- **Type-Safe Formats**: Beartype catches typos early, IDE support
- **Composable**: Conversions compose naturally with other doeff effects
- **Testable**: Mock conversions via handler replacement

## Quick Start

```python
from doeff import do
from doeff.program import Program
from doeff_omni_converter import (
    AutoData, F, KleisliEdge, KleisliRuleBook,
    RULEBOOK_KEY, convert_handler_interceptor
)

# Define a simple Kleisli converter
@do
def load_image(path: str):
    """Load image from path - effectful operation."""
    from doeff.effects import tell
    yield tell({"event": "loading", "path": path})
    # In real code: actually load the image
    return {"data": f"loaded from {path}"}

# Define rules
def path_rules(fmt):
    if fmt == F.path:
        return [KleisliEdge(
            converter=load_image,
            dst_format=F.numpy(),
            cost=1,
            name="load_from_path"
        )]
    return []

# Create rulebook
rulebook = KleisliRuleBook([path_rules])

# Use in a pipeline
@do
def image_pipeline():
    img = AutoData("/path/to/image.jpg", F.path)
    
    # Effectful conversion
    numpy_img = yield img.to(F.numpy())
    
    return numpy_img.value

# Run with environment
from doeff.run import run_sync

result = run_sync(
    image_pipeline().intercept(convert_handler_interceptor),
    env={RULEBOOK_KEY: rulebook}
)
```

## Type-Safe Formats

Replace error-prone string formats with structured, type-checked formats:

```python
from doeff_omni_converter import F, ImageFormat

# Using convenience factory
torch_fmt = F.torch("float32", "CHW", "RGB", (0.0, 1.0))
numpy_fmt = F.numpy("uint8", "HWC", "RGB", (0.0, 255.0))

# Using ImageFormat directly
custom_fmt = ImageFormat(
    backend="torch",
    dtype="float16",
    arrangement="BCHW",
    colorspace="RGB",
    value_range=(0.0, 1.0)
)

# Singleton formats for simple types
path_fmt = F.path
url_fmt = F.url
```

## Core Concepts

### AutoData

Self-describing data with format information:

```python
data = AutoData(value, format)
converted = yield data.to(target_format)  # Returns ConvertEffect
```

### KleisliEdge

A conversion step with a Kleisli arrow (effectful converter):

```python
edge = KleisliEdge(
    converter=my_kleisli_function,  # A -> Program[B]
    dst_format=target_format,
    cost=1,  # Used by A* solver
    name="my_conversion"
)
```

### KleisliRuleBook

Collection of rules that produce edges:

```python
rulebook = KleisliRuleBook([
    image_rules,
    custom_rules,
])
```

## Effectful Converters

Converters are Kleisli arrows that can use any doeff effect:

```python
@do
def fetch_url(url: str):
    """Effectful converter that fetches from URL."""
    from doeff.effects import tell
    
    yield tell({"event": "fetching", "url": url})
    
    # Use IO effect for actual network call
    from doeff.effects.io import perform
    response = yield perform(lambda: httpx.get(url))
    
    return response.content
```

## API Reference

### Types
- `ImageFormat`: Type-safe image format specification
- `F`: Factory for common formats
- `Backend`, `DType`, `Arrangement`, `ColorSpace`: Format component types

### Effects
- `ConvertEffect`: Effect requesting format conversion
- `convert(data, target)`: Create conversion effect

### Rules
- `KleisliEdge`: Single conversion step
- `KleisliRuleBook`: Collection of rules
- `AutoData`: Self-describing data

### Solver
- `solve(rulebook, src, dst)`: Find optimal path
- `solve_lazy(...)`: Returns None on failure
- `can_convert(...)`: Check if path exists
- `estimate_cost(...)`: Get path cost

### Handlers
- `handle_convert(effect)`: Process ConvertEffect
- `convert_handler_interceptor`: Interceptor for use with `.intercept()`
- `RULEBOOK_KEY`: Environment key for rulebook

## License

MIT
