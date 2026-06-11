# Conductor core-feature validation matrix (campaign of 2026-06-11)

Goal: make conductor usable from agents via CLI + Hy DSL, then validate
every core feature in spec/ADR against live behavior. Evidence = run ids,
state dirs, pane captures, or plan/validate outputs. ✅ = validated live,
🧪 = unit/stub-tested only, ⏳ = in flight, ❌ = defect found (fix linked),
⬜ = not yet exercised.

## §1-§3 Surfaces, layers, profiles

| Feature | Status | Evidence |
|---|---|---|
| `conductor env describe` vocabulary | ✅ | profiles/router table (this session) |
| `plan` binding table + budget | ✅ | effort-axis, ws-resume plans |
| `validate` stub closure (4 scenarios) | ✅ | effort-axis, review wfs |
| `run` end-to-end real workers | ✅ | sample-hardening (run 6), effort-axis-review3 |
| Profile semantic names → adapter binding | ✅ | frontier→claude, cheap→codex observed |
| model=None → CLI default | ✅ | Fable 5 via baked agent-home settings |
| effort = profile axis → fingerprint | ✅ | effort-axis merge c37f5534; live argv pending daemon rebuild |
| Routing discipline (frontier for invariants) | ✅ | A/B evidence + tonight's runs |

## §4 DSL + loader

| Feature | Status | Evidence |
|---|---|---|
| defworkflow/defphase/agent!/<-/params/roles | ✅ | all runs |
| Hy-only surface (.py rejected) | ✅ | C10 acceptance |
| Nondeterminism rejection w/ named replacement | ✅ | probe: datetime.now → "use time!" w/ file:line |
| `parallel` (all-must-succeed) | ✅ | sample-hardening 2-way parallel |
| `parallel-for` | ⬜ | needs probe run |
| `parallel :quorum k` → Try-typed bindings | ⬜ | needs probe run |
| `loop :max :until` — exits on gate pass w/o fixer | ✅ | effort-axis Gate phase (no fixer session spawned) |
| `loop` fixer path (gate fails → fixer runs) | ⬜ | needs forced-failure probe |
| `gate!` deterministic subprocess | ✅ | sample + effort-axis (pytest+cargo) |
| `time!` / `random!` effects live | ⬜ | needs probe run |
| `:files` overlap rejection (shared ws) | ✅ | probe: overlap error names ws + files |
| `:class` always explicit | ✅ | loader enforces; all runs |

## §5 L2 session algebra (via agentd)

| Feature | Status | Evidence |
|---|---|---|
| Launch (idempotent re-adopt) | ✅ | effort-axis resume re-adopted session |
| AwaitResult (artifact-first) | ✅ | done+payload within seconds of valid file (06bcf25e) |
| FollowUp retry feedback | ✅ | run-1 attempts; retry path in review3 |
| Result channel = in-workdir file, git-excluded | ✅ | .agentd-result.json + info/exclude |
| Claude worker visibility (turn-end/latch/watchdog) | ❌→✅ | 4 defects fixed: f5a7517a, a11abb62, d47cf6c8, 06bcf25e |
| Await budget default | ❌→✅ | 600s→3600s; owning axis still open (§11-7) |
| L2 single retry authority (no follow-up to terminal sessions) | ❌→✅ | killed 4 runs at completion boundaries; fixed 546d193a (AwaitOutcome.continuable) |
| effort → worker argv delivery | ✅ | live: `codex --yolo -c 'model_reasoning_effort="xhigh"'` from profile binding |

## §6 Workspace / medium

| Feature | Status | Evidence |
|---|---|---|
| workspace! from ref → worktree+branch | ✅ | all runs |
| merge! real merge commits | ✅ | sample-hardening run 6 |
| Runtime auto-commit after agent nodes | ✅ | PR #440 + tonight (caveat §11-6 agent-home capture) |
| **Resume-stable workspace identity** | ✅ | landed b5854d60 (worker impl + overseer occurrence fix + reviewer F1/F2); E2E: kill→resume bound the SAME worktree (1 dir, marker present) |
| Shared setv workspace = ONE workspace | ✅ | regression caught pre-merge by probe; occurrence identity; test pinned |
| Merge re-applies every source on resume | ✅ | reviewer F2 (live git probe) → fixed + test |
| Merge conflict bounce-back | ⬜ | needs probe |

## §7-§9 Verbs, overseer, replay

| Feature | Status | Evidence |
|---|---|---|
| Supervision: autonomous | ✅ | all runs |
| Supervision: phase-checkpoints + gate adjudication | ⬜ | never exercised live |
| Parked gate closure options (proceed/redirect/abort) | 🧪 | validate scenarios only |
| Resume: journal replay of completed agents | ⬜ | blocked on ws-resume fix; then kill-resume E2E |
| Fingerprint-gated cache invalidation (profile edit) | 🧪 | unit tests only |
| State dir as audit record | ✅ | journals inspected during incidents |

## §10 Enforcement

| Feature | Status | Evidence |
|---|---|---|
| Tier-0 guards (semgrep boundary rules per ADR 0005) | ⬜ | existence check needed |
| Condemned-code deletions (no ratchets) | ✅ | C1/C9 audits |

## CLI monitoring inventory (2026-06-11)

| Layer | Tool | Status |
|---|---|---|
| L3 workflows | `conductor ps / show [--since] / watch / stop / resume` | ✅ works (live-tested ps, show, show --json) |
| L2 sessions | `doeff-agents ps / watch / output / send / attach` | ❌ reads local-handler store; BLIND to agentd sessions (conductor's only route) |
| L1 daemon | `doeff-agentd` | serve only — no client subcommands; session table reachable only via sqlite |

Gap: the L2 CLI predates the agentd-only ruling; it should query the
daemon (single authority). Candidate follow-up issue.

## Open defects (spec §11)

5. Workspace resume divergence — ✅ fixed b5854d60 (+ occurrence identity, F1/F2 review fixes)
6. Auto-commit captures .agent-home session state — ✅ fixed 913149d1
7. Await budget owning axis — open
8. blocked-status cosmetic misread — open
9. (candidate) doeff-agents CLI blind to agentd sessions — monitoring gap


## Late-session findings (after the first matrix cut)

| Item | Status | Evidence |
|---|---|---|
| §9 replay: re-run done run-id = journal hit | ✅ | 0.5s, zero agentd events, identical payload |
| Gates (deterministic) re-execute on replay | ✅ | new exec log per replay — level-triggered |
| Fingerprint invalidation END-TO-END | ❌→✅ | two defects: session name lacked identity digest, then digest not threaded to L2 (journal poisoning); fixed 3d765cac + 7th commit; live: same run-id re-launched `…-84716d3a-0` with effort="medium" argv |
| Result channel per-session | ❌→✅ | shared-workspace collision (stale acceptance); fixed 0ad2c7b4 |
| Loop iteration in node identity | ❌→✅ | round 2 re-adopted round 1's session; fixed 0ad2c7b4 |
| §8 attention tiers LIVE | ❌ | stub-only: live exhaustion = terminal error, no parked gate, no closure verb, phase-checkpoints inert — NEXT MAJOR STAGE |
| `random!` true randomness | ❌ | Random(0) constant-seed placeholder (§11-10) |
| quorum/oks Try-bindings | ⬜ | not yet probed live |
| merge-conflict structured bounce | ⬜ | not yet probed live |
