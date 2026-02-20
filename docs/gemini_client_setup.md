# Gemini client setup (API key vs ADC)

This integration builds a `google.genai.Client` from values provided via Reader
(`Ask`) or State (`Get`). It supports two authentication paths:

## API key (simple)

Provide `gemini_api_key`. Optional extras:
- `gemini_vertexai` (default False for API key)
- `gemini_location` (optional; often `global` when Vertex AI is enabled)
- `gemini_client_options`, `gemini_client_kwargs` (dicts passed through)

Minimal example:
```python
from doeff import async_run, default_async_handlers

result = await async_run(
    my_program(),
    handlers=default_async_handlers(),
    env={"gemini_api_key": "your-api-key"},
)
```

## Application Default Credentials (ADC)

If `gemini_api_key` is absent, the client attempts ADC:
- Requires `google-auth` installed and `gcloud auth application-default login`
  having been run.
- If no project can be determined from ADC, you must set `gemini_project`.
- When ADC is used, `gemini_vertexai` defaults to True and `gemini_location`
  defaults to `global` unless provided.

Example with explicit project:
```python
result = await async_run(
    my_program(),
    handlers=default_async_handlers(),
    env={
        "gemini_project": "your-project-id",
        "gemini_location": "global",  # optional override
    },
)
```

## Environment keys consulted
- `gemini_client` (prebuilt `GeminiClient` instance, if you want to inject one)
- `gemini_api_key`
- `gemini_vertexai`
- `gemini_project`
- `gemini_location`
- `gemini_credentials` (explicit credentials object)
- `gemini_client_options` (dict)
- `gemini_client_kwargs` (dict, extra kwargs passed to `genai.Client`)

## Failure modes
- Missing project under ADC: fails with a log suggesting setting
  `gemini_project` or configuring gcloud.
- Missing `google-auth` when no API key: fails with a log instructing to install
  `google-auth` or set an API key.
