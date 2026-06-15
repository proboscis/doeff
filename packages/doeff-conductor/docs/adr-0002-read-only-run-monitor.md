# ADR 0002 (doeff-conductor) — Run monitor: a node-lifecycle progress producer + read-only consumer

- **Status:** Proposed 2026-06-15 (iter 3 — adversarial review converged
  SOUND-with-nits after the "pure reader" premise was refuted in iter 1 and the
  K4 emission hazard fixed in iter 2; see Residual gaps).
- **Builds on:** ADR 0001 D6 (L2 session algebra; "success is decided solely by
  the result artifact, never by session heuristics"), D9 (overseer contract),
  D12 (K3 workspace/journal SSOT, law L-K3-3); constraint-graph C9 (agentd
  monitoring authority).
- **Related rule:** *Live orchestration state is produced by the conductor
  runtime (which owns dispatch) into a write-only observational journal;
  completion truth is the agent-journal `result_artifact`; agentd pane-derived
  liveness is never a completion/correctness source; the consumer never mutates
  run state.* (ADR 0001 D6 extended to a produced observability surface.)

## Context

The conductor CLI exposes one-shot monitoring (`ps`/`show`/`watch`/`wait`/
`gates`). Operators driving multi-stage runs want a continuously-redrawing
deck showing every running workflow, per-node progress, and parked gates.

Facts verified against the code (read-only Explore fan-out + adversarial review,
2026-06-15; line numbers approximate — anchor by symbol):

1. **Conductor exposes read APIs, but they do NOT carry live per-node
   progress.** `ConductorAPI.list_workflows` (`api.py`, ~`:349`) returns
   `WorkflowHandle`s (`types.py:320`). `overseer.progress_since` (`overseer.py:206`)
   and `list_open_gates` (`:215`) are read-only — BUT the `run-state.json`
   `events` they read are written **only** by `record_open_gates` /
   `record_gate_answer` / `validate` (`overseer.py:286,338,403`; `verbs.py:301`).
   **A real `run` emits no per-node lifecycle event**, and a gateless run never
   creates `run-state.json` at all (then `progress_since` raises
   `FileNotFoundError`, `overseer.py:196`). `ConductorAPI.__init__` does an
   idempotent `mkdir` of the state dir (`api.py:49`) — the only FS side effect of
   construction; it writes no run state.

2. **Completion truth is the agent-journal artifact, keyed by `node_identity`
   (not `node_id`).** `agent-journal.jsonl` `AgentJournalEntry` has
   `generation/entry_index/cache_key/resolved_identity_fingerprint/node_identity/
   result_artifact/terminal_kind` — **no `node_id`** (`journal.py:39`).
   `terminal_kind` ∈ {`succeeded`,`open-gate`,`gate-answer`,`workspace-created`}
   (`journal.py:33`); a completed agent node is `terminal_kind == "succeeded"`.
   `result_artifact` is presence-checked only (may be null) (`journal.py:97,607`).

3. **agentd's per-worker status is pane-derived and non-authoritative.**
   `capture_pane → is_waiting_for_input → detect_status`, `has_prompt ⇒ BLOCKED`
   (`packages/doeff-agents/.../handlers/production.py:645-660`, `.../monitor.py:177,207`).
   Completion is independent: `validate_result_payload` (`production.py:369`) and
   agentd-side result-file validation (`packages/doeff-agentd/src/main.rs:2551`).
   ADR 0001 D6 commits the split (adr-0001:276,674); semgrep
   `doeff-agents-no-terminal-text-success-status` enforces it (`.semgrep.yaml:104`).

4. **The worker tmux session name is identity-qualified, not bare `node_id`.**
   `session_id = deterministic_session_id(run_id, node_id=session_node_key, attempt)`
   where `session_node_key = node_id` or `f"{node_id}-{digest}"` with an 8-char
   resolved-identity fingerprint (`effects/agent.py:50-72`). So a node's session
   name is NOT prefix-derivable from `(run_id, node_id)` alone (digest +
   multi-attempt + stale sessions collide). It IS known at dispatch time.

5. **agentd (Rust) listens on an RPC socket but has no lifecycle-event ingest.**
   `packages/doeff-agentd` binds/serves a unix socket (`main.rs:752,845`); the
   dispatcher has no method to accept a pushed lifecycle event (`main.rs:857`).
   Worker→agentd liveness eventing is therefore a new Rust-daemon endpoint, not
   an additive change (see D5).

6. **No declared core for "worker-session status"; but agentd monitoring is
   C9 and session naming connects to K3.** Cores are K3/K4/K5
   (`docs/crystallization/constraint-graph-conductor.md` §5); C9 = agentd
   monitoring authority, and session identity ties to K3 (ibid:24,29). The
   "label-derived / artifact-store-of-record" relation is already committed law
   (D6). Reading a live pane (attach) is in C9 territory — not "core-free".

**The reframe:** existing surfaces are insufficient for a live per-node view
because conductor never persists node-lifecycle state. The correct fix (root
cause, not a pane-scraping patch) is to make the orchestrator — which alone
knows "I dispatched node X and have not received its terminal result" — *produce*
that state read-only. This is orchestration progress (conductor-side), distinct
from worker liveness (agentd-side, D5).

## Decision

### D1 — Observability = a runtime producer + a read-only consumer

The conductor runtime emits node-lifecycle progress events
(`dispatched` / `running` / `succeeded` / `failed` / `parked`) to a dedicated
**append-only, write-only `progress-journal.jsonl`** under the run state dir.
Each event records the full identity tuple needed for joins —
`{node_id, phase, attempt, session_node_key, session_id, node_identity,
status, terminal_kind?, at}` — so the consumer never reverse-engineers an
identity (`node_identity` is required: it is the join key to the agent-journal,
D2). This journal is **observational only**: resume/replay MUST NOT read it, and
deleting it changes no run outcome (keeps it out of the K3/K5 store-of-record).
Scope: the progress journal covers **agent-node** lifecycle only; parked/gate
state (quorum shortfall, loop exhaustion, merge conflict, phase checkpoint — the
several `_park` sites, `workflow_runtime.py:406,497,677,1126`) is read from
`list_open_gates` (the authoritative gate surface), **not** duplicated into the
progress journal, so `_park` sites need no instrumentation. Resume reads only named files (`workflow.hy`, optional
`run-state.json`, `agent-/gate-answer-/workspace-/effect-journal.jsonl`) — it
does not enumerate the run dir (`api.py:241` → `workflow_loader.py:225`,
`journal.py:124`) — so a new `progress-journal.jsonl` is provably never read by
the replay cursor.

**Emission obeys the K4 non-blocking discipline (L-K4-1).** Progress writes are
a best-effort, **fail-open, non-blocking** side-channel: they MUST NOT raise
into the run, block the dispatch/await critical path, or affect node
ordering/timing — a failed or slow write degrades the monitor's freshness,
never the run. The canonical emit site is the **offloaded agent handler**
(`handlers/agent_handler.py`, offloaded via `handlers/utils.py:161`), through
which BOTH dispatch paths funnel — `_execute_agent` (`workflow_runtime.py:563`)
AND the template-run path that emits `AgentEffect` directly (`api.py:145`);
emitting only in `_execute_agent` would silently drop template-run progress. The
handler has the task identity (`session_node_key`/`session_id`,
`effects/agent.py:50`) and `node_identity` (computed via `agent_replay_decision`,
`journal.py:557`); `phase` is NOT currently on the handler and must be threaded
through `AgentTask` / wrapper metadata. The write must **not** be a synchronous,
exception-propagating append before the effect is offloaded the way the existing
journals are (`journal.py:158`) — in K4 parallel (spawn/gather,
`workflow_runtime.py:321`) that would serialize sibling dispatch; emit
fire-and-forget (swallow+log on error).

The monitor (`conductor monitor`, working name; `rich.Live`)
consumes `progress-journal.jsonl` + `agent-journal.jsonl` (completion) +
`list_open_gates`, and **performs no run-state/journal/fingerprint mutation**
(its only FS effect is the idempotent state-dir `mkdir`).

### D2 — Status-source precedence law

A node's displayed status, highest authority first (lower tiers annotate, never
override):

1. **Completion = artifact.** `agent-journal.jsonl` entry (joined by
   `node_identity`, carried in the progress event) with
   `terminal_kind == "succeeded"` ⇒ **DONE (validated)**; a failure terminal
   kind / `runtime-error` progress event ⇒ **FAILED**; `open-gate` ⇒ **PARKED**.
2. **In-flight = progress journal.** Absent a terminal record, the node's latest
   `progress-journal.jsonl` event ⇒ dispatched / running / parked.
3. **Liveness = agentd pane status.** NEVER used for completion/correctness;
   rendered only as a dimmed, explicitly-labeled hint if shown — **omitted in v1**.

Correctness is judged against this committed identity: a node whose pane reads
"blocked" but whose journal entry is `succeeded` MUST render DONE.

### D3 — Attach uses the producer-recorded `session_id`, read-only

To inspect a worker live, the monitor uses the `session_id` **recorded by the
producer** (D1) — never a prefix guess — and attaches **read-only**
(`tmux attach -r` / read-only stream). Attaching reads an agentd-owned pane (C9),
so this is an explicit, read-only observation of an agentd surface, not a new
authority; nothing the monitor decides depends on pane contents.

### D4 — Gate visibility in v1 is read + command-surfacing; adjudication deferred

v1 lists open gates with options/outcomes/stakes and surfaces the exact
`conductor gate answer <run> <gate> <option>` command. Writing a gate answer
from the monitor is OUT of v1 (it mutates `gate-answer-journal.jsonl`, breaking
D1). Any future in-TUI adjudication MUST call the same `gate answer` write path
(single store of record, L-K5-2), never a parallel writer.

### D5 — Worker-liveness event-sourcing is out of scope and core-adjacent

Making agentd's per-worker liveness accurate in real time (a Claude Code hook
→ an agentd lifecycle-event ingest endpoint → `agent_sessions.status` derived
from events, pane capture demoted to fallback — the agent-deck pattern) requires
a **new RPC/hook endpoint on the Rust agentd daemon** (Context 5) and touches C9
(agentd monitoring authority). If pursued it ships as frontier-authored code +
human adjudication + a new law, as ONE set:

> **Status-source precedence (worker liveness):** result-contract (terminal
> artifact) > fresh lifecycle event > pane heuristic (fallback). A stale event
> never overrides a terminal status; freshness windows + an `acknowledged` flag
> tame the event/poll race (cf. agent-deck `sessionstatus.Derive`).

This is **distinct from D1** (which adds *orchestration* progress, owned by
conductor) and is **not a prerequisite** for the monitor.

## Implementation plan (stages; each lands with tests)

- **Stage 1 — node-lifecycle progress producer** (runtime instrumentation;
  core-adjacent → careful tier). Emit `dispatched/running/terminal` events with
  the full identity tuple to `progress-journal.jsonl` from the runtime's
  agent-node execution path. Done: a unit test runs a stub 2-node workflow and
  asserts the journal records the expected events + identities; a test asserts
  resume/replay does NOT read the progress journal and that its presence/absence
  changes no run outcome or fingerprint; a **fault-injection test** asserts an
  emission that raises or blocks changes no run outcome, ordering, or fingerprint
  (fail-open, L-K4-1).
- **Stage 2 — read-only monitor MVP** (test-verifiable; non-core). `rich.Live`
  `run→phase→node` tree from progress journal + `list_workflows`, status glyph
  by the D2 precedence, parked-gate panel from `list_open_gates`. Done: renders
  correct glyphs from fixtures **including the blocked-pane-but-`succeeded`
  case ⇒ DONE**; a "no write/mutation" test pins D1.
- **Stage 3 — read-only attach** (test-verifiable). Select node → use recorded
  `session_id` (D3) → `tmux attach -r`. Done: a test asserts the recorded
  `session_id` is used and `-r` is passed (mocked tmux).
- **Stage 4 — gate surfacing** (test-verifiable). Render gates + exact
  `gate answer` command; no write path invoked.
- **Stage 5 — (deferred, OUT of scope) worker-liveness eventing.** Per D5;
  frontier + human + law as one set; only if Stages 1–4 prove it needed.

Routing: Stage 1 is runtime instrumentation adjacent to K3/K5 → author carefully
with the resume-isolation test as the guard; Stages 2–4 are read-only/test-
verifiable → cheap-tier-eligible under overseer review; Stage 5 is C9-adjacent →
frontier + human + law, explicitly gated.

## Enforcement

- **Tests:** Stage-1 resume-isolation test (progress journal never read by
  resume); Stage-1 fail-open test (an emission failure never alters run
  outcome/ordering/fingerprint, L-K4-1); Stage-2 "blocked-but-succeeded ⇒ DONE"
  fixture (pins D2); "no write/mutation" test (pins D1).
- **Static:** semgrep `conductor-monitor-no-pane-status-as-completion` (sibling
  to `doeff-agents-no-terminal-text-success-status`) forbids deriving
  done/success from any agentd/pane status field in the monitor; a rule (or
  review gate) forbids the resume path importing the progress-journal reader.
- **Review:** any change making the monitor read agentd status for non-liveness
  purposes, or adding a second writer to the gate-answer journal, is rejected.

## Residual gaps (staged — not claimed done)

- **Scope fork — RESOLVED 2026-06-15 (human):** land the Stage-1 producer now
  (correct live per-node view via runtime instrumentation, no pane scraping, no
  agentd change). The thinner "existing-surfaces-only, no live indicator" v0 was
  rejected.
- Join key **confirmed** (iter 3): the producer obtains the same `node_identity`
  the agent-journal records by calling `agent_replay_decision` (`journal.py:557`);
  no equality guess needed.
- Remaining Stage-1 implementation tasks: (a) thread `phase` to the offloaded
  handler (`AgentTask`/wrapper metadata) so the canonical handler-side emit has
  it; (b) emit in the handler (not `_execute_agent`) so the template-run path
  (`api.py:145`) is covered.
- Line-number anchors are approximate; re-grep at implementation.

## Non-goals / cautions

- Not a process manager (surfaces `conductor stop`, invents no worker control).
- v1 reads no agentd RPC/sqlite except the read-only attach pane (C9, explicit).
- `CONDUCTOR_PROFILE_CONFIG` affects plan/run fingerprinting, not this read path;
  the monitor must not write.
- Do not let "the blocked label is wrong" drive a premature agentd change — D6
  already declares that label non-authoritative; the fix for live status is the
  D1 producer, not pane scraping.
