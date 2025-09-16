# doeff-gemini

Google Gemini integration for the `doeff` effect system. The package mirrors the
`doeff-openai` structured LLM helper and provides:

- Lazy configuration of the official [`google-genai`](https://pypi.org/project/google-genai/) client
- Structured responses backed by Pydantic models via Gemini `response_schema`
- Full observability with `Log`, `Step`, and retry tracking just like the OpenAI integration

## Quick start

```bash
gcloud auth application-default login

```

```python
from doeff import do, run_with_env
from pydantic import BaseModel

from doeff_gemini import structured_llm__gemini


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


result = run_with_env(fetch_weather(), env={"gemini_api_key": "your-api-key"})
print(result.value)
```

When no API key is supplied the integration automatically falls back to
Application Default Credentials, so running
`gcloud auth application-default login` once on the machine is sufficient for
local development.
