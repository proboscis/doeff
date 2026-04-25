# Decisions

## D1: Keep the `WithHandler` shim permanently (scope A)
**Choice**: `WithHandler(h, body)` remains callable forever; it routes new-style handlers to `h(body)` and legacy `@do` dispatchers to the Rust `_WithHandlerNode`. PR A4 adds `DeprecationWarning` on both paths but does not remove the shim.
**Why**: The migration cost of rewriting every `WithHandler(h, body)` call site in proboscis-ema + tests is high, and the two-line shim is cheap. User explicitly chose this: "shim is to be kept as is but with deprecation."
**Alternatives considered**:
- Scope B (deprecated + removed in a later release): rejected — same migration cost, with a deadline attached, and no benefit beyond aesthetics.
- Scope C (freeze, no warning): rejected — new code needs a signal that `h(body)` is the preferred form.
**Reversible**: yes — removing the warning or the shim itself is a 10-line change.

## D2: `doeff run PROGRAM` does NOT auto-wrap with scheduler / env-var-ask
**Choice**: CLI calls `run(program)` with whatever the user supplied. Responsibility for `(scheduled ...)` and `(env-var-ask ...)` wrapping is the `defp` author's.
**Why**: User chose option A ("A + convenience helper"). The contract "PROGRAM is handlers pre-applied" is worth the boilerplate — consistency across `-c` / `--hy` / PROGRAM matters more than saving two wrapper calls.
**Alternatives considered**:
- B: auto-install `scheduled` only (scheduler is always needed). Rejected — still breaks the pre-applied contract.
- C: auto-install `scheduled` + `env-var-ask` (ambient secrets). Rejected — users who want to test *without* env var access would have to fight the CLI.
**Reversible**: yes. A future `standard-env` convenience helper (backlog #8) can close the boilerplate gap without changing the CLI contract.

## D3: `--env` / `--set` / `--interpreter` / `--apply` / `--transform` deprecated (not removed)
**Choice**: These flags stay functional on the Python `--program` path and emit a stderr `DeprecationWarning` with concrete migration examples. When combined with `--hy`, they hard-fail with the same rewrite example.
**Why**: User: "--interpreter like usecase, is still useful for python users so i want to keep that for now, but with deprecation. and for --hy, we want to fail hard if --transform etc is specified."
**Alternatives considered**:
- Immediate removal: rejected — proboscis-ema still uses `--interpreter` in `Makefile` and `k3s_interpreter.hy`.
- Warn only, no hard-fail for `--hy`: rejected — `--hy` users are already writing the Hy-native form; silently accepting legacy flags would dilute the contract.
**Reversible**: yes — the deprecation is pure CLI text.

## D4: `--runner` is a CLI-level concept, not a Hy library concept
**Choice**: The runner callable signature is `(ctx: RunnerContext) -> int`, built into doeff at `doeff.runners.local.run_local` by default. Remote backends (k3s, docker) live in user packages.
**Why**: Runners need the *original* CLI invocation (not a composed Program) to replay inside a pod. That data lives in argparse-land, so the CLI is the right seam. Keeping it a pure function also avoids the "meta-interpreter" confusion where a function named `k3s_interpreter` never actually runs the program locally.
**Alternatives considered**:
- Runner as a `Program -> Program` transform: rejected — a k3s runner never *executes* the program; it serializes ctx.
- Runner as a separate `doeff deploy` subcommand: rejected — discussed earlier in the session; user preferred the `--backend`-style flag idiom over a new subcommand.
**Reversible**: yes, but would require changing every runner to accept a new signature.

## D5: `env_var_ask` lives in `doeff-core-effects` as pure Python
**Choice**: Added as a Python `@do` handler alongside `lazy_ask` — not as a Hy `defhandler` inside doeff-hy.
**Why**: `doeff-core-effects` has no `hy` dependency today. Adding one for a single handler is a large blast radius. Python + manual `lazy-var`-style cache is ~60 lines.
**Alternatives considered**:
- `.hyk` file inside doeff-core-effects: rejected — would force the package to depend on doeff-hy + hy.
- Separate package `doeff-env-handlers` in Hy: rejected — premature; one handler doesn't justify a package.
**Reversible**: yes, can rewrite in Hy later if doeff-hy becomes a runtime dep of doeff-core-effects.

## D6: `lazy_ask` Pass-on-miss is the new default; `strict=True` opts into legacy throw
**Choice**: `lazy_ask(env={})` with a missing key now `Pass(effect, k)`s the ask to the outer handler. Callers that want the old "loud missing-env" behaviour pass `strict=True`.
**Why**: Chain-of-responsibility composition (`env_var_ask → lazy_ask → Unhandled`) is the design target. With the old behaviour, `lazy_ask` would throw before `env_var_ask` ever sees the ask, making the stack unusable for secrets fallback.
**Alternatives considered**:
- New handler `lazy_ask_pass`: rejected — two nearly-identical handlers is worse than a flag.
- Silent fallthrough (no flag): rejected — would silently change semantics for existing `lazy_ask` callers. The `strict=True` flag is the migration lever; `test_lazy_ask::test_missing_key_error_strict` was updated to keep the legacy contract exercised.
**Reversible**: yes — flip the default back if downstream relies on the throw.

## D7: Handler chain is embedded in the UnhandledEffect *message*, not only `__doeff_traceback__`
**Choice**: `pyvm.rs::make_effect_error` now extracts the `["handler", "chain", [names]]` entry from `last_error_context` and prepends `handlers in scope (innermost→outermost): A → B → C` to the RuntimeError message itself.
**Why**: `doeff.traceback.format_default` already renders the chain, but users see the plain `str(exception)` in pytest output, CI logs, and print statements. Folding it into the message means every surface gets it.
**Alternatives considered**:
- Only improve the traceback renderer: rejected — doesn't help silent consumers.
- Add a separate `handlers_in_scope` attribute and let callers format: rejected — future extension (Phase 2 / PR G) will add `reason` per-entry anyway; the message is the canonical surface.
**Reversible**: yes — revert `make_effect_error`.

## D8: Handler-name trimming mirrors `traceback.py` (first `.<locals>.` split) + drop module prefix
**Choice**: `"doeff_core_effects.handlers.lazy_ask.<locals>.handler"` → `"lazy_ask"`.
**Why**: Matches the existing `format_default` renderer so the message and the traceback agree on what a handler is called. Module prefix drop keeps lines short.
**Alternatives considered**:
- Keep full qualname: rejected — noisy, unreadable at the terminal.
- Take AFTER last `.<locals>.`: rejected — surfaces generic names like `handler` for factory-returned handlers (`lazy_ask` factory has inner `handler`).
- Whitelist generic names like `handler`/`_h`/`_make_handler`: attempted, too brittle; scrap.
**Reversible**: yes, single function.

## D9: PR G (Pass with reason) deferred to the next session
**Choice**: Design work on `SPEC-VM-022` not started this session; commit what we have and pick it up later.
**Why**: User: "this is very controversion. lets commit our current change and discuss in next session." VM-core changes to `Pass`/DoCtrl need SPEC alignment with SPEC-VM-020 (OCaml 5) and SPEC-VM-021 (single-owner). The design involves choices that touch the effect dispatch hot path — worth a clean design pass in a fresh session.
**Alternatives considered**:
- Implement now: rejected — too many unresolved design questions (where does the trail live? does defhandler auto-inject reasons? OCaml 5 dialect concerns).
**Reversible**: yes — not implemented, no code cost to revisit.

## D10: `RunnerContext` is a new dataclass separate from `DoeffRunContext`
**Choice**: Added `doeff.cli.run_services.RunnerContext` with the fields the `--runner` contract needs (`program_ref`, `py_source`, `hy_source`, `runner_ref`, `format`, `raw_argv`). Kept the existing `DoeffRunContext` for the legacy interpreter path.
**Why**: `DoeffRunContext.program_ref` is typed `str` (required) — adding `Optional` would break existing callers that rely on non-None, and users' `@dataclass(frozen=True)` subclasses if any. A new dataclass avoids the breaking change; overlap between fields is acceptable.
**Alternatives considered**:
- Extend `DoeffRunContext` with `Optional[...]`: rejected — soft-breaks existing type assumptions.
- Pass raw argparse.Namespace: rejected — runner authors want a stable, documented shape.
**Reversible**: yes — can unify later.

## D11: Prefix for env-var handler is `DOEFF_` (not `__DOEFF__`)
**Choice**: Default `env_var_ask(prefix="DOEFF_")`. User-configurable via kwarg.
**Why**: Shell-friendly, follows 12-factor convention, visually identical to `AWS_*`, `PATH`, etc.
**Alternatives considered**:
- `__DOEFF__` (user initially suggested): rejected — noisy, double-underscore eats keystrokes; the prefix kwarg lets individual projects override (`NAKAGAWA_` etc).
**Reversible**: yes — per-handler kwarg.

## D12: env-var value syntax `{module.path}` triggers lazy import (auto-call if callable)
**Choice**: `DOEFF_KEY=plain-string` → resume with the string. `DOEFF_KEY={myapp.factory}` → `import_symbol("myapp.factory")`, auto-call if callable, `yield` the resulting Program with inner handlers reinstalled.
**Why**: Mirrors the existing `--set key={module.path}` syntax from the old CLI; gives env-var users access to lazy Program evaluation (e.g. `{myapp.make_influxdb_client}`) without redesigning the env-handler model.
**Alternatives considered**:
- Pure-string values only: rejected — can't express `DoeffHistoricalStockPriceServiceProtocol` key bindings or lazy k8s-service-lookup factories.
- Require users to point at pre-instantiated Program constants (`{myapp.p_daily}`): partial — still supported, but users get the ergonomic `@do def factory():` form too.
**Reversible**: yes — remove the auto-call branch.

## D13: env-var cache keyed on `(ask_key, raw_env_value)` with invalidation on raw change
**Choice**: `cache[key] = (raw, resolved)`. On re-Ask, compare `cache[key][0]` with current `os.environ[env_key]`; mismatch → re-evaluate.
**Why**: The user explicitly wanted "env var should be dynamically checked on every Ask, not static once creation" plus "lazy evaluation must be cached so do use lazy-var/lazy-val." Keying on raw value achieves both — the os.environ read happens each call, but the Program evaluation only re-runs when the raw text changes.
**Alternatives considered**:
- Cache by ask_key only, ignore env changes after first resolve: rejected — violates "dynamic re-check".
- No cache (re-eval every Ask): rejected — violates "must be cached".
- Cache invalidation on external signal (e.g. SIGHUP): rejected — overbuilt.
**Reversible**: yes.

## D14: k3s runner reuses legacy helpers instead of duplicating them
**Choice**: `nakagawa/runners/k3s.hy` imports `_build-and-push`, `_submit-and-wait`, `generate-doeff-job-yaml` from the legacy `nakagawa.phase2.interpreters.k3s_interpreter`.
**Why**: Both files live during the migration window; DRY-ing now means there's one image-build + one wait-loop implementation. Retiring the legacy file later (backlog #6) is a mechanical move of the helpers.
**Alternatives considered**:
- Fork the helpers to the new module: rejected — two divergent copies to maintain during the migration.
**Reversible**: yes.
