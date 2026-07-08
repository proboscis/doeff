# Unified LLM Effects (`doeff-llm`)

`doeff-llm` introduces provider-agnostic effects that can be handled by multiple provider packages:

- `LLMChat`
- `LLMStreamingChat`
- `LLMStructuredQuery`
- `LLMEmbedding`

## Why

Before `doeff-llm`, each provider package defined near-identical effect classes.
Now, effect definitions are shared in one package while provider packages focus on handlers.

## Model-Based Routing

Handlers inspect `effect.model`:

1. If a handler supports the model, it handles the effect.
2. If not, it yields `Pass()` so the next outer handler can try.

When a handler uses a narrow effect annotation such as `effect: LLMChat` or
`effect: LLMStructuredQuery`, the installed handler scope also carries a runtime type filter so
non-matching effects skip the handler entirely.

This enables a single program to call multiple providers by model name.

## Example: Multi-Provider Stack

```python
from pydantic import BaseModel

from doeff import do, run
from doeff_llm.effects import LLMChat, LLMStructuredQuery
from doeff_gemini.handlers import gemini_production_handler
from doeff_openai.handlers import openai_production_handler
from doeff_openrouter.handlers import openrouter_production_handler


class Analysis(BaseModel):
    verdict: str
    score: int


@do
def workflow():
    analysis = yield LLMStructuredQuery(
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
    openrouter_production_handler(  # fallback / catch-all
        gemini_production_handler(
            openai_production_handler(workflow()),
        ),
    ),
)
```

## Deprecated Provider-Specific Effect Names

Provider-specific effect classes remain for compatibility:

- `doeff_openai.effects.ChatCompletion`, `StructuredOutput`, `Embedding`
- `doeff_gemini.effects.GeminiChat`, `GeminiStructuredOutput`, `GeminiEmbedding`
- `doeff_openrouter.effects.RouterChat`, `RouterStructuredOutput`

They are now deprecated aliases and emit `DeprecationWarning` when instantiated.

## Migration Note

- `LLMStructuredOutput` was renamed to `LLMStructuredQuery`. The old name does not exist in the current API.
- `run()` takes a single `doexpr` argument and returns the raw value. It does not accept `env=`, `handlers=`, or `trace=` keyword arguments. Environment values should be provided via handler composition (e.g. the `Ask` effect and `lazy_ask` handler).
