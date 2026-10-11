# Changelog

## Unreleased

### Added

- `doeff-traverse`: added `ItemOutcome(traverse, label, total, outcome, item)` — `sequential`, `parallel` and
  `parallel_fail_fast` report each item's outcome (`ok` / `failed` / `skipped` / `carried`) under the handlers between
  the Traverse and themselves, while one of those handlers names `ItemOutcome`; and `progress_bars(make_bar)`, which
  counts them into one bar per Traverse (`tqdm.tqdm` fits) — the bar now reaches its total and closes when items fail
  (card ki-e3a936788353).

- Added `HttpRequest`, `HttpResponse`, and `HttpError` to `doeff-core-effects`.
- Added Hy `defhandler`-backed `http_production_handler` with owned `httpx.AsyncClient` lifecycle
  cleanup, JSON body serialization, retry/backoff, timeout/redirect forwarding, and `slog` request
  events.
- Added Hy `defhandler`-backed `http_fixture_handler` for record/replay HTTP fixtures keyed by
  request identity. Record mode now composes the fixture recorder with the production HTTP handler,
  so callers do not need to install a separate transport handler to capture fixtures.
- Added doeff-hy HTTP verb wrappers: `http-get`, `http-post`, `http-put`, `http-delete`,
  and `http-head`.
- Added new workspace package `doeff-llm` with unified effects:
  - `LLMChat`
  - `LLMStreamingChat`
  - `LLMStructuredOutput`
  - `LLMEmbedding`
- Added cross-provider example `examples/llm_multi_provider_stacking.py`.
- Added docs page `docs/llm_unified_effects.md` for model-routed handler composition.
- `doeff-claude-code`: added `ClaudeReplaceSessionCredential(session_id, credential)` — replaces the lent credential of a
  session's live Claude CLI process (answers `CredentialReplaced` | `NoSuchSession` | `SessionProcessDown` |
  `HomeCredentialSession`). The CLI uses the new access token from its next API call, also in the middle of a turn
  (card ki-4ffdafa021e5).
- `doeff-agents`: added `ReplaceSessionCredentialEffect(handle, credential_ref)` (and `ReplaceSessionCredential`). The
  headless Claude handler redeems the reference with `RedeemTurnCredentialEffect` and replaces the live process's
  credential (answers `SessionCredentialReplaced` | `SessionProcessNotLive` | `SessionOnHomeCredential` |
  `ReplacementCredentialUnavailable`); the headless Codex handler refuses it with `AgentCapabilityUnsupportedError`.

### Changed

- `doeff-claude-code`: a lent credential now reaches the Claude CLI as the `.credentials.json` (mode 0600) of a config
  dir of the process's own (`<CLAUDE_CONFIG_DIR>/.credential-homes/<random>/`, mode 0700, whose other entries are
  symlinks to the shared config dir), not as `CLAUDE_CODE_OAUTH_TOKEN` in the child env; the dir is removed when the
  process goes down. `ClaudeSessionSpec.credential_expires_at` is replaced by `ClaudeSessionSpec.credential`
  (`LentCredential` — access token, expiry, plan kind and rate limit tier); the launch key no longer includes the token
  or its expiry, so a turn whose credential alone changed reuses the live process and writes the new credential into
  its file. A spec without a credential still launches on the shared config dir's own credentials.
  `argv.process-env` takes the process's config dir as a second argument.
- `doeff-agents`: the headless Claude handler passes the redeemed `TurnCredential` as the spec's `LentCredential`
  (plan kind and rate limit tier go into the same file) instead of `CLAUDE_CODE_OAUTH_TOKEN` /
  `CLAUDE_CODE_SUBSCRIPTION_TYPE` / `CLAUDE_CODE_RATE_LIMIT_TIER` in the child env. `GH_TOKEN` is unchanged.

- `http_fixture_handler` now keys a fixture by every request field that can change the answer (headers, timeout,
  `max_retries`, `follow_redirects`, `failures_as_values` in addition to method / url / params / body), and records and
  replays failures too (an `HttpFailed` value, or the transport error raised again). Fixture files recorded before this
  change no longer match and must be recorded again.
- `Cancel(task)` now throws `TaskCancelledError` into a started task at its suspension point, so
  its `except` / `finally` blocks run (and may perform effects) before waiters observe the
  cancellation. Previously the task's continuation was dropped and its cleanup never ran.
- Updated `doeff-openai`, `doeff-gemini`, and `doeff-openrouter` handlers to support unified effects.
- Added model-based delegation behavior for stacked handlers.
- Added single-protocol handler entrypoints for Gemini and OpenRouter:
  - `gemini_production_handler` / `gemini_mock_handler`
  - `openrouter_production_handler` / `openrouter_mock_handler`

### Deprecated

- Provider-specific effect classes are now deprecated aliases of `doeff-llm` effects and emit
  `DeprecationWarning` on instantiation.
