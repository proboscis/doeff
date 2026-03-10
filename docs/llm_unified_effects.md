# Unified LLM Effects (`doeff-llm`)

`doeff-llm` introduces provider-agnostic effects that can be handled by multiple provider packages:

- `LLMChat`
- `LLMStreamingChat`
- `LLMStructuredOutput`
- `LLMEmbedding`

## Why

Before `doeff-llm`, each provider package defined near-identical effect classes.
Now, effect definitions are shared in one package while provider packages focus on handlers.

## Model-Based Routing

Handlers inspect `effect.model`:

1. If a handler supports the model, it handles the effect.
2. If not, it yields `Pass()` so the next outer handler can try.

When a handler uses a narrow effect annotation such as `effect: LLMChat` or
`effect: LLMStructuredOutput`, `WithHandler(...)` also installs a runtime type filter so
non-matching effects skip the handler entirely.

This enables a single program to call multiple providers by model name.

## Example: Multi-Provider Stack

```python
from pydantic import BaseModel

from doeff import WithHandler, do, run
from doeff_llm.effects import LLMChat, LLMStructuredOutput
from doeff_gemini.handlers import gemini_production_handler
from doeff_openai.handlers import openai_production_handler
from doeff_openrouter.handlers import openrouter_production_handler


class Analysis(BaseModel):
    verdict: str
    score: int


@do
def workflow():
    analysis = yield LLMStructuredOutput(
        messages=[{"role": "user", "content": "Analyze this code"}],
        response_format=Analysis,
        model="gpt-4o",
    )
    summary = yield LLMChat(
        messages=[{"role": "user", "content": f"Summarize: {analysis.verdict}"}],
        model="gemini-1.5-pro",
    )
    fallback = yield LLMChat(
        messages=[{"role": "user", "content": "Say hello"}],
        model="mistral-large-latest",
    )
    return analysis, summary, fallback


result = run(
    WithHandler(
        handler=openrouter_production_handler,  # fallback / catch-all
        expr=WithHandler(
            handler=gemini_production_handler,
            expr=WithHandler(handler=openai_production_handler, expr=workflow()),
        ),
    ),
    env={
        "openai_api_key": "...",
        "gemini_api_key": "...",
        "openrouter_api_key": "...",
    },
)
```

## Deprecated Provider-Specific Effect Names

Provider-specific effect classes remain for compatibility:

- `doeff_openai.effects.ChatCompletion`, `StructuredOutput`, `Embedding`
- `doeff_gemini.effects.GeminiChat`, `GeminiStructuredOutput`, `GeminiEmbedding`
- `doeff_openrouter.effects.RouterChat`, `RouterStructuredOutput`

They are now deprecated aliases and emit `DeprecationWarning` when instantiated.
