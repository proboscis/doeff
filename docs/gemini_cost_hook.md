# Gemini Cost Calculation

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

Recommended stack:

```python
handlers = [
    default_gemini_cost_handler,   # built-in known-model pricing
    custom_cost_handler,           # optional overrides / unknown models
    *default_handlers(),
]
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
