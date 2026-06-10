# C7 pilot: k2-k3 reference workflow on conductor

Date: 2026-06-10

## Workflow and target

The pilot workflow lives at
`packages/doeff-conductor/examples/k2_k3_pilot_workflow.py`.

It ports the reference shape into the C7 DSL surface:

- 5 parallel implementers
- compile/reconcile gate loop with a conditional fixer, bounded to 3 rounds
- 2 parallel test writers
- full build/test/lint gate loop with a conditional fixer, bounded to 3 rounds
- 4 adversarial reviewers returning the C5 verdict schema

The same file is now DSL-only: `conductor run` executes the expanded
`WorkflowSpec` directly through the production interpreter. The scratch target
is generated outside the doeff repository:

```bash
uv run python packages/doeff-conductor/examples/setup_k2_k3_scratch_repo.py \
  /tmp/doeff-conductor-k2-k3-scratch
```

The scratch gates are intentionally small but shaped like the reference:

- build: `PYTHONPATH=src python3 tools/build_check.py`
- test: `PYTHONPATH=src python3 -m unittest discover -s tests`
- lint: `PYTHONPATH=src python3 tools/lint_check.py`

The workflow uses per-task workspaces plus downstream `merge!`. That matches
the default D5 fork/reconcile model and is the only topology that generalizes
to the reference script's fifth task, which edited a different repository.
The scratch pilot uses one repo but keeps the same isolation contract.

## Verification

```bash
uv run conductor plan packages/doeff-conductor/examples/k2_k3_pilot_workflow.py --json \
  2>&1 | tee /tmp/c7-plan.log

uv run conductor validate packages/doeff-conductor/examples/k2_k3_pilot_workflow.py --json \
  2>&1 | tee /tmp/c7-validate.log
```

Observed:

- `plan`: 13 static worker rows, 9 `cheap-coder`, 4 `cheap-reviewer`.
- `validate`: all built-in scenarios closed: `all-pass`,
  `schema-invalid-then-pass`, `retry-exhaustion`, `quorum-shortfall`.

Production run used real Codex workers via explicit native structured output:

```bash
CONDUCTOR_STATE_DIR=/tmp/c7-conductor-state \
PYTHONUNBUFFERED=1 \
/path/to/.venv/bin/conductor --state-dir /tmp/c7-conductor-state run \
  packages/doeff-conductor/examples/k2_k3_pilot_workflow.py \
  --run-id c7-replay2 \
  --agent-mode codex-exec \
  --params '{"run_id":"c7-replay2","base_ref":"main","effort":"low"}' \
  --json 2>&1 | tee /tmp/c7-full-run.log
```

Observed final status: `done`.

## Measurements

Review routing:

- Tier-1 terminal fraction: 1 / 4 = 0.25.
- Tier-2 callbacks: 3 / 4. All were BLOCKER escalations from structured
  tier-1 verdicts.
- Calibration escapes: none observed. Calibration sampling was disabled for
  this pilot run, so no sampled PASS reached tier 2.

Gate behavior:

- Reconcile build gate passed in 1 round.
- Full gate passed in 2 rounds. Round 1 failed on the intentional lint
  sentinel; the gate fixer removed it; round 2 passed.

Wall clock:

| Phase | Seconds |
|---|---:|
| Implement, initial crashed run | ~271 |
| Implement, resumed replay | 0.74 |
| Reconcile | 0.29 |
| Tests | 147.00 |
| Gate | 29.13 |
| Review | 83.40 |
| Full resumed run wall clock | 260.59 |

Replay:

- The crash run stopped after Implement and left 5 journal entries.
- The resumed run reused those 5 cached agent artifacts; Implement in the
  resumed run took 0.74s.
- After completion, the journal held 12 agent entries: 5 implementers,
  2 test writers, 1 gate fixer, 4 reviewers. The compile fixer did not run
  because the build gate passed.

Cost notes:

- Static plan estimate: 13 cheap-model budget units.
- Actual agent invocations across crash plus resumed completion: 12 Codex
  workers.
- Rough all-frontier baseline: 36 units for the 12 workers that actually ran,
  or 39 units for the full static 13-worker shape at 3 units per frontier
  worker.
- Codex CLI token accounting was not exposed through conductor in this run, so
  token and dollar cost are recorded as budget-unit estimates rather than exact
  usage.

## Honest Deltas

- C8 removed the hand-written `main` Program. The example is a DSL-only
  `WorkflowSpec` and production execution uses the native interpreter.
- Conditional fixer execution is represented conservatively in `plan` as a
  static worker row even when the production gate passes and the fixer is not
  launched.
- Replay caches agent artifacts, not workspace mutations. The resumed pilot
  skipped the first 5 workers, but fresh workspaces were created on resume.
  This is not a sound durable workspace replay story yet; it was acceptable
  here because deterministic gates and later fixers revalidated the merged
  tree.
- C8 exposes the workflow return payload through `conductor run --json`, so the
  old `report_path` workaround is gone.
- The local environment did not have a reachable `doeff-agentd`; the pilot used
  explicit `--agent-mode codex-exec`, which still runs real Codex workers
  through the production conductor handler but bypasses agentd session
  supervision.
