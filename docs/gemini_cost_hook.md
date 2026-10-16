# Gemini Cost Calculation

> **Needs components outside `pip install doeff`.** This page describes `doeff-gemini`, which is
> not on PyPI. Install it from a checkout of this repository together with the repository
> packages it depends on (`doeff-time`, `doeff-llm`, `doeff-image`). pip takes everything else
> from PyPI:

```bash
git clone --depth 1 https://github.com/proboscis/doeff.git
pip install doeff ./doeff/packages/doeff-time ./doeff/packages/doeff-llm \
  ./doeff/packages/doeff-image ./doeff/packages/doeff-gemini
```

Gemini cost tracking is effect-based.

## Effect

`track_api_call` emits:

- `GeminiCalculateCost(call_result: GeminiCallResult) -> GeminiCostEstimate`

The effect is handled via the normal handler stack.

## Default handler

Use `doeff_gemini.handlers.default_gemini_cost_handler` to enable built-in
pricing for known models.

- Known model: resume with `GeminiCostEstimate`.
- Unknown model: `Delegate()` so another handler can handle it.
- If no handler handles the effect: runtime fails fast (unhandled effect).

## Composition

Compose handlers around the program directly:

```python
from doeff import do, run
from doeff_core_effects.handlers import writer, state

prog = default_gemini_cost_handler(   # built-in known-model pricing
    custom_cost_handler(              # optional overrides / unknown models
        state()(writer(program))
    )
)
result = run(prog)
```

To fully replace default pricing, omit `default_gemini_cost_handler`.

## Built-in pricing table

From `https://ai.google.dev/pricing` (checked 2026-02-28), plus legacy 1.5
rates retained for compatibility in `doeff_gemini.costs`.

- `gemini-2.5-pro`: input $1.25 / output $10.00 per 1M tokens (<=200K input),
  input $2.50 / output $15.00 (>200K input)
- `gemini-2.5-flash`: input $0.30 / output $2.50 per 1M tokens (<=200K input),
  input $0.60 / output $2.50 (>200K input)
- `gemini-2.0-flash`: input $0.10 / output $0.40 per 1M tokens,
  image-output equivalent $30.00 per 1M image tokens (`$0.039/image`)
- `gemini-2.0-flash-lite`: input $0.075 / output $0.30 per 1M tokens
- `gemini-1.5-pro` (legacy compatibility): input $1.25 / output $5.00 per 1M
  tokens (<=128K input), input $2.50 / output $10.00 (>128K input)
- `gemini-1.5-flash` (legacy compatibility): input $0.075 / output $0.30 per
  1M tokens (<=128K input), input $0.15 / output $0.60 (>128K input)
