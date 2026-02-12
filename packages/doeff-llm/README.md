# doeff-llm

Unified, provider-agnostic LLM effect types for the `doeff` ecosystem.

`doeff-llm` defines effect data only. Provider packages such as
`doeff-openai`, `doeff-gemini`, and `doeff-openrouter` implement handlers
that route these effects by model name.

## Effects

- `LLMChat`
- `LLMStreamingChat`
- `LLMStructuredOutput`
- `LLMEmbedding`

## Quick Example

```python
from doeff import WithHandler, do, run
from doeff_llm.effects import LLMChat
from doeff_openai.handlers import openai_production_handler
from doeff_gemini.handlers import gemini_production_handler


@do
def workflow():
    first = yield LLMChat(
        messages=[{"role": "user", "content": "Reply with one sentence"}],
        model="gpt-4o-mini",
    )
    second = yield LLMChat(
        messages=[{"role": "user", "content": "Summarize the previous answer"}],
        model="gemini-1.5-pro",
    )
    return first, second


result = run(
    WithHandler(
        gemini_production_handler,
        WithHandler(openai_production_handler, workflow()),
    ),
    env={"openai_api_key": "...", "gemini_api_key": "..."},
)
```
