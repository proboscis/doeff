# Session Context

## Goal
Complete the "Hy-first" direction for doeff's CLI and handler ecosystem:
- Land the PRs (A1, A3, A4, B, C, D, E, F) that migrate Python handlers to
  defhandler, redesign `doeff run`, introduce the `env_var_ask` handler, and
  ship `--runner` for deployment backends.
- Leave PR G (SPEC-VM-022 "Pass with reason") as a next-session design task —
  the VM change is too controversial to rush.

## Background
doeff is an OCaml 5-aligned algebraic-effects framework with a Rust VM and
Python/Hy bindings. Prior work (not this session) rebuilt the VM and ported
the subpackages. This session continued the direction of making Hy the
*primary* authoring surface for handlers, pipelines, and entrypoints, with
Python kept for interop.

The broader motivation:
- User-level memory pins the convention "entrypoints are fixed constants,
  not generic CLI entrypoints" and "secrets only in `~/.doeff.py`"; the
  old `doeff run --program X --interpreter Y --env Z --set K=V` surface
  fights both.
- PR A1 (earlier in session) made `defhandler` return a `Program -> Program`
  function; the natural next step is a CLI whose PROGRAM contract is "fully
  handler-applied" so composition lives in Hy, not in flags.

## Scope
Two repos, multiple packages.

**doeff repo** (branch: `main`):
- `doeff/program.py` — `WithHandler` shim (PR A1/A4)
- `doeff/__main__.py` — CLI entry, new `--runner`, deprecations, rich errors
  (PR B/C)
- `doeff/cli/run_services.py` — added `RunnerContext` dataclass (PR C)
- `doeff/cli/hy_runner.py` — inline Hy evaluator for `--hy` (PR B)
- `doeff/runners/` — new package: `local.py` builtin (PR C)
- `packages/doeff-core-effects/doeff_core_effects/handlers.py` —
  `env_var_ask`, `lazy_ask(strict=...)` (PR D)
- `packages/doeff-hy/src/doeff_hy/handle.hy` — defhandler macro (PR A1,
  landed earlier)
- `packages/doeff-vm/src/pyvm.rs` — UnhandledEffect chain names (PR E)
- `tests/cli/`, `tests/effects/`, `tests/test_withhandler_shim_deprecation.py`
  — test coverage
- `.claude/projects/.../memory/design_doeff_run_redesign.md` — design memory

**proboscis-ema repo** (branch: `codex/package-split-phase1`):
- `packages/nakagawa/src/nakagawa/runners/k3s.hy` — new k3s_sim runner (PR F)
- `packages/nakagawa/tests/unit/test_k3s_runner_helpers.py` — unit tests
- `packages/nakagawa/Makefile` — `run-k3s` target switched to new runner
- Already-migrated PR A3 handlers (from earlier session batches 4–11)

## Starting State
- doeff `main` at `dc797fef` (fix(doeff-hy): lazy init injection misses
  references inside tuple/set/dict literals).
- Proboscis-ema `codex/package-split-phase1` at `ad79c5bf` (batch 11 of the
  handler migration, landed in the previous session).
- PR A1 commit `436369fd` had already been pushed before this session
  started; all subsequent commits here are within this session.

## Current State
All committed and pushed.

**doeff `main` (ahead 7 commits from session start):**
- `436369fd` PR A1 — defhandler is Program→Program + WithHandler shim (from
  earlier session, referenced here for completeness)
- `c379c0a4` PR B step 1 — `--hy` CLI flag
- `167758f0` PR B step 2 — auto-require handle/defhandler in --hy prelude
- `a8a99b64` PR B step 3 — tests for PR-A1 defhandler idioms under --hy
- `57498be1` PR D — env_var_ask handler + lazy_ask Pass-on-miss
- `88cb24e4` PR E — UnhandledEffect includes handler chain names
- `6912d589` PR C — --runner flag + rich migration errors
- `d907d6a3` PR A4 — WithHandler shim emits DeprecationWarning

**proboscis-ema `codex/package-split-phase1`:**
- `696d51d7` PR F — nakagawa.runners.k3s.k3s_sim + Makefile migration

Next session starts here — every PR C/D/E/F/A4 change is on origin.
Tests for each PR are green (`tests/cli/test_cli_hy_flag.py`,
`test_cli_deprecations.py`, `test_cli_runner.py`, `test_cli_hy_error_messages.py`,
`tests/effects/test_env_var_ask.py`, `test_unhandled_effect_chain.py`,
`tests/test_withhandler_shim_deprecation.py`).

Pre-existing failures in `tests/cli/test_cli_run.py` /
`test_doeff_run_context.py` (45 failures) are unrelated — verified by
stashing the PR C/D/E changes and re-running.

## Key Files

### doeff repo

| File | Role |
|---|---|
| `doeff/program.py` | `WithHandler` shim function — PR A4 added `DeprecationWarning` on both paths (new-style marked fn → `h(body)` preferred; legacy @do dispatcher → defhandler migration hint). `WithHandlerType` alias stays non-deprecated. |
| `doeff/__main__.py` | CLI entry. Adds `--runner` flag (default `doeff.runners.local.run_local`), `_DEFAULT_RUNNER`, `_HY_FLAG_REWRITE` (rich conflict errors), `_LEGACY_FLAG_DEPRECATION`. Captures `raw_argv` on `args`. |
| `doeff/cli/hy_runner.py` | `evaluate_hy_source(source) -> HyEvalResult` — wraps Hy with `(require doeff-hy.macros [do! <-])` + `(require doeff-hy.handle [defhandler handle])` prelude. |
| `doeff/cli/run_services.py` | Added `RunnerContext` frozen dataclass: `program_ref`/`py_source`/`hy_source`/`runner_ref`/`format`/`raw_argv`. |
| `doeff/runners/__init__.py` | Package init, re-exports `run_local`. |
| `doeff/runners/local.py` | Built-in local runner. Resolves `hy_source` > `py_source` > `program_ref` and calls `doeff.run(program)`. Renders text/json output. |
| `packages/doeff-core-effects/doeff_core_effects/handlers.py` | `lazy_ask(env, *, strict=False)` — PR D made miss default to `Pass` instead of `ResumeThrow(KeyError)`; strict=True keeps legacy. Added `env_var_ask(prefix="DOEFF_")` — dynamic os.environ lookup, `{module.path}` lazy Program import with per-key semaphore and raw-value invalidation. |
| `packages/doeff-core-effects/doeff_core_effects/__init__.py` | Re-exports `lazy_ask`, `env_var_ask`. |
| `packages/doeff-vm/src/pyvm.rs` | `make_effect_error` now calls `extract_handler_chain` on `last_error_context` and formats `handlers in scope (innermost→outermost): A → B → C` into the RuntimeError message. Name trimming: first `.<locals>.` split + module prefix drop + consecutive dedup. |
| `tests/cli/test_cli_hy_flag.py` | 15 tests — PR B `--hy` CLI flag + handle/defhandler idioms. |
| `tests/cli/test_cli_hy_error_messages.py` | 5 tests — PR C hard-fail + rewrite example for every legacy flag combined with `--hy`. |
| `tests/cli/test_cli_deprecations.py` | 3 tests — PR C stderr deprecation for `--interpreter` / `--set` on the legacy path; no warning under `--hy`. |
| `tests/cli/test_cli_runner.py` | 4 tests — PR C default runner, explicit `doeff.runners.local.run_local`, custom runner round-tripping `RunnerContext`, unknown runner error. |
| `tests/cli/cli_runner_assets.py` | Test-only `ctx_spy_runner` that dumps the RunnerContext as JSON. |
| `tests/effects/test_env_var_ask.py` | 12 tests — PR D env_var_ask plain strings / `{path}` imports / cache / concurrency; plus lazy_ask Pass-on-miss and strict legacy. |
| `tests/effects/test_unhandled_effect_chain.py` | 3 tests — PR E handler chain name in message (single / stacked / innermost-first order). |
| `tests/test_withhandler_shim_deprecation.py` | 6 tests — PR A4 DeprecationWarning on both shim paths; shim still routes; `WithHandlerType` alias not deprecated. |

### proboscis-ema repo

| File | Role |
|---|---|
| `packages/nakagawa/src/nakagawa/runners/__init__.py` | Package init for the new runners namespace. |
| `packages/nakagawa/src/nakagawa/runners/k3s.hy` | `k3s_sim(ctx: RunnerContext) -> int` — strips `--runner` from `raw_argv`, replays `uv run --no-sync doeff run ...` inside a k3s pod, auto-injects `cllm_sim_paper_interpreter` if absent, validates `nakagawa.session_id`. Reuses `_build-and-push` / `_submit-and-wait` / `generate-doeff-job-yaml` from the legacy `k3s_interpreter.hy`. |
| `packages/nakagawa/tests/unit/test_k3s_runner_helpers.py` | 15 unit tests for argv rewrite helpers (strip, reconstruct, session_id assert, interpreter default, agent detect, program extract). |
| `packages/nakagawa/Makefile` | `run-k3s` target switched from `--interpreter ...k3s_sim_interpreter` to `--runner nakagawa.runners.k3s.k3s_sim`. Legacy interpreter still works for the migration window. |
| `packages/nakagawa/src/nakagawa/phase2/interpreters/k3s_interpreter.hy` | Unchanged — legacy meta-interpreter kept in place; the new runner imports its helpers. |

### Memory

| File | Role |
|---|---|
| `~/.claude/projects/-Users-s22625-repos-doeff/memory/design_doeff_run_redesign.md` | Captures every decision from this session's design discussions (CLI shape, env-var-ask design, lazy-ask Pass semantics, UnhandledEffect diagnostics). Seeded the implementation PRs. |
| `~/.claude/projects/-Users-s22625-repos-doeff/memory/MEMORY.md` | Index updated to point at the design doc. |
