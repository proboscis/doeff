# doeff-gemini

Google Gemini integration for the `doeff` effect system. The package mirrors the
`doeff-openai` structured LLM helper and provides:

- Lazy configuration of the official [`google-genai`](https://pypi.org/project/google-genai/) client
- Structured responses backed by Pydantic models via Gemini `response_schema`
- Full observability with `Log`, `Step`, and retry tracking just like the OpenAI integration

## Unified Effects Routing

`doeff-gemini` now handles provider-agnostic effects from `doeff-llm`:

- `LLMChat`
- `LLMStreamingChat`
- `LLMStructuredQuery`
- `LLMEmbedding`

The Gemini handler routes by model name (for example `gemini-*`) and delegates
unsupported models via `Delegate()` so stacked handlers can continue routing.

## Quick start

```bash
gcloud auth application-default login

```

```python
import asyncio
from pydantic import BaseModel

from doeff import async_run, default_handlers, do
from doeff_gemini import structured_llm__gemini
from doeff_gemini import default_gemini_cost_handler


class WeatherResponse(BaseModel):
    location: str
    summary: str
    temperature_c: float


@do
def fetch_weather() -> WeatherResponse:
    return (
        yield structured_llm__gemini(
            text="Provide the current weather in Tokyo as JSON",
            model="gemini-1.5-flash",
            response_format=WeatherResponse,
        )
    )


async def main():
    result = await async_run(
        fetch_weather(),
        handlers=[default_gemini_cost_handler, *default_handlers()],
        env={"gemini_api_key": "your-api-key"},
    )
    print(result.value)


asyncio.run(main())
```

When no API key is supplied the integration automatically falls back to
Application Default Credentials, so running
`gcloud auth application-default login` once on the machine is sufficient for
local development.

## Nano Banana Pro (Gemini 3 Pro Image)

Use the official model ID `gemini-3-pro-image-preview` for Nano Banana Pro. Example:

```python
result = await async_run(
    edit_image__gemini(
        prompt="Add a small yellow banana icon in the center",
        model="gemini-3-pro-image-preview",
        images=[...],
    ),
    handlers=[default_gemini_cost_handler, *default_handlers()],
    env={"gemini_api_key": "your-api-key"},
)
```

## Cost calculation hook

Cost tracking emits the `GeminiCalculateCost` effect. Use
`default_gemini_cost_handler` for built-in pricing, then stack your own handler
for custom/unknown models:

```python
result = await async_run(
    my_program(),
    handlers=[
        default_gemini_cost_handler,  # known models
        my_custom_cost_handler,       # overrides / unknown models
        *default_handlers(),
    ],
)
```

`default_gemini_cost_handler` delegates unknown models with `Delegate()`, so a
custom handler can catch them. If nothing handles `GeminiCalculateCost`, the
program fails fast with an unhandled effect error.

## Waiting between retries

Retries and the wait for an uploaded file to become ACTIVE wait with doeff-time's `Delay` effect rather than sleeping, so the caller's time handler
decides what a second is. Install `doeff_time.async_time_handler()` (the wall clock) in the handler
stack, inside `await_handler`, which answers its `Await`; a test installs `sim_time_handler()`, whose virtual clock moves at once, and the retry path
runs without sleeping. Without a time handler, a retry fails with an unhandled `DelayEffect`.

## Client setup

See `docs/gemini_client_setup.md` for details on how the Gemini client is
constructed (API key vs ADC) and which environment keys are read.

## Unified image effects (`doeff-image`)

`doeff-gemini` now supports provider-agnostic image effects:

- `doeff_image.ImageGenerate`
- `doeff_image.ImageEdit`

Use `gemini_image_handler` for model-routed protocol handling. It handles
Gemini image models and delegates unsupported models with `Delegate()`, so it
can be stacked with other provider handlers.
