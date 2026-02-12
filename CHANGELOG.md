# Changelog

## Unreleased

### Added

- Added new workspace package `doeff-llm` with unified effects:
  - `LLMChat`
  - `LLMStreamingChat`
  - `LLMStructuredOutput`
  - `LLMEmbedding`
- Added cross-provider example `examples/llm_multi_provider_stacking.py`.
- Added docs page `docs/llm_unified_effects.md` for model-routed handler composition.

### Changed

- Updated `doeff-openai`, `doeff-gemini`, and `doeff-openrouter` handlers to support unified effects.
- Added model-based delegation behavior for stacked handlers.
- Added single-protocol handler entrypoints for Gemini and OpenRouter:
  - `gemini_production_handler` / `gemini_mock_handler`
  - `openrouter_production_handler` / `openrouter_mock_handler`

### Deprecated

- Provider-specific effect classes are now deprecated aliases of `doeff-llm` effects and emit
  `DeprecationWarning` on instantiation.
