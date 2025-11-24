# Gemini cost calculation hook

This document describes the generic cost calculation hook for the Gemini
integration. The goal is to let users inject their own pricing logic while still
providing a default implementation that knows about the built-in pricing table.

## Types

- `GeminiCallResult`
  - `model_name: str`
  - `payload: dict` — includes request payloads (user-facing and API), request
    summary, usage (if extracted), timing, operation, etc.
  - `result: Result[Any, Exception]` — wraps the API call outcome (Ok with the
    raw response object, Err with the exception).

- `GeminiCostEstimate`
  - `cost_info: CostInfo`
  - `raw_usage: dict | None` (optional, for diagnostics)

- `gemini_cost_calculator`: `KleisliProgram[GeminiCallResult, GeminiCostEstimate]`

## Resolution & fallback

1. Try `Ask("gemini_cost_calculator")`. If provided, call it.
2. If the injected calculator is missing or raises, fall back to the built-in
   `gemini_cost_calculator__default` (uses the known pricing table).
3. If both fail, raise an error with guidance on supplying a calculator.

## Default calculator

`gemini_cost_calculator__default` uses the known pricing table to compute
`CostInfo` from usage. It expects usage fields like `text_input_tokens`,
`text_output_tokens`, `image_input_tokens`, `image_output_tokens`. Unknown
fields are ignored.

Included pricing (USD per 1M tokens):
- `gemini-3-pro-image-preview` (Nano Banana Pro): text input 2.00, text output 12.00, image input 2.00, image output 120.00
- Other Gemini 1.5/2.x models per existing table in `costs.py`.

## How to provide a custom calculator

Bind a Kleisli program in the environment:

```python
env = {
    "gemini_cost_calculator": my_cost_calculator,  # KleisliProgram
}
result = run_with_env(my_program(), env=env)
```

Your `my_cost_calculator` receives a `GeminiCallResult` and must return a
`GeminiCostEstimate`. Raise or fail to stop the call; return normally to supply
cost.
