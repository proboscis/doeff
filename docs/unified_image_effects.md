# Unified Image Effects (`doeff-image`)

`doeff-image` provides provider-agnostic image effects:

- `ImageGenerate`
- `ImageEdit`
- `ImageResult`

Provider packages implement protocol handlers and route by model prefix.

## Model-routed handlers

- `doeff_seedream.handlers.seedream_image_handler`
  - Handles models starting with `seedream-` (and `doubao-seedream-` for compatibility)
  - Delegates unsupported models via `yield Delegate()`
- `doeff_gemini.handlers.gemini_image_handler`
  - Handles Gemini image models (`gemini-...image...`)
  - Delegates unsupported models via `yield Delegate()`

## Multi-provider workflow

```python
from doeff import WithHandler, do, run, default_handlers
from doeff_image.effects import ImageEdit, ImageGenerate
from doeff_gemini.handlers import gemini_image_handler
from doeff_seedream.handlers import seedream_image_handler


@do
def workflow():
    base = yield ImageGenerate(
        prompt="A serene mountain lake at sunset",
        model="seedream-4",
        size=(1024, 1024),
    )

    edited = yield ImageEdit(
        prompt="Add a small wooden bridge in the foreground",
        model="gemini-3-pro-image",
        images=[base.images[0]],
        strength=0.7,
    )
    return edited


result = run(
    WithHandler(
        seedream_image_handler,
        WithHandler(gemini_image_handler, workflow()),
    ),
    handlers=default_handlers(),
)
```

## Deprecation aliases

- `doeff_seedream.effects.SeedreamGenerate` is a deprecated alias of unified image editing semantics.
- `doeff_gemini.effects.GeminiImageEdit` is a deprecated alias of `doeff_image.effects.ImageEdit`.
