# Todos

## Completed (this session)

- [x] **PR B** — `--hy` CLI flag with do!/<- auto-prelude + handle/defhandler macros pre-required (doeff `c379c0a4`, `167758f0`, `a8a99b64`).
- [x] **PR D** — `env_var_ask` handler + `lazy_ask(strict=False)` Pass-on-miss (doeff `57498be1`).
- [x] **PR E** — UnhandledEffect message includes `handlers in scope (innermost→outermost)` chain (doeff `88cb24e4`).
- [x] **PR C** — `--runner PATH` flag, `RunnerContext` dataclass, built-in `doeff.runners.local.run_local`, rich error messages for `--hy` + legacy flag combos, deprecation warnings for legacy flags (doeff `6912d589`).
- [x] **PR A4** — `WithHandler` shim emits `DeprecationWarning` on both paths; `WithHandlerType` alias stays clean (doeff `d907d6a3`).
- [x] **PR F** — `nakagawa.runners.k3s.k3s_sim` new runner + unit tests + `Makefile` switched to `--runner` (proboscis-ema `696d51d7`).
- [x] Update memory (`design_doeff_run_redesign.md`, `MEMORY.md` index).
- [x] Push all PRs to remote.

## Prioritized Backlog

Priorities come from the user's explicit statements during the session.
The only high-priority item not landed is PR G; everything else is
follow-up cleanup.

| # | Priority | Task | Status |
|---|----------|------|--------|
| 1 | high (user: "this is very controversion. lets ... discuss in next session") | Write `specs/vm/SPEC-VM-022-pass-with-reason.md` covering (a) `Pass` DoCtrl/DoExpr adds `reason: Option<String>` vs alternative effect-side `pass_trail` mutable field; (b) defhandler macro auto-reason injection ("handler does not handle Ask" vs typed "telemetry handles only Slog, got Ask"); (c) OCaml 5 alignment with SPEC-VM-020/021 (is a reason-carrying reperform a doeff dialect?). | next |
| 2 | high (depends on #1) | Implement SPEC-VM-022: extend `Pass` in `doeff-vm-core/src/do_ctrl.rs` + `do_expr.rs`; thread trail through `dispatch.rs`; extend `extract_handler_chain` in `doeff-vm/src/pyvm.rs` to emit "pass trail" lines (innermost→outermost with reasons). | next |
| 3 | high (depends on #2) | Update `defhandler` macro in `packages/doeff-hy/src/doeff_hy/handle.hy` so auto-generated clause-mismatch `Pass` carries a generic reason; document the `(reperform effect :reason "...")` surface. | next |
| 4 | medium | After SPEC-VM-022 lands, decide whether to remove the `WithHandler` shim (scope change from "permanent" to "deprecated-and-eventually-removed") — currently scope A ("shim stays forever") is in force. User to reopen if direction changes. | later |
| 5 | medium | proboscis-ema: migrate remaining `WithHandler(h, body)` call sites that still use the shim to the new-style `h(body)` idiom. Current DeprecationWarning is silent by default, but `-W error::DeprecationWarning` surfaces ~30 sites. No call-site list has been compiled yet. | later |
| 6 | low | Retire `packages/nakagawa/src/nakagawa/phase2/interpreters/k3s_interpreter.hy` once every `run-k3s`-shaped invocation has migrated to `--runner nakagawa.runners.k3s.k3s_sim`. Check users of `k3s_sim_interpreter` in VAULT and experiments first. | later |
| 7 | low | Pre-existing failures in `tests/cli/test_cli_run.py` / `test_doeff_run_context.py` (45 fails) — unrelated to PR C/D/E but may confuse CI. Root cause: `tests.cli_assets` dotted-path import doesn't resolve via `import_symbol`'s progressive-path lookup. Triage separately; not a blocker. | later |
| 8 | low | Consider adding a `standard-env` convenience helper in `doeff-core-effects` — `(standard-env program)` = `(env-var-ask (scheduled program))` — so users don't have to wrap twice in their `defp`. Deferred because the "no auto-wrap" contract (option A) was the chosen default. | later |

## Blocked

- [ ] PR G implementation (#2) — blocked by: spec writing (#1). User explicitly asked to defer the design discussion to the next session.
