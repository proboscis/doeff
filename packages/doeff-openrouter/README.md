# doeff-openrouter

OpenRouter integration for [doeff](https://github.com/CyberAgentAILab/doeff) providing effect-aware logging, graph tracking, and structured output helpers for the OpenRouter API.

## Features

- 🌐 Call any OpenRouter compatible model using do-notation programs
- 🔎 Automatic logging and step tracing through `Log` and `Step` effects
- 📦 Built-in helpers for structured outputs backed by Pydantic models
- 🧪 Works with the same execution/test utilities as other doeff packages

## Installation

```bash
pip install doeff-openrouter
```

## Quick Example

```python
from doeff import do, SyncRuntime
from doeff_openrouter import chat_completion, structured_llm
from pydantic import BaseModel

class Summary(BaseModel):
    title: str
    points: list[str]

@do
def workflow():
    yield chat_completion(
        messages=[{"role": "user", "content": "Say hello from OpenRouter"}],
        model="openai/gpt-4o-mini",
    )

    result = yield structured_llm(
        text="Summarise doeff in three bullet points",
        model="openai/gpt-4o-mini",
        response_format=Summary,
    )
    return result

runtime = SyncRuntime()
result = runtime.run(workflow(), env={"openrouter_api_key": "sk-or-v1..."})
print(result.value)
```

## Environment Keys

- `openrouter_api_key`: API key forwarded to the OpenRouter HTTP endpoint
- `openrouter_base_url` (optional): Override the API endpoint, defaults to `https://openrouter.ai/api/v1`
- `openrouter_timeout` (optional): Request timeout in seconds
- `openrouter_default_headers` (optional): Extra headers to merge onto every request

## Structured Output

`structured_llm` converts a Pydantic model into a JSON schema payload. When a model
cannot stream strict JSON, the helper tries to repair markdown fences or loose JSON
before validating with Pydantic.