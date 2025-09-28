# doeff-seedream

Helpers for invoking ByteDance's Seedream 4.0 image generation API from the [doeff](https://github.com/CyberAgentAILab/doeff) effect system. The package mirrors the ergonomics of the Gemini integration so you can swap providers without changing call sites.

## Features

- Minimal client for the Ark (`https://ark.cn-beijing.volces.com`) image generation endpoint
- `@do` compatible `edit_image__seedream4` helper that accepts the same signature as `edit_image__gemini`
- Result objects with convenience helpers for decoding into `PIL.Image.Image`

## Quick start

```python
import asyncio

from doeff import ExecutionContext, ProgramInterpreter, do
from doeff_seedream import edit_image__seedream4

@do
def main():
    result = yield edit_image__seedream4(
        prompt="A futuristic maglev train rushing through a neon city",
    )
    image = result.images[0].to_pil_image()
    image.save("seedream.png")

engine = ProgramInterpreter()
context = ExecutionContext(env={"seedream_api_key": "YOUR_ARK_KEY"})
run_result = asyncio.run(engine.run(main(), context=context))
run_result.value  # SeedreamImageEditResult
```

Set `seedream_api_key` in the Reader environment (or provide a pre-configured `SeedreamClient` via `seedream_client`).

Consult [the official API docs](https://www.volcengine.com/docs/82379/1541523) for the full parameter surface. Most advanced options can be passed through `generation_config_overrides`.
