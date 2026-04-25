# Feedbacks

## Corrections

- **CLI stacking order**: user corrected my claim that `(-> p env-var-ask (lazy-ask ...))` would check env-var first. `->` threading inserts left as innermost, so the first transform (`env-var-ask`) is actually the *outermost* in the handler chain. Ask resolution runs innermost-first, so lazy-ask (closer to the program) sees the effect before env-var-ask. Fixed the design doc accordingly.

- **Tests don't interfere with A option**: I worried that env-var-first priority would break tests (env vars leaking through `Local` overrides). User pushed back: "i dont get why you think A will interfere with test..." Retracted the concern — tests isolate env vars via `monkeypatch.delenv` and CI doesn't inherit dev shells.

- **lazy evaluation must be cached, but env check must be dynamic**: after I proposed a static `env_var_dict()` helper that reads `os.environ` once at startup, user corrected: "env var should be dynamically checked on every Ask, not static once creation." Redesigned `env_var_ask` to call `os.environ.get(...)` per Ask; cache only the `{module.path}` resolved Program value, keyed on the raw env-var string so mutation invalidates the cache.

- **Use `defhandler`, never `@handler`**: historical correction from the summary — user rejected a `@handler` Python decorator I'd previously prototyped: "I said defhandler, never @handler or anything". Relevant now because PR A4's deprecation message specifically points users at `defhandler`, not `@handler`.

## Preferences

- **Error messages must show a complete, copy-pasteable example**: "All failures around this change, must show exact example for how to resolve to the user. clear error message with complete example." Implemented in `_HY_FLAG_REWRITE` + `_LEGACY_FLAG_DEPRECATION` — every deprecated or rejected flag produces a before/after block with ready-to-run shell invocations.

- **Gradual migration for the Python path, hard fail for the Hy path**: "`--interpreter` like usecase, is still useful for python users so i want to keep that for now, but with deprecation. and for `--hy`, we want to fail hard if `--transform` etc is specified." Python users see `DeprecationWarning` + continue; `--hy` users get an `exit 2` error.

- **No auto-wrap on `doeff run PROGRAM`**: user picked option A — "PROGRAM = handlers pre-applied" is the contract. CLI calls `run(program)` as-is.

- **Runner contract is `(ctx) -> int`**: user agreed on the `RunnerContext` field list (`program_ref`, `py_source`, `hy_source`, `runner_ref`, `format`, `raw_argv`) and the built-in local runner default.

- **Don't touch `uv pip`**: the hook blocks it system-wide; use `uv add --dev <pkg>` or edit `pyproject.toml` + `uv sync`.

- **Pipe-threading intuition for transforms**: "in my intuition transform is like a piping operation so `p |> t1 |> t2`". User sees the first transform as applied first (innermost). Relevant because the `_HY_FLAG_REWRITE` migration examples use `(-> p t1 t2)` — matches the user's mental model.

- **CLI surface should shrink to three forms**: `doeff run PROGRAM` / `doeff run --hy SRC` / `doeff run -c CODE`, with `--runner` as the only "modifier". M × N composition (program × interpreter) is expressed inside Hy source via `import` + `->`, not via orthogonal CLI flags.

- **Defer SPEC-VM-022 to next session**: "this is very controversion. lets commit our current change and discuss in next session." Documented as backlog #1.

## Guidance

- **Existing `~/.doeff.py` convention is for secrets only**: related memory entry `feedback_doeff_py_secrets_only.md`. PR D's env-var-ask acts as the replacement for the secrets path; sim parameters stay in `.hyp` source.

- **Entrypoints are fixed constants**: memory `feedback_question_format.md` cousin — "entrypoints are fixed constants, not generic CLI entrypoints". Drove the decision to push `M × N` combinatorial setups into Hy `defp` instead of CLI flags.

- **Ctx-requiring meta-interpreters are deploy tools**: the user accepted my framing that `k3s_interpreter` (old) was really a deploy tool pretending to be an interpreter. This is why PR F reframes it as `nakagawa.runners.k3s.k3s_sim` under the new `--runner` contract.

- **Prefer `handle` macro over `WithHandler` function in Hy**: user noticed my PR B test used `WithHandler` where `handle` would be more idiomatic. `handle` is now in the `--hy` auto-prelude (PR B step 2).

- **TDD order matters**: user memory `feedback_tdd_order.md` — "repro test (red) first, then fix (green), separate commits". Followed for PR D (test_env_var_ask red → handler implementation), PR E (test_unhandled_effect_chain red → pyvm.rs change), PR C (test_cli_hy_error_messages / deprecations / runner red → __main__.py rewrite), and PR A4 (test_withhandler_shim_deprecation red → program.py warnings).

- **Observability of long-running commands**: user memory `feedback_no_background_sleep.md` + CLAUDE.md instructions — never pipe through `tail`/`head` for filtering; use `tee` for logs. Not load-bearing this session (all commands quick), but kept in mind.

- **Never bypass pre-commit hooks with `--no-verify`**: prior session's violation is documented; this session avoided the pattern. All commits in this session went through pre-commit cleanly.
