# SBI k3s readiness validation plan

Status: In progress
Date: 2026-06-19
Owner: Codex

## Goal

Run the SBI company readiness checks from both local and k3s runtimes, validate
the results, and fix any failures with TDD plus semgrep where an invariant is
being changed.

Completion requires:

1. Local SBI safe readiness passes before k3s readiness is trusted.
2. k3s SBI safe readiness passes from the k3s runtime, not from Plutus source or
   a Plutus runner.
3. `TradeEvent` append errors do not reappear in local or k3s logs.
4. Any code fix is covered by focused `deftest` tests.
5. Any newly discovered forbidden pattern is guarded by `.semgrep.yaml`.
6. The final report names the exact commands, logs, pass/skip counts, and any
   skipped checks.

## Safety Boundary

- Runtime under validation: local process and k3s pod runtime.
- Broker host: Plutus is only SSH + Chrome/CDP for SBI.
- Safe target: `test-k3s-sbi-safe-readiness`, which selects `not real_money` and
  `not market_open_order`.
- Do not run L3 board-open real-fill readiness without explicit
  `MARKET_OPEN_ORDER=1` and an explicit user request.
- Do not run proboscis-ema source, doeff-agent, tmux agents, or readiness
  runners on Plutus.

## Current Evidence

### Local SBI Safe Readiness

Command:

```bash
gtimeout 3600 make -C packages/nakagawa test-trading-readiness-sbi-local-safe \
  > /tmp/proboscis-ema-sbi-local-safe-20260619-215930.log 2>&1
```

Result:

- Exit code: `0`
- Summary: `3 passed, 1 skipped, 16 deselected in 95.97s`
- Log: `/tmp/proboscis-ema-sbi-local-safe-20260619-215930.log`
- Negative log scan:
  - no `TradingEvent event-log append failed`
  - no `GetTimeEffect`
  - no `NameError`
  - no `Invalid isoformat`

### k3s SBI Safe Readiness

Command started after local pass:

```bash
gtimeout 5400 make -C packages/nakagawa test-k3s-sbi-safe-readiness \
  > /tmp/proboscis-ema-sbi-k3s-safe-20260619-220130.log 2>&1
```

State observed at 2026-06-19 22:02 JST:

- The user interrupted the Codex turn while this command was running.
- The process was still alive afterward.
- Observed subprocesses:
  - `gtimeout 5400 make -C packages/nakagawa test-k3s-sbi-safe-readiness`
  - `make -C packages/nakagawa test-k3s-sbi-safe-readiness`
  - `rsync ... /Users/s22625/repos/proboscis-ema/ zeus:/tmp/nakagawa-build-ctx/`
- Log: `/tmp/proboscis-ema-sbi-k3s-safe-20260619-220130.log`
- Stage: syncing/building the `zeus:5000/nakagawa:test` image for the
  k3s test job.

State observed at 2026-06-19 22:09 JST:

- The command had not reached Docker build or k3s Job creation.
- Local and remote rsync processes were alive but idle for more than 7 minutes.
- Remote build context stayed around `311M` / `3875` files.
- The sync/build process was stopped before any k3s readiness Job was created.
- No broker-side readiness test had started from this k3s attempt.

Next k3s attempt:

- Start a fresh `make -C packages/nakagawa test-k3s-sbi-safe-readiness` run.
- Keep the same log discipline and update this file after the run reaches build,
  job creation, pass, or failure.

State observed at 2026-06-19 22:13 JST:

- Fresh k3s safe readiness run reached Docker build and push successfully.
- Image pushed: `zeus:5000/nakagawa:test`
- Digest: `sha256:51e544a440b0b9e5e4b6784acf2b0952e8342e471e82ccefe43d9f0ae9e329d4`
- k3s runtime pytest selection started:
  - `4 selected`
  - selector: `(sbi_order_confirmation_static_single_day or sbi_order_confirmation_device_auth_fallback_single_day or sbi_company_full_plan_order_confirmation_deployed_shape or sbi_doeff_agent_mcp_order_confirmation_single_day) and not local`
  - markers: `not real_money and not market_open_order`
- Current log: `/tmp/proboscis-ema-sbi-k3s-safe-20260619-221000.log`

## TDD + Semgrep Requirements For Any New Fix

If k3s readiness fails:

1. Reproduce or isolate the failure with the narrowest local `deftest` first.
2. Run the new test and capture the expected failure.
3. If the failure reveals a forbidden old pattern or architectural invariant,
   add a semgrep rule before the implementation fix.
4. Implement the fix.
5. Re-run the focused `deftest`.
6. Re-run the relevant semgrep command.
7. Re-run local SBI safe readiness if the changed behavior affects local
   execution.
8. Re-run k3s SBI safe readiness.

## Next Steps

1. Keep tracking the existing k3s command instead of starting a duplicate.
2. Poll `/tmp/proboscis-ema-sbi-k3s-safe-20260619-220130.log` and the k3s job
   state until it exits.
3. If it passes, scan logs for the `TradeEvent` append error family.
4. If it fails, extract the failing test, traceback, k3s pod/job state, and
   broker/helper command that failed.
5. Apply the TDD + semgrep flow above for any code change.
6. Update this plan file after each material state change.

## Open Risks

- The worktree is dirty and the Docker build context includes the current local
  state, not a clean commit. Report the exact branch and note this in the final
  validation result.
- `.semgrep.yaml` currently contains broader unrelated rules beyond the
  `TradeEvent` fix. Do not commit the whole file blindly as part of the
  readiness bugfix.
