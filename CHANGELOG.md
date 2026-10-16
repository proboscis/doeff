# Changelog

## Unreleased

### Added

- `doeff-traverse`: added `ItemOutcome(traverse, label, total, outcome, item)` — `sequential`, `parallel` and
  `parallel_fail_fast` report each item's outcome (`ok` / `failed` / `skipped` / `carried`) under the handlers between
  the Traverse and themselves, while one of those handlers names `ItemOutcome`; and `progress_bars(make_bar)`, which
  counts them into one bar per Traverse (`tqdm.tqdm` fits) — the bar now reaches its total and closes when items fail
  (card ki-e3a936788353). Consumers pass each report on (only the Traverse handler answers it), so several compose in
  any order; one that answers a report or throws into it fails the Traverse with `ItemOutcomeNotPassedError` (design
  check lt-H07C2RG4T2BM59EENGQWQHSJKH).

- `doeff-linter`: added the critical rule `DOEFF210` (Program Sleeps On The Wall Clock) — a Hy program (`defk` / `defp`)
  must not call `time.sleep` / `asyncio.sleep`; it waits by yielding doeff-time's `Delay` (card ki-3c3d85253c2c). Handler
  implementations may sleep: a `defhandler` clause, a definition taking `[effect k]`, and a helper that declares
  `:role "foundation"` in its contract's `:tags`. A literal `0` (yield once) is not counted; `deff` / `defn` are plain
  callables and are not judged. The rule runs only where a repo declares its program places in the new section
  `[tool.doeff-linter.program_sleep]` (`paths`, `exclude`, `exclude_parts`); doeff declares `doeff` and `packages`
  without `tests` / `examples`.
- `doeff-linter`: added the critical rule `DOEFF211` (Test Sleeps On The Wall Clock) — a Hy test file must not call
  `time.sleep` / `asyncio.sleep` (nor reword it as `(.wait (threading.Event) seconds)` or `(.sleep time seconds)`)
  unless it declares that real time is its subject (card ki-bb3ee40d0af2). The declaration is the pytest mark
  `realtime` in one of three forms: a deftest's meta map `{:marks ["realtime"]}`, a plain test function's decorator
  `(defn [pytest.mark.realtime] name [] …)`, or a top-level `(val pytestmark pytest.mark.realtime)` for a whole file.
  The rule runs only where a repo declares its test places in the new section `[tool.doeff-linter.test_sleep]`
  (`parts`, `names`, `exclude`); doeff declares every Hy file under a `tests` dir and lists each declared test with
  its reason in `tests/realtime_tests.toml`. The rule's hits are not lowered by the known-violation registry.
- `.semgrep.yaml`: the new ERROR rule `doeff-agents-no-sleep-effect-hy` rejects a wait effect defined again in
  doeff-agents' Hy source (a `defclass` / `defeffect` whose name contains `Sleep` / `Delay`, or a constructor named
  `sleep` / `…-sleep`).

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

- `doeff run` starts without Hy: its entry point read `DOEFF_PROFILE` through Hy's process effects
  (`doeff_core_effects.os_process` / `process_effects`, since 6ceb0db53), so with only the four core packages
  installed (`doeff`, `doeff-vm`, `doeff-core-effects`, `frozendict`) every call — `--help` included — died with
  `No module named 'doeff_hy'`. It now asks `env_var_ask` (Python) for `DOEFF_PROFILE`, so `doeff run --help`,
  `--program` (a Python program), `-c`, `--profile` and `DOEFF_PROFILE` work without Hy. Hy is still needed for `--hy`
  and for a `.hy` program. `doeff.cli.profiling.profiling_config_from_env(env)` is replaced by
  `profiling_config_from_environment()`.
- `doeff-core-effects`: an `Ask` whose key the env does not hold (`reader`, `lazy_ask(strict=True)`) raises
  `MissingEnvKeyError`, now its own subclass of `KeyError` (`except KeyError` still catches it; `args` is `(key,)`;
  `.key` and `.env_keys` name the key and the env's keys). Its message names the env's keys (the first 8 of a large
  env) and the difflib close names of the key, keeps its newlines in `str()` (in 0.5.0 they were folded into `\n`), and
  no longer recommends the legacy CLI flags `--set` / `--env` / `--interpreter` to a program run from Python.
  `doeff run` adds the README's `--hy` form (`((lazy-ask :env {"key" VALUE}) p)`) when the error reaches it. Before,
  `doeff.MissingEnvKeyError` was `KeyError` itself.
- `doeff-core-effects`: `Semaphore(...)` raises `TypeError` again, as docs 09, 13 and 21 and SPEC-EFF-014 say; the
  2026-03 scheduler rewrite had made the base class buildable by hand. Create a semaphore with
  `sem = yield CreateSemaphore(permits)`. A hand-made handle never worked: it named no semaphore of the run
  (`AcquireSemaphore` failed with `KeyError`) or silently shared the permits of the run's semaphore with the same
  number. Handles from `CreateSemaphore`, `isinstance` checks and subclasses whose `__init__` calls
  `super().__init__(sem_id)` (doeff-cluster's `ClusterSemaphore` and `ReplaySemaphore`) are unchanged.
- `doeff-core-effects`: a `Listen` inside another `Listen`'s sub-program is handled. In 0.5.0 `listen_handler` did not
  install itself again around the sub-program (as `try_handler` and `local_handler` do), so the inner `Listen` failed
  with `UnhandledEffect: no handler found for effect: Listen` unless `listen_handler` was installed once more per
  nesting level. One `listen_handler` now serves every level: the inner `Listen` captures only its own scope, and the
  outer `Listen` captures its whole scope, including the inner scope's entries. `docs/03-basic-effects.md` (Listen
  Composition Rules) runs the nested example; `specs/effects/SPEC-EFF-003-writer.md` is rewritten for the current
  `(value, collected)` result and the handler's mechanism (it described `ListenResult`, `ListenFrame` and a `__log__`
  store key that no longer exist) (found while checking chapter 03, card ki-59841195eb05).
- `doeff-hy`: `defmcp-tool` refuses, when it expands, a body that calls the tool's own name
  (`(defmcp-tool submit-order … (submit-order …))`, spelled `submit-order` or `submit_order`, at any depth outside a
  quote). The expansion binds that name to the `McpToolDef`, so the call reached the tool instead of a `defk` of the
  same name — even one defined first — and the handler failed with `TypeError: 'McpToolDef' object is not callable`
  when it ran. The example in `docs/12-mcp-tools.md` and the docstrings of `defmcp-tool` and `doeff.mcp` named the
  tool after the `defk` it called; the example now names the tool `submit-order-tool`, defines the `defk`, and runs
  the handler. `tests/test_docs_page_examples.py` runs that example and the From Python one and compares what they
  print; the page's other fences, which need Claude Code or names the page does not define, are marked `excerpt`
  (or `text` for the diagrams) (card ki-896fc4bca8cb).
- `doeff-vm`: `EffectBase` (`doeff.Effect`) raises `TypeError` for constructor arguments that no `__init__` keeps —
  `Effect("fetch", url=...)`, or a subclass without an `__init__` called with arguments. 0.5.0 dropped them without a
  word, so the handler got an effect with neither the name nor the arguments (docs/MARKERS.md's examples did this).
  Subclasses with an `__init__` (`@dataclass` effects) and calls without arguments are unchanged, and the stub's
  `EffectBase.__init__` takes no arguments, so pyright flags the same call (card ki-450280be9dfe).
- `doeff` CLI: `doeff run` no longer prints `[DOEFF][PROFILE]` timing lines unless asked. `doeff run --profile` or
  `DOEFF_PROFILE=1` prints them on stderr as before (`DOEFF_PROFILE` empty or `0` leaves them off).
  `DOEFF_DISABLE_PROFILE` is no longer read, since profiling is off by default, and `doeff-ml-nexus`'s docker handler
  no longer passes it to the container.
- `doeff`, `doeff-vm`, `doeff-core-effects`, `doeff-hy`, `doeff-hy-pytest`: the five core packages share one version
  number, `0.5.0` (from `doeff` 0.4.1, `doeff-vm` 0.2.0 and 0.1.0 for the other three), and each one's dependencies on
  the others are limited to that minor series (`>=0.5.0,<0.6`), so one number tells which releases go together. The
  workspace sources still resolve them to the working tree. `tests/test_core_packages_version.py` fails when any of the
  eight places holding the number (the five `pyproject.toml` files, `doeff.__version__`, and `doeff-vm`'s `Cargo.toml`
  and `Cargo.lock`) or any core-to-core dependency drifts (card ki-4f3135e2be74).
- `doeff`, `doeff-vm`, `doeff-core-effects`, `doeff-hy`, `doeff-hy-pytest`: each declares its license as the PEP 639
  SPDX expression `license = "MIT"` with `license-files = ["LICENSE"]` and carries a copy of the root MIT `LICENSE`, so
  every wheel and sdist ships it (`.dist-info/licenses/LICENSE`, core metadata `License-Expression: MIT`); the
  deprecated `License ::` classifier is gone. The build backends now require `hatchling>=1.27` / `maturin>=1.9.3`
  (the first releases that write these fields). The `doeff` sdist's include patterns are anchored to the repository
  root, so it no longer picks up other packages' `LICENSE`, `README.md` and `doeff/` directories (card ki-2dbb91f7e2c8).
- `doeff-openai`, `doeff-gemini`, `doeff-openrouter`, `doeff-seedream`: the wait between retries (and Gemini's wait for an
  uploaded file to become ACTIVE) yields doeff-time's `Delay` instead of `Await(asyncio.sleep(...))`, and the four
  packages now depend on `doeff-time` (card ki-94380dba532f). **Callers must install a time handler**:
  `async_time_handler()` inside `await_handler()` waits on the wall clock; `sim_time_handler()` lets a test run the
  retry path without sleeping. Without one, the first retry fails with an unhandled `DelayEffect`.
- `doeff-flow`: `run_result` and `run_workflow` now install `doeff_time.async_time_handler()` inside `await_handler()`,
  so a workflow that yields `Delay` / `GetTime` (or any doeff-time effect) is answered on the wall clock; before, it failed
  with an unhandled effect (card ki-94380dba532f). The examples wait with `yield Delay(...)` instead of `time.sleep`.
  Examples `01`, `02`, `03`, `04`, `05` and `06` run to the end again: their `slog` calls now carry the `msg` that
  `slog` requires (`02`, `03`, `04` ended with `Err` before), `01` and `06` use `run_result` in place of the removed
  `default_handlers` / `run_sync`, and `05` uses an `@do` cache handler that yields `Pass` (not the removed
  `Delegate`) and yields the `Await` that `SQLiteStorage.get` / `put` return.
- `.semgrep.yaml`: `doeff-no-sleep-in-programs` covers `packages/*/examples/` and `examples/` as well as the source.
- `doeff-agents`: programs wait by yielding doeff-time's `Delay`; the package's own wait effects are removed (card
  ki-3c3d85253c2c). `doeff_agents.io_effects.Sleep` / `sleep` and `doeff_agents.sessionhost.effects.ClockSleep` /
  `clock-sleep` no longer exist — yield `Delay(seconds)` from `doeff_time`. `driver-io-handler` and `real-substrate` do
  not answer waits: the roots install doeff-time's `sync_time_handler()` (`run_driver_io`, the session host's
  `run-hosted`), and **a caller that builds its own stack around `driver-io-handler` must add a time handler**. The
  fakes (`fake-driver-io-handler`, `recorded-spawn-handler`) answer `DelayEffect` on their own clock / ledger.
- `.semgrep.yaml`: the new ERROR rule `doeff-no-sleep-in-programs` rejects `time.sleep` / `asyncio.sleep` inside a
  `@do` program under `doeff/` and the packages' source (a handler taking `(effect, k)` may sleep). The rule
  `doeff-no-sleep-in-tests` is now ERROR and also catches an un-awaited `asyncio.sleep(...)` such as
  `yield Await(asyncio.sleep(1.5))`; tests that declare `realtime` are listed with a reason in
  `tests/realtime_tests.toml`.
- `.semgrep.yaml`: `doeff-no-sleep-in-tests` now also reaches test-named files outside a `tests/` directory
  (`test_*.py`, `*_test.py`, `conftest.py`) and catches the `threading.Event().wait(seconds)` spelling of a sleep
  (card ki-6aa7cb5ffb83). The only ways out are the two tables of `tests/realtime_tests.toml`: `[tests]` (the `realtime`
  marks) and `[suites]` (the directories in the rule's `paths.exclude` — today the black-box `doeff-agents`
  conformance suite). A `nosemgrep` comment on the rule now fails `tests/semgrep/test_realtime_tests_registry.py`.
- `doeff-traverse`: a Traverse handler now stops the items it started when it stops (card ki-e0fd319d5b75). When `Gather`
  raises, `parallel` and `parallel_fail_fast` cancel the item tasks left, wait for them to unwind, and raise the
  original exception. Before, items kept running after a `parallel_fail_fast` failure that the caller caught, and new
  ones even started; cancelling the task that runs a Traverse stopped only that task, not its items. The stopped items
  are reported with the new `ItemOutcomeKind` `cancelled`, so a Traverse that runs an item reports exactly `total`
  outcomes however it ends. `progress_bars` counts a cancelled item without advancing the bar, so the bar of a stopped
  Traverse closes at n < total; before, it stayed open and pushed later bars one line down.
  **Behaviour change**: `TaskCancelledError` out of an item is no longer that item's failure — it stops the Traverse
  and propagates (all three handlers). That includes an item that `Wait`s on another, cancelled task: wrap that `Wait`
  in `Try` to keep the item going. Before, `sequential` caught the cancellation of its own task as one failed item and
  ran the rest.
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
