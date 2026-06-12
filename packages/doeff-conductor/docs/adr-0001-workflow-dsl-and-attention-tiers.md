# ADR 0001 (doeff-conductor) — Hy workflow DSL, schema-enforced agent boundary, and attention-tier review routing

- **Status:** Proposed 2026-06-10; amended same day after a design session
  with the maintainer (layer boundaries, profiles, DSL closure, L2 algebra,
  workspace/medium model, overseer contract, no-backcompat policy). This
  file is the implementation brief for agents — it is self-contained, you
  do NOT need the originating conversation.
- **Companion spec:** [`spec-workflow-orchestration.md`](spec-workflow-orchestration.md)
  holds the detailed contracts (L2 signatures, DSL grammar, binding
  resolution, validation rules). This ADR records decisions and rationale;
  the spec records contracts. The ADR wins on conflict.
- **Goal:** replace Claude Code's built-in `Workflow` tool (a sandboxed JS DSL
  that can only spawn *Claude* subagents — the most expensive tokens) with a
  doeff-conductor-native orchestration layer whose **workers are interchangeable
  (Codex first), whose control flow is compiled and validated before any token
  is spent, and whose review/verification load is routed through budgeted
  attention tiers** instead of "the expensive model reads everything".

## Context

### What is being replaced

Claude Code's `Workflow` tool executes a plain-JS script in a sandbox with
injected primitives. Its contract (which WORKS and must be preserved, not
discarded):

- `agent(prompt, {schema, label, phase})` — spawn one worker; when `schema`
  (JSON Schema) is given, the worker's final output is **validated structured
  data, enforced at the call boundary** (the worker retries on mismatch);
  returns the parsed object.
- `parallel([thunks])` — barrier; `pipeline(items, ...stages)` — per-item
  flow without barriers; `phase(title)` — progress grouping; `budget` —
  token budget tracking.
- **Replay resume**: re-running a script returns cached results for the
  longest unchanged prefix of `agent()` calls, keyed by `(prompt, opts)`.
  To make this sound, the sandbox *bans* nondeterminism syntactically:
  no `Date.now()`, no `Math.random()`, no filesystem, `meta` must be a pure
  literal.
- Failure model: a failed/skipped agent resolves to `null` (callers
  `.filter(Boolean)`) — i.e. silent-ish drops.

Reference implementation to port (a real production run, 5 parallel
implementers → compile-reconcile → 2 test writers → gate loop → 4 adversarial
reviewers):
`/Users/s22625/.claude/projects/-Users-s22625-repos-agent-control-plane/3911089d-cc6c-484c-ab7c-4f9a71de723d/workflows/scripts/k2-k3-merge-failure-routing-wf_158874ac-ae0.js`
Use it as the pilot port and the acceptance benchmark for the DSL's
expressiveness.

### Why doeff is the right substrate

The JS sandbox **fakes by prohibition** what an effect system provides **by
construction**:

- Nondeterminism (agent calls, clock, fs, subprocess) becomes *declared
  effects*; the program between effects is pure, so replay/durable resume
  falls out of the effect log instead of syntax bans.
- Handlers make workers interchangeable: Codex / Claude / Gemini /
  **stub** — so orchestration logic gets *unit tests that burn zero tokens*
  (impossible with the JS tool).
- `Try`-style typed failure replaces silent `null` drops: every branch's
  failure path must be handled explicitly (fail-fast philosophy).
- Hy macros give **compile-time validation of the workflow graph** — an
  LLM-written workflow fails at expansion time (free) instead of mid-run
  after burning agents.

### Why "router by verification cost", not "review everything"

The maintainer's concern: if the expensive model (Claude) conducts cheap
workers (Codex), it ends up reviewing all their work — the cost moves, it
doesn't shrink. The resolution adopted here:

1. Delegation pays only where **verification ≪ generation**. Tasks are
   therefore classified by *verification class*, a required field:
   - `mechanical` — verified by deterministic gates (build/test/lint/golden).
     Delegate fully; nobody reads the diff.
   - `test-verifiable` — acceptance tests are part of the task spec; passing
     them IS the review.
   - `semantic` — verification requires re-deriving the work (subtle
     invariants). **Reviewing properly costs as much as writing — route to
     the expensive tier to WRITE, or pin with property tests first.**
2. Review itself is a tiered, budgeted resource ("attention tiers"):

   | Tier | Reviewer | Cost | Budgeting |
   |---|---|---|---|
   | 0 | deterministic gates (build, tests, linters, schema checks) | ~0 | always-on, exhaustive |
   | 1 | cheap-model reviewers (Codex) emitting **structured verdicts** | low | bounded attempts per item |
   | 2 | expensive model (Claude) adjudicating *verdicts*, not transcripts | high | explicit budget; sees only BLOCKERs, tier-1 disagreements, and calibration samples |
   | 3 | human | scarcest | gate queue with stakes/priority metadata |

   Rules (the "closure law", borrowed from agent-control-plane ADR 0008 —
   see `~/repos/agent-control-plane/docs/adr/0008-*.md` for the prior art):
   - Every tier-N review terminates in exactly one of: a verdict, an
     escalation to tier N+1, or budget-exhausted → an open gate at tier N+1.
     Never a silent drop.
   - **Disagreement routes up** (never majority-voted away when stakes are
     high). Cross-provider review is preferred (error modes decorrelate).
   - **Calibration sampling**: tier N+1 audits a random % of tier-N PASSes;
     the rate per lane is adjusted by observed escape rate (level-triggered).
     Without this, cheap tiers drift into rubber-stamping.
   - **Compile-down ratchet**: every escape that reaches tier N+1 must
     produce a new tier-0 check (lint rule / property test) or a tier-1
     prompt improvement. Expensive attention is an *investment that compiles
     downward*, so total review demand decreases monotonically.

## Decision

### D1 — `agent!` effect with schema-enforced results

A conductor workflow invokes workers only through an `agent!` effect:
prompt, result JSON-schema (or pydantic model), a **semantic profile name**
(a capability tier such as `:cheap-coder` / `:frontier-reviewer` — never a
concrete agent kind or an account; the project env binds the name to
adapter+model+effort and the user env completes it with auth. `stub` is NOT a
profile but an interpreter choice reachable only via the `validate` verb
and tests), workspace, label/phase, and the
**verification class** (required, always explicit, never inherited or
defaulted). The handler launches the worker **only as a
doeff-agentd-supervised session** (tmux: observable via attach/capture,
steerable, durable across conductor restarts — D6). **doeff-agentd is the
only route for the handler.** Direct subprocess execution of a worker
(e.g. invoking `codex exec` from the handler) is FORBIDDEN: an
unsupervised worker is invisible (no attach/capture/steer) and dies with
its parent — violating the maintainer's observability rule and the
durable-sessions contract. If agentd is unreachable, the handler fails
loudly with start instructions; it never falls back to a subprocess. The
result is obtained through a **handler-private result channel operating
inside the supervised session** — e.g. the in-workdir result file agentd's
`await_result` validates (`.agentd-result.json`) or an MCP result tool.
The mechanism is NEVER part of the contract and MUST work inside the
worker's sandbox boundary (no out-of-workspace writes). The handler validates the result against the
schema and **retries with the validation error appended** (bounded, e.g. 3)
before failing typed. The validated object is the effect's return value:
`agent!` awaits the worker and returns the artifact — there is no separate
fetch effect at the workflow boundary, and concurrency is structural
(parallel/pipeline forms compile to `Gather`), so workflow code never
holds session handles or futures. This reproduces the JS
`agent(prompt,{schema})` contract and agentd's `await_result` pattern.

### D2 — Hy macro DSL: a closed vocabulary with expansion-time validation

The DSL surface is **closed and complete**: `defworkflow (:params :roles)`,
`defphase`, `agent!`, `gate!`, `parallel (:quorum)`, `parallel-for`,
`pipeline` (v1-open, see spec §11), `loop (:max :until)`, result bindings
(`<-`), explicit `time!`/`random!` effects, and pure glue functions.
Nothing else. In particular, **none of doeff's binding machinery surfaces
in the DSL** — no `ask`, no `Local`, no handler/interpreter access, no
session handles or futures. Each carrier is replaced by a DSL-native
concept: kleisli args → `:params`; effect payloads → form fields;
`Local` → the flat `:roles` table + call-site overrides; interpreter env →
system-side verbs; `ask` → name references resolved by the environment.

**The authoring surface is exclusively the Hy macro DSL.** Workflows are
authored as `.hy` files using the `defworkflow`/`agent!`/... macros. The
Python spec IR those macros expand into is an internal compilation target
— it is NEVER written by an author (human or agent), and the loader does
not accept user-supplied `.py` workflow files. (Amends the C8 stage text,
which wired and documented `.py` as the runnable surface — that was an
implementation expedient that leaked into the contract; the snapshot,
examples, and loader move to `.hy`.) The loader's nondeterminism check is
unchanged in substance: Hy compiles to Python AST, so the same AST walk
runs on the compiled module.

**Binding locality (no dynamic scoping):** every `agent!`'s binding is
derivable from its call site plus the flat top-of-file `:roles` table.
This is what makes the DSL writable by LLM authors and totally checkable.

**Glue purity by construction:** glue runs as plain (non-kleisli)
functions, so it structurally cannot launch agents or touch the
environment. The residual hole — raw Python nondeterminism that is not an
effect — is closed by the **workflow loader itself**: conductor AST-walks
every workflow module at load time, banning `datetime.now`/`random`/
`open`/network/subprocess/non-allowlisted imports, each diagnostic naming
the replacement (`time!`, `random!`, `gate!`, a `:params` entry). Because
the check lives in the loader, `plan`/`validate`/`run` all inherit it and
no module marker is needed (the loader knows what it loads). A
separately-run linter is NOT an acceptable enforcement point: workflows
are ephemeral request artifacts (D10) that no CI will ever see — a gate
that depends on a tool the author might run is a prompt promise, not an
invariant. (This amends the original C2v placement in doeff-linter; the
rule logic and fixtures move into conductor and the linter rule is
deleted — no parallel implementations.)

Macros compile to a static DAG of doeff Programs. Expansion-time checks
(all free, before any token is spent):

- phase declarations vs uses match; no orphan nodes; joins well-formed;
- every `agent!` carries a schema and an **explicit** verification class;
- role references resolve against the `:roles` table;
- **file-disjointness** between parallel same-workspace editors (declared
  `:files` sets must not intersect) — or each writer takes its own
  workspace + a downstream `merge!` node (D5);
- every parallel branch's failure path is handled (`Try`), and every node
  terminates in a gate, an artifact, or an escalation — the closure law as
  a compile check;
- **quorum typing**: `(parallel :quorum k)` with k < branch count makes the
  bindings `Try`-typed; direct dereference is an expansion error, and
  tolerated losses are journaled and surfaced, never silent;
- dataflow well-formedness (references defined; unconsumed results
  flagged); budget annotations parse and sum;
- the loader's nondeterminism check passes on the workflow module
  (enforced at load time, so `plan`/`validate`/`run` cannot skip it).

Keep the JS tool's load-bearing constraints: static graph skeleton, pure
metadata, effect-boundary caching for replay resume — with the cache key
defined by the **result-distribution criterion**: prompt, schema, and the
resolved identity fingerprint (adapter kind, model, effort) enter the key;
substrate never does (see D7).

### D3 — Deterministic gates are plain steps, not agents

build/test/lint/schema-parity gates run as subprocess effects (no LLM).
Gate output is structured (pass/fail + log path). Logs are `tee`'d to
files, never piped through `head`/`tail` (SIGPIPE truncation corrupts
builds — maintainer house rule).

### D4 — Review routing and the conductor protocol

- Tier-1 reviewers are ordinary `agent!` calls returning a structured
  verdict schema: `{verdict: PASS|CHANGES_REQUESTED, findings: [{title,
  severity: BLOCKER|MAJOR|MINOR, detail, file}]}`.
- Tier routing is implemented by the **router**, a pure injected function
  `route(verification-class, stakes, remaining-budget) → profile-name`
  supplying the default profile for any `agent!` without an explicit
  `:profile` override; the conductor passes the remaining budget in, the
  router holds no state.
- The conductor (tier 2 — the **overseer**, typically a Claude session
  driving this library; see D9) receives only: BLOCKER findings, tier-1
  disagreements, and the calibration sample. It never reads raw transcripts
  in the normal path.
- Tier-3 (human) gates carry stakes metadata (verification class, blast
  radius, reversibility, novelty) so the human queue can be prioritized.
- Budget pressure degrades explicitly (lower sampling rate, defer
  low-stakes items, batch) — never silently skips a review.

### D5 — Workspace model, join strategy, and medium families

Three nouns, strictly separated: a **site** is the execution host
(1 per run in v1, interpreter-bound, system-only); a **workspace** is the
logical unit of mutable state — for the git family, `(repo, ref)` — and is
a first-class DSL dataflow value (`workspace!` creates, `agent!`
consumes/returns, `merge!` reconciles, `gate!` runs on one); a **worktree**
is the site-local materialization of a workspace and is L1-private —
paths never surface. The workspace's portable identity is the *ref*, which
is why multi-site execution later is a pure handler extension (clone +
push/fetch as transport) with zero DSL change. Multi-repo workflows are a
v1 requirement (the k2-k3 reference's TASK D edits a second repository).

Default isolation is **workspace-per-task → branch → deterministic
`merge!` node → tier-0 gate on the merged tree**; merge conflicts bounce
back as fix tasks. Shared-workspace parallel editing is allowed only with
the compile-checked disjoint `:files` declaration (D2). These are the two
instances of the general rule the DSL core owns: concurrent mutators of
shared state declare either **fork/reconcile** or a **statically
partitioned write set**.

**The DSL core is medium-agnostic.** git+worktree+merge is the *first
medium family* implementing the fork/reconcile contract; core vocabulary
and checks must not depend on a medium family's nouns. v1 implements the
git family only — a scope decision, not a structural constraint. New media
(document vaults, asset directories) arrive as new closed vocabulary
families added by system designers, never by authors. Stage C4 uses public
workspace vocabulary; site-local materialization nouns remain handler-private.

Workspace identity is **resume-stable by construction**: every
workspace-producing node (explicit `workspace!`, the implicit per-`agent!`
workspace, `merge!`) derives its workspace id — hence its branch and
worktree — deterministically from `(run-id, workspace-node identity)`, and
materialization is idempotent ensure-style (re-adopt when present,
re-materialize from the branch when the worktree is missing, create from
the base ref exactly once per identity lifetime). For explicit
`workspace!`, the node identity is the EXPRESSION's source-order
occurrence, never the evaluation site: one `workspace!` value shared by
several nodes is one workspace. This mirrors D6's deterministic session
names and keeps workspace effects out of the replay journal.

### D6 — Layer boundaries and the L2 session algebra

```
L3  intent vocabulary    agent! / gate! / workspace! / merge!
L2  session algebra      Launch / AwaitResult / FollowUp / Stop / Release
L1  substrate handlers   tmux | opencode | zellij | ssh | stub
L0  identity environment profile registry, auth homes, model bindings
```

**Noun-ownership rule:** a layer's vocabulary never mentions a lower
layer's nouns. Session handles are opaque (`session_id` only —
`SessionHandle.pane_id` is removed; substrate nouns live in L1-private
state). The L2 core is **total** (every substrate implements it):
`Launch` takes a deterministic session name derived from
`(run-id, node-id, attempt)` and is **idempotent** (already-exists →
re-adopt; agentd supervises tmux sessions across conductor restarts);
`AwaitResult` blocks and returns
`{status: exited|awaiting-input|timed-out, exit-code?, result|absent}` in
one journal entry (agentd's `session.await_result` generalized — no
separate fetch effect, no race window); `FollowUp` is the total
continuation primitive (in-session for interactive substrates,
relaunch-with-context for batch ones). Status vocabulary is exactly
`starting|running|awaiting-input|exited` — **success is decided solely by
the result artifact**, never by session heuristics. Human steering
(`AttachTTY`/`CaptureTranscript`/`Steer`) is capability-gated, ops-only,
and not emittable from the DSL. Capability names (`interactive`,
`durable-sessions`) are declared by substrates and checked at plan time.
Full signatures and handler obligations: spec §5.

### D7 — Binding model: one resolver, logged fingerprints, plan-time resolution

Five configuration axes — intent / agent kind / identity / substrate /
persona. "Profile" names the (agent kind + identity) bundle; substrate is
runtime-scoped (one site per run, interpreter-bound); persona is
intent-side. Per `agent!`, **exactly one resolver** evaluates the fixed
cascade `explicit field > :roles entry > router default > interpreter
env` — no ask-with-default fallbacks anywhere. The **resolved fingerprint**
(adapter kind, model, effort — the result-distribution-affecting subset) is
journaled; cache hits require fingerprint match, so editing a profile's
definition between resume invalidates correctly while substrate swaps do
not. Reasoning **effort** is an axis of the profile binding (L0 identity
environment), never a workflow/run parameter: it changes the result
distribution, so it participates in the identity fingerprint exactly like
adapter kind and model. Before any worker starts, the conductor produces the **binding plan**
(all static nodes resolved: profiles exist, capabilities satisfied,
budgets sum) for overseer approval — resolve early, execute late.
Workspaces need no journal entries for resume: their identity is derived
from `(run-id, workspace-node identity)`, not recorded — the same
identity-by-construction discipline as D6's session names (see D5).

### D8 — Verbs, not interpreters

Interpretation selection is system-internal: author- and overseer-facing
interfaces are verb-shaped and never accept an interpreter, handler stack,
or backend name. Named interpreter constants back the verbs:
`conductor plan` = record/estimate interpretation (emits the binding
plan); `conductor validate` = **stub scenario simulation** (zero tokens;
deterministic failure scripts drive every branch, making the closure-law
test a CI-runnable model check, and the kill-and-resume test runs under
the same interpretation); `conductor run` = production. The stub is a
branch-exploring simulator, not a happy-path mock.

### D9 — Overseer contract

Every workflow run has an **overseer** (an agent or a human) who must be
able to track progress and resume on failure. Progress is contract, not
telemetry: journal-materialized, delta-oriented views (`ps/show/watch`).
Every parked failure and tier-3 escalation is an **open gate** carrying
stakes metadata and closure-preserving options (each option states its
outcome for the whole run — ADR 0008 R2b discipline; options include
retry-with-feedback, edit-and-resume, justified-skip, abort). Resume is an
overseer verb backed by D7 fingerprint caching and D6 session
re-adoption. Tolerated degradation (quorum losses, budget downgrades,
deferred reviews) always surfaces in the run report. The closure law is
what bounds the overseer's load: no silently-wedged state exists to hunt
for.

**Supervision policy** — how much the overseer is *in the loop* is a
run-scoped dial the overseer owns, set at plan approval, and never
written in the workflow text (where to pause is a trust/stakes judgment,
extrinsic to the task; authors may declare intrinsic `:stakes` on phases,
which the policy maps to checkpoint defaults):

```
:supervision  autonomous                  ;; default: gates/escalations only
            | (checkpoints [PhaseName …]) ;; checkpoint gates after named phases
            | phase-checkpoints           ;; every phase boundary
            | step                        ;; every node (debugging)
```

A checkpoint is an ordinary gate — closure-law compliant, auto-inserted at
the phase boundary, blocking only the dependent subtree — presenting the
phase's **artifact summaries and binding deltas, not diffs** (if the
overseer must read a diff, that is an escalation and tier economics
apply). Options: proceed / redirect (edit-and-resume) / abort. Reviewing
every boundary by default would resurrect the very cost problem the tiers
exist to solve, so supervision is a **trust dial**: first runs of a new
workflow run supervised; the dial relaxes toward `autonomous` as
calibration escape rates earn it.

**Laws (K5 — closure/adjudication core):**

- **L-K5-1 (operational closure):** for every open gate *g*, there exists
  `answer(g, o)` with `o ∈ options(g)`, recorded in the gate answer
  journal (`gate-answer-journal.jsonl`), consumed by `resume`, after
  which the run leaves the parked state. "For every read verb there is a
  write verb."
- **L-K5-2 (adjudication determinism):** answers are part of replay
  identity — `resume` after an answer replays the same decision.
  Re-adjudication is a NEW appended journal entry, never an in-place
  edit of a previous answer.

### D10 — Workflows are ephemeral request artifacts, never version-controlled code

A workflow encodes ONE request ("implement these four features in
parallel") — it is kin to a prompt, not to code. It is authored on demand
(typically by an agent), used once, and discarded. **Committing a
per-request workflow to a repository is an anti-pattern**: it references
branches/issues/files that stop existing, becoming exactly the
unowned-state rot this design bans elsewhere. (The doeff
"committed Program constants" philosophy deliberately does NOT apply
here.) The Claude Code Workflow tool being replaced already implements
the correct lifecycle: scripts persist under the session directory and
resume by run id — never in the target repo.

Contract:
- `conductor plan`/`run` **snapshot the workflow source into the run
  state directory** (next to the journal). Resume — including
  edit-and-resume — operates on the snapshot; the original path (`/tmp`,
  a session dir) may vanish freely.
- Durable workflow-shaped artifacts exist only as **named, parameterized
  templates** (library code, `:params`-instantiated); the instantiation
  is still snapshotted per run.
- What stays version-controlled: ADR/spec, env config (profiles, router
  policy), templates, and each run's state dir (journal + snapshot) as
  audit record.
- Corollary: no CI can ever see these files, which is why the
  nondeterminism gate must live in the loader (D2) and nowhere else.

### D11 — Non-blocking handler composition and the await budget axis (K4 laws)

**Context:** D1 declares "concurrency is structural" (parallel/pipeline
compile to Spawn/Gather), D1/D6 make worker waiting an agentd RPC
(`await_result`), and the runtime uses a cooperative scheduler
(`run(scheduled(WithHandler(conductor_handler, program)))`). The three
decisions individually are correct, but their **composition** was
under-specified: `DaemonAgentHandler.handle_await_result` blocked
synchronously inside the handler, freezing the scheduler's dispatch loop
and preventing sibling parallel branches from launching (live-proven
2026-06-12, run `doeff-review-20260612-1`: 6-branch parallel, only 1
session after 5 minutes). The fix bridges unbounded handler I/O through
the scheduler's `ExternalPromise` mechanism.

**Laws ratified:**

- **L-K4-1 (non-blocking handler):** An effect handler must not perform
  unbounded blocking I/O synchronously. Unbounded waits enter ONLY via
  the scheduler's Await / external-completion path
  (`scheduler.py:CreateExternalPromise` + daemon thread + `Wait`).
  Bounded fast RPCs (e.g. `launch_session`, `send_session`) may remain
  synchronous.

- **L-K4-2 (overlap observable):** For pending parallel agent nodes a, b:
  session lifetimes intersect, and
  `wall_clock(parallel(a, b)) < wall_clock(a) + wall_clock(b)`.
  Enforced by integration test (stub agentd sessions with controlled
  delay, asserting overlap and sub-additive wall time).

**Await budget axis owner** *(superseded by D13 / L-K4-3 on 2026-06-13)*:
The L2 attempt loop (`_run_agent_task` in
`doeff-agents/handlers/production.py`) remains the single authority for
validation-failure retries, but the wall-clock axis it temporarily
carried (the `timeout_seconds` task field flowing to agentd) was a
band-aid, not an owner. D13 moves wall-clock semantics to the node spec
(`:deadline-seconds`) and demotes the transport await budget to a pure
keep-alive heartbeat. This closes the open defect §11-7 from the
validation campaign.

**Cancellation contract** *(amended by D13)*: When a Gather fails fast
(sibling error), the still-pending offload threads are daemon threads
that run to their own terminal outcome: each transport round-trip is
bounded by the heartbeat (`DEFAULT_AWAIT_BUDGET_SECONDS +
RPC_TIMEOUT_MARGIN_SECONDS`), and the re-await loop ends at the node's
terminal result or its node-spec deadline. No client-side polling loops
or cancellation tokens are introduced.

**Static enforcement:** A semgrep rule forbids synchronous
`await_result()` calls inside `handle_*` methods of effect handlers in
`packages/doeff-conductor` and `packages/doeff-agents`, allowing only
the bridged/offloaded form.

### D12 — Workspace journal coverage (K3 law L-K3-3)

**Context:** Workspace identity is deterministic from `(run_id,
workspace-node identity)` and the handler is idempotent ensure-style
(D10). However, workspace **creation** was not journaled: resuming a
run created FRESH worktrees while agent sessions re-adopted their
deterministic names. Gates and reviewers then ran against an empty
workspace — false-positive green, the worst failure class.

**Law ratified:**

- **L-K3-3 (resource coverage):** Every resource materialization
  (workspace creation) MUST be recorded in a durable journal
  (`workspace-journal.jsonl`) before the orchestrator considers the
  effect complete. On resume, the journal is the authoritative record
  of which workspaces were materialized. Pre-coverage runs (agent
  journal exists, workspace journal absent) MUST fail loudly with
  `PreCoverageRunError`.

**Implementation:**

- `CreateWorkspaceJournalEntry` — frozen dataclass with `workspace_id`,
  `repo`, `branch`, `worktree_path`, `base_ref`, `issue_id` (nullable),
  `created_at`, `terminal_kind="workspace-created"`.
- `WorkspaceJournal` — flat append-only JSONL with `latest_workspaces()`
  returning last-wins-per-`workspace_id`. No generation/entry_index —
  workspace identity is deterministic, not nondeterministic.
- `JournaledWorkspaceHandler` — wraps the delegate workspace handler:
  first call for a `workspace_id` delegates AND appends; subsequent
  calls delegate but do NOT double-append. Pre-coverage detection
  raises `PreCoverageRunError`.

**Policy:** NO backward compatibility. Pre-coverage runs fail loudly.
No shim, no dual path.

### D13 — Wall-clock deadline ownership (K4-edge × K5 law L-K4-3)

**Context (the absent decision):** Wall-clock await semantics lived in an
L2 client constant, `DEFAULT_AWAIT_BUDGET_SECONDS` (3600s, agentd clamp
[1, 3600]). The 600→3600 bump during the 2026-06-11 campaign was a
band-aid; nobody owned the axis (validation ledger §11-7). A slow agent
either tripped an arbitrary transport constant — the L2 attempt loop
burned a retry per timed-out await, eventually parking a misleading
`budget-exhausted` gate on a healthily working session — or waited
invisibly. Meanwhile the unit-budget axis already had an owner: the
`budget` node annotation with its expansion-time check and the
budget-exhausted K5 gate.

**Decision (owner adjudication 2026-06-13, k8s `activeDeadlineSeconds`
semantics imported — controllers never hold deadlines; deadlines belong
to the resource spec and are observed):**

1. **Declaration.** The wall-clock deadline is an `agent!` node-spec
   attribute: `:deadline-seconds` (sibling of `budget`). The name keeps
   the unit visible at the call site — `:deadline 600` reads as ambiguous
   (units? iterations?) where `:deadline-seconds 600` does not — and the
   noun mirrors its k8s ancestor. Expansion validates it (positive,
   finite, numeric, non-bool) so malformed deadlines fail at
   plan/validate time.
2. **Observation/enforcement.** The L3 conductor runtime owns the
   deadline decision: the node spec carries `deadline_seconds` into the
   L2 attempt loop, which observes the clock against it and raises the
   typed `AgentDeadlineExceededError`; the runtime parks the run as a K5
   gate. The gate is a **named sibling** of `budget-exhausted`
   (`deadline-exceeded`, reason "wall-clock deadline exceeded") rather
   than one merged gate type: both are K5 exhaustion parks with journaled
   answers, but their forward paths differ — unit exhaustion offers
   `proceed` (accept the attempt history and re-run), while wall-clock
   exhaustion's only forward path is RENEWING the window (`extend`); a
   merged gate would make the answer vocabulary ambiguous in the journal,
   and the journal is the adjudication record (L-K5-1/2).
3. **Extension = adjudication.** The ONLY way to extend is a gate answer
   (`extend`, outcome `resume`) appended to `gate-answer-journal.jsonl`.
   Extensions are replay inputs — L-K5-2 applies automatically: replaying
   a finished run id replays the journaled artifact without re-parking.
   **NO auto-extension policy tier** — explicitly rejected for now;
   revisit only with a law if attention cost proves painful in practice.
4. **L2 demotion.** `DEFAULT_AWAIT_BUDGET_SECONDS` becomes a pure
   transport keep-alive heartbeat. The attempt loop behind the offloaded
   bridge (`make_offloaded_scheduled_handler`) re-awaits until a terminal
   result or the L3 deadline; heartbeat expiry is NEVER surfaced as a
   node failure, never burns a retry attempt, and carries no semantic
   decision. The `timeout_seconds` task field is deleted (no-backcompat
   policy); a semgrep rule (`k4-deadline-not-transport-timeout`) bans its
   return.

**Law ratified:**

- **L-K4-3 (deadline ownership):** `deadline(agent node) ∈ node spec`;
  `renewal(deadline) = gate answer ∈ journal`; transport await carries
  no deadline semantics beyond keep-alive.

**Cache identity:** like `budget` and `:retry`, `deadline_seconds` does
NOT enter the replay cache key — it bounds whether a result arrives, not
the result distribution (D7 criterion).

## Implementation plan (stages; each lands with tests)

- **C1 — agent boundary (L2 core + `agent!`)**: implement the D6 algebra
  on doeff-agents' existing fine-grained effect layer, **wrapping agentd's
  `session.await_result`** (already implemented: result file, expected
  schema, retry counters) rather than building from scratch. `agent!`
  handler = Launch → AwaitResult → validate → bounded FollowUp retry →
  typed failure; result channel handler-private per D1; Codex adapter via
  `--output-last-message`, Claude via headless structured output, tmux
  interactive via in-workdir reserved path — start with the mechanisms
  deliberately different per adapter (cheapest proof the abstraction
  holds). Stub handler is **scenario-driven** from day one. Done: a unit
  test runs a 2-node workflow entirely on stubs (incl. a failure
  scenario); an integration test runs one real Codex worker returning
  schema-valid JSON. This stage also deletes the condemned capture paths
  (see "Condemned existing code").
- **C2 — DSL + replay keying (co-designed)**: the closed vocabulary of D2
  in doeff-hy, **including** `:roles`, quorum typing, `time!`/`random!`,
  the nondeterminism validator, and the cache-key/fingerprint design from
  D7 — keying is settled here, not retrofitted in C3, because it
  constrains what the macros may emit. Expansion-time checks ship with
  failing-case tests (each rule has a test proving it rejects). Done: the
  k2-k3 reference JS script's structure expresses 1:1 (5-way parallel,
  fixer loop ≤3, test writers, gate loop ≤3, 4-way review fan-out) — note
  the gate phase decomposes into `gate!` (Exec) + conditional fixer
  `agent!`, fixing the reference's waste of an LLM agent on running
  builds.
- **C3 — replay/durability**: effect-log-backed resume; longest-valid-
  prefix caching gated on resolved fingerprints (D7); idempotent Launch
  re-adoption (D6). A test kills a run mid-phase and resumes without
  re-running completed agents, under the stub interpretation.
- **C4 — gates + workspace family**: `Exec` gate steps with structured
  results (tee'd full logs as mechanism); the git medium family —
  `workspace!`/`merge!`/conflict bounce-back, renaming the worktree-noun
  effects per D5; multi-repo workspaces. Done: parallel 3-task demo
  merging into a green gate, plus a two-repo demo.
- **C5 — attention tiers**: verdict schema, the pure router (D4/D7),
  disagreement-routes-up, per-tier bounded budgets with exhaustion→gate;
  calibration v1 = manually-set sampling rates + escape recording only
  (autonomous rate adjustment deferred); a review-routing demo where only
  BLOCKERs reach the tier-2 callback. Done-condition includes the
  closure-law model check (D8): the stub scenario suite proves no
  reachable terminal state lacks a verdict/escalation/gate.
- **C6 — verbs + overseer surfaces**: `conductor workspace describe` (the
  author's discoverable vocabulary: profiles, roles conventions, router
  defaults), `conductor plan` (binding plan for overseer approval),
  `conductor validate` (stub scenario runs); gate queue and delta-oriented
  progress views per D9.
- **C7 — pilot**: port the k2-k3 reference script and run it with Codex
  workers against a scratch repo. Measure: (a) fraction of items
  terminating at tier ≤1, (b) tier-2 token spend vs an all-Claude
  baseline, (c) calibration escape rate. Notes into `docs/pilot-k2-k3.md`.
- **C8 — single-file run hardening** (from C7's honest deltas + the
  2026-06-10 follow-up design session): (1) native `WorkflowSpec`
  production interpreter — `conductor run <workflow.hy>` executes a
  DSL-only file (authoring surface amended to Hy-only; see D2); the hand-written `main` Program requirement is deleted;
  (2) loader-enforced nondeterminism check per amended D2 — port the
  DOEFF032 fixtures into conductor tests, delete the doeff-linter rule;
  (3) workflow source snapshot into the run state dir per D10 — resume
  reads the snapshot; (4) run return payload exposure — the `report_path`
  workaround is deleted. Done: a DSL-only ephemeral file under `/tmp`
  plans, validates, runs, and resumes with journaled results; a workflow
  containing `datetime.now()` fails at plan time naming `time!` as the
  replacement.

## Condemned existing code — mark-for-fix (delete outright, no deprecation)

Policy: **no backward compatibility, no deprecation periods, no migration
ratchets.** A wrong contract is deleted and every consumer is rewritten in
the same change. Limbo states ("legacy" extras, `deprecated` markers kept
alive — e.g. doeff-agentic's `[legacy]` optional dependency on
doeff-agents) are themselves violations: resolve them by decision (promote
to a real backend or delete), never by deprecation. (Note: the
"compile-down ratchet" in the tier rules above is a review-economics
mechanism — escapes become new tier-0 checks — and has nothing to do with
preserving legacy code; it is the only "ratchet" in this design.)

The boundary that defines the violation class: **terminal capture is a
human-facing channel and an L1-private mechanism.** A tmux adapter may use
TTY heuristics internally to detect session liveness/awaiting-input (a TTY
has no other channel). What is condemned is any **domain fact or
workflow-facing result derived from captured terminal text** — the only
worker→workflow data channel is the schema-validated result artifact.

### A. Capture→domain-fact sniffing (delete)

| Where | What |
|---|---|
| `doeff-agents/src/doeff_agents/monitor.py:253-261` | `PR_URL_PATTERN` / `detect_pr_url` — regex over screen text producing a domain fact |
| `doeff-agents/src/doeff_agents/monitor.py:42` | `MonitorState.pr_url` |
| `doeff-agents/src/doeff_agents/effects/agent.py:72,102,116,139,182,205` | `Observation.pr_url` / `AgentSessionSnapshot.pr_url` (+ serialization) — domain noun inside the session vocabulary |
| `doeff-agents/src/doeff_agents/session.py:226,258-262` | `on_pr_detected` callback wiring |
| `doeff-agents/src/doeff_agents/programs.py:78,198-233,466-497` | program results carrying observation-derived `pr_url` |
| `doeff-agents/src/doeff_agents/handlers/production.py:413-418,427-434,451,565,575,598` | sniffing + propagation in the tmux handler |
| `doeff-agents/src/doeff_agents/handlers/daemon.py:144`, `handlers/testing.py:309` | propagation |

A PR URL must come from the typed result of the effect that created the PR
(`CreatePR` / gh output) or from the worker's result artifact — never from
screen text. (`doeff-conductor`'s issue-frontmatter `pr_url` fields are fed
by `CreatePR`'s typed result and are NOT part of this list.)

### B. Captured text as the workflow-facing result (replace the contract)

| Where | What |
|---|---|
| `doeff-conductor/src/doeff_conductor/effects/agent.py` legacy text-result agent effect | contract "Yields: str (agent output)" — a rendered text blob as the workflow result; superseded by `agent!` |
| `doeff-conductor/src/doeff_conductor/handlers/agent_handler.py:201-221` | `handle_capture_output` joining `[role] content` as the result |
| `doeff-conductor` templates consuming legacy text agent output (`basic_pr`, `enforced_pr`, `reviewed_pr`, `multi_agent`) | rewrite onto `agent!` (schema-validated artifact) |
| `doeff-agentic` legacy text-result agent effect contract | same text-blob shape; superseded by `agent!` |
| `doeff-conductor/src/doeff_conductor/effects/agent.py` legacy transcript capture effect | as a data channel: condemned. Survives only as the ops/human transcript capability, never yielded by workflow code |

### C. Success/failure judged from screen text

`monitor.py detect_status`'s DONE/FAILED heuristics must not be
workflow-facing truth: a session's terminal fact is `exited` (+ exit code
where available); **success is decided solely by the result artifact**
(present + schema-valid). Text heuristics survive only inside the L1 tmux
adapter for liveness/awaiting-input detection.

### D. Unsupervised worker subprocesses (delete; added 2026-06-10 post-C8)

| Where | What |
|---|---|
| `doeff-conductor/.../handlers/agent_handler.py` `CodexExecAgentBackend` + `_handle_codex_exec` | worker spawned as a direct `codex exec` subprocess — invisible (no attach/capture/steer), dies with conductor, bypasses the entire doeff-agents session layer (a parallel re-implementation of launch + result retrieval) |
| `doeff-conductor/.../cli.py` `--agent-mode` flag + `CONDUCTOR_AGENT_MODE` env handling; `make_agent_backend` selection | backend selection must not exist: **doeff-agentd is the only route for the handler** |

Root cause that motivated the bypass: `LazyAgentdClient` socket discovery
failed to find a running daemon (default-path mismatch). The fix is to
repair discovery — one canonical socket convention shared by the daemon
launcher and the client, with a loud actionable error when no daemon is
reachable — never to route around supervision.

## Non-goals / cautions

- Do not rebuild orch: doeff-conductor already owns workspaces/DAG; orch
  remains the interactive ops tool (`capture`/`send` steering, now the
  capability-gated `AttachTTY`/`CaptureTranscript`/`Steer` surface). A
  later bridge is possible, not required.
- Do not let the DSL grow dynamic graph construction in v1 — it breaks
  replay identity; static skeleton first. `pipeline` with runtime-sized
  fan-out is explicitly OPEN (spec §11): decide it together with the C2
  keying design or defer — static `parallel`/`parallel-for` cover the
  pilot.
- v1 scope cuts (structure permits, scope defers): one site per run
  (multi-site later via git-ref transport, no DSL change); the git medium
  family only (no second medium); manual calibration rates (no autonomous
  adjustment).
- The maintainer's house rules apply: this repo is upstream — verify
  locally (tests green) before merging to main; never pipe build output
  through head/tail; fail fast, no silent fallbacks (no bare `except: pass`,
  no defaulting on schema mismatch).
- Prior art to read before implementing: `README.md` + `docs/api.md` in
  this package; `doeff-agents` README (session_scope/LaunchConfig/
  monitor_session); doeff-hy macro conventions (`defk`/`defhandler`/`Try`);
  agent-control-plane ADR 0008 (closure law) and its `MergeAttempts`
  bounded-budget pattern (durable, reap-safe counters).
