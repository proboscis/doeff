# doeff-conductor — Workflow Orchestration Specification

- **Status:** Draft 2026-06-10, companion to
  [ADR 0001](adr-0001-workflow-dsl-and-attention-tiers.md). The ADR records
  *decisions and their rationale*; this spec records the *contracts* — layer
  boundaries, the L2 session algebra, the DSL grammar, binding resolution,
  and validation rules. Where they disagree, the ADR's Decision section wins
  and this spec must be fixed.
- **Policy (binding, from ADR 0001):** no backward compatibility, no
  deprecation periods, no migration ratchets. Wrong contracts are deleted
  and their consumers rewritten in the same change.

## 1. Roles and surfaces

Three parties interact with a workflow. Each sees a different surface and
none may reach below it:

| Party | Surface | Never sees |
|---|---|---|
| **Author** — an LLM agent writing a workflow on demand | the DSL (closed vocabulary, §4); expansion/validation errors; `conductor workspace describe` output | effects machinery, handler stacks, interpreters, account names, substrate names, filesystem paths |
| **Overseer** — the agent or human responsible for a run | verbs: `plan` (binding table, budget, capability check) → approve; the supervision policy (§8.1); progress views; the gate queue; `resume` | interpreter names, session internals (except via capability-gated ops escape hatches: attach/transcript/steer) |
| **System** — conductor runtime + environment config | everything: interpreter constants, profile registry, substrate handlers, stub scenarios, calibration state | — |

Interfaces are **verb-shaped**. No author- or overseer-facing interface
accepts an interpreter, handler stack, or backend name as an argument.
Interpretation selection is system-internal (§7).

## 2. Layer model and noun ownership

```
L3  intent vocabulary    agent! / gate! / workspace! / merge!  — what the DSL compiles to
L2  session algebra      Launch / AwaitResult / FollowUp / Stop / Release  (opaque handles)
L1  substrate handlers   tmux | opencode | zellij | ssh | stub  — interpret L2
L0  identity environment profile registry (ask-resolved), auth homes, model bindings
```

**Noun-ownership rule:** a layer's vocabulary must not mention a lower
layer's nouns. L3 does not mention sessions. L2 does not mention panes,
HTTP, or terminal text. Substrate nouns (`pane_id`, server URLs, capture
text) live only in L1 handler-private state, keyed by the opaque
`session_id`. The current `SessionHandle.pane_id` field violates this and
is removed: handles carry `session_id` only.

Five configuration axes, and where each binds:

| Axis | Scope | Mechanism |
|---|---|---|
| (A) intent: prompt, schema, verification class, workspace topology | task — author writes it | DSL form fields (effect payload) |
| (B) agent kind (claude/codex/gemini adapter) | bundled into profile | resolved from profile at handle time |
| (C) identity: account, auth home, model | bundled into profile | profile registry (L0), env-completed |
| (D) execution substrate / site | runtime — one site per run (v1) | interpreter composition, system-side |
| (E) persona (role prompt) | task | `:persona` field, intent-side |

The word **profile** is reserved for (B)+(C). The old agent-effect `profile`
(persona) meaning is renamed. Call-site defaults for agent kind (e.g.
`agent_type: str = "claude"`) are abolished — identity comes only from
profile resolution; defaulting at a call site is a fail-fast violation.

## 3. Profiles, roles, and binding resolution

### 3.1 Profiles are semantic names

Workflows reference **capability tiers**, never accounts or concrete agent
kinds: `:cheap-coder`, `:cheap-reviewer`, `:frontier-reviewer`,
`:frontier-author`. The environment binds names in two stages, both outside
the DSL:

- the **project env** maps semantic name → adapter + model + reasoning
  effort (`cheap-coder → codex / gpt-x / xhigh`);
- the **user env** completes it with identity (auth home, e.g. a
  `CODEX_HOME`); secrets never appear in repo config.

Reasoning **effort** is profile-owned: it is an axis of the profile
binding, participates in the resolved identity fingerprint (it affects the
result distribution, ADR D7), and is never a workflow/run parameter. House
policy binds every default profile to `xhigh`.

`stub` is NOT a profile; it is an interpreter choice reachable only through
the `validate` verb and the test suite (§7).

### 3.2 The router

`route(verification-class, stakes, remaining-budget) → profile-name` is a
**pure function**, injected as environment policy. It supplies the default
profile for any `agent!` that does not override; budget is passed in by the
conductor, the router holds no state. The DSL call-site `:profile` field is
an explicit override and is locally visible.

### 3.3 Binding cascade — single resolver, logged fingerprint

Per `agent!` node, exactly one resolver evaluates, in fixed precedence:

```
explicit call-site field  >  role entry (workflow :roles table)
                          >  router default  >  interpreter env default
```

Rules:

- Resolution happens in ONE function. No ask-with-default fallbacks
  sprinkled at call sites or in handlers.
- The **resolved fingerprint** (the result-distribution-affecting subset:
  adapter kind, model, effort, schema, prompt) is recorded in the effect
  journal. Cache hits on resume require fingerprint match — a profile
  *name* whose definition changed does not produce stale hits (§9).
- **Verification class is never resolved** — it is task-intrinsic, required
  and explicit on every `agent!`, never inherited from a role, never
  defaulted. (Misclassification is audited by calibration sampling, ADR D4.)

### 3.4 Launch-time binding plan — resolve early, execute late

Before any worker starts, the conductor resolves the cascade for every
static `agent!` node and validates:

- every profile name resolves in the registry;
- required capabilities (e.g. `interactive`, `durable-sessions`) are
  provided by the run's site/substrate;
- resolved budgets sum within the declared budget annotations;
- workspace repo names resolve.

The output is the **binding plan** shown to the overseer for approval:
"5× cheap-coder (tmux), 2× frontier-reviewer, estimated budget X,
capabilities satisfied".

## 4. The DSL

### 4.1 Principles

1. **Closed vocabulary.** The DSL surfaces none of doeff's binding
   machinery — no `ask`, no `Local`, no handlers, no interpreters, no
   session handles, no futures. Every binding carrier is replaced by a
   DSL-native concept (params, form fields, roles table, name references).
2. **Local readability.** Every `agent!` node's binding must be derivable
   from (a) the call site and (b) the flat `:roles` table at the top of the
   file. No dynamic scoping, no nesting, no shadowing — an LLM author,
   a reviewer, and the expansion-time checker all reason locally.
3. **Intrinsic facts only.** The workflow text contains task-intrinsic
   facts (prompts, schemas, classes, topology, relative budget weights).
   Everything environment-contingent (profile definitions, router policy,
   substrate, paths, auth, absolute budgets) enters by name reference.
   Portability test: the file must keep its meaning in any conforming
   environment.
4. **Concurrency is structural.** `parallel` / `pipeline` forms compile to
   `Gather`; `agent!` is one effect that awaits the worker and returns the
   validated artifact. Authors never manage asynchrony.
5. **Medium-agnostic core.** The core (workflow/phase/agent!/gate!/control
   flow/bindings/glue) mentions no version-control nouns. git+worktree+
   merge is the first *medium family* (§6); core vocabulary and checks must
   not depend on a medium family's nouns.

### 4.2 Grammar

The grammar below IS the authoring surface: workflows are `.hy` files
written in these macros. The Python dataclass IR the macros expand into is
internal — never authored, never accepted by the loader.

```hy
(defworkflow NAME
  :params {param-name schema ...}        ;; supplied at launch, schema-validated
  :roles  {role-name {:profile semantic-name
                      :retry   n
                      :persona name} ...} ;; flat table; the only sharing mechanism
  body...)

(defphase NAME [:stakes low|normal|high]  ;; progress grouping; matched against uses
  body...)                                ;; :stakes is intrinsic metadata consumed
                                          ;;   by the supervision policy (§8.1)

(agent! :role ROLE                        ;; row in :roles
        :class CLASS                      ;; REQUIRED, always explicit, never inherited
        :prompt EXPR                      ;; pure glue expression
        :schema SCHEMA                    ;; REQUIRED (JSON Schema / pydantic)
        :workspace WS                     ;; optional; a workspace value (§6)
        :files #{...}                     ;; required iff sharing a workspace
                                          ;;   with another parallel writer
        :profile NAME                     ;; optional explicit override
        :persona NAME                     ;; optional
        :retry N                          ;; optional override
        :deadline-seconds N)              ;; optional wall-clock deadline
                                          ;;   (node-spec attribute, L-K4-3;
                                          ;;   exceed parks a K5 gate)
  ;; ⇒ awaits the worker, returns the schema-validated artifact
  ;;   (plus its workspace ref when a workspace was given)

(gate!  :cmd STR :workspace WS [:timeout N])
  ;; deterministic step, no LLM ⇒ {exit-code, log-path}; ADR D3

(parallel [:quorum K] branch...)          ;; default quorum = all branches
(parallel-for [x LITERAL-SEQ] body)       ;; homogeneous static fan-out
(pipeline ...)                            ;; OPEN for v1 — see §11
(loop :max N :until PRED body)            ;; bounded, statically declared

(<- NAME expr)                            ;; result binding
(<- [a b c] expr)                         ;; destructuring bind

(time!) (random! spec)                    ;; explicit, journaled effects

;; plus: pure glue functions (prompt builders, predicates, filters)
```

### 4.3 Failure semantics

Typed, explicit, never silent (replaces the JS tool's `null` +
`filter(Boolean)`):

- `parallel` (default, quorum = all): bindings are plain values. Any branch
  failure fails the whole form; the failure path must be handled — retry,
  branch abort, or park as an overseer gate — and the expansion-time
  closure check requires one of these.
- `(parallel :quorum k)` with k < total: bindings are `Try`-typed
  (`Ok x | Err e`); direct field access on them is an expansion-time error,
  forcing explicit handling (e.g. `(oks ...)` glue). Tolerated losses are
  journaled and surfaced in the overseer's view — declared, not silent.
  Below-quorum → typed failure of the form.
- `agent!` failure (retry budget exhausted, awaiting-input under the
  default policy, typed worker error): parks as an overseer gate whose
  options are closure-preserving (each option states the outcome for the
  whole run; cf. agent-control-plane ADR 0008 R2b).
- `agent!` wall-clock deadline exceeded (`:deadline-seconds`, L-K4-3):
  parks as the `deadline-exceeded` K5 gate — a named sibling of
  `budget-exhausted` — whose only forward option is `extend` (grant one
  more deadline window; the journaled answer IS the renewal). Transport
  heartbeat expiry is NOT a failure path at all: the handler re-awaits
  transparently (§5.1.1).

### 4.4 Glue code and nondeterminism

Glue (prompt builders, predicates, transforms) is **pure Hy functions
invoked as plain functions** — not kleisli, so they structurally cannot
yield effects: no agent launches, no `ask`, no `Local`, by construction.

The remaining hole — raw Python calls that are not effects — is closed by
the **workflow loader's nondeterminism check** (an AST walk conductor runs
on every workflow module at load time, so `plan`, `validate`, and `run`
all inherit it and cannot skip it):

- ban `datetime.now` / `time.time` / `random.*` / `open` / network and
  subprocess calls / non-allowlisted imports inside workflow modules;
- each diagnostic names the replacement (`time!`, `random!`, `gate!`,
  a `:params` entry);
- load fails hard; there is no baseline, no allowlist-by-file, and no
  suppression mechanism.

The enforcement point is deliberate: workflows are ephemeral request
artifacts (§4.7) that no CI or separately-run linter will ever see. No
module marker is needed — the loader checks exactly what it loads.
(History: this check first shipped as doeff-linter rule DOEFF032; the
rule logic and fixtures move into the loader and the linter rule is
deleted — single owner, no parallel implementations.)

### 4.5 Expansion-time checks (complete list, all pre-token)

1. phase declarations vs uses; no orphan nodes; joins well-formed;
2. every `agent!` carries `:schema` and an explicit `:class`;
3. role references resolve against the `:roles` table;
4. shared-workspace parallel writers declare pairwise-disjoint `:files`,
   OR each writer has its own workspace + a downstream `merge!`;
5. closure law: every node and every failure path terminates in an
   artifact, a verdict, an escalation, or a gate;
6. quorum typing (§4.3): `Try`-typed bindings cannot be dereferenced
   directly;
7. budget annotations parse and sum;
8. binding locality: no dynamic scoping constructs; bindings come only from
   the `:roles` table and call-site fields;
9. dataflow: every reference defined before use; unconsumed results are
   flagged;
10. the loader's nondeterminism check (§4.4) over the workflow module —
    enforced at load, so no verb can skip it.

### 4.6 Example — 4 parallel feature implementations

```hy
(defworkflow four-features
  :params {:base-ref str}
  :roles  {:implementer {:profile :cheap-coder :retry 3}}

  (defphase Implement
    (<- [auth search export i18n]
        (parallel
          (agent! :role :implementer :class :test-verifiable
                  :workspace (workspace! :from base-ref)
                  :prompt (impl-prompt "OAuth2 login") :schema AuthImplResult)
          (agent! :role :implementer :class :test-verifiable
                  :workspace (workspace! :from base-ref)
                  :prompt (impl-prompt "full-text search") :schema SearchImplResult)
          (agent! :role :implementer :class :test-verifiable
                  :workspace (workspace! :from base-ref)
                  :prompt (impl-prompt "CSV export") :schema ExportImplResult)
          (agent! :role :implementer :class :test-verifiable
                  :workspace (workspace! :from base-ref)
                  :prompt (impl-prompt "i18n") :schema I18nImplResult))))

  (defphase Merge
    (<- merged (merge! :workspaces [(:workspace auth) (:workspace search)
                                    (:workspace export) (:workspace i18n)]))
    (gate! :workspace merged :cmd "cargo build && cargo test")))
```

### 4.7 Workflow source lifecycle — ephemeral by design

A workflow encodes one request; it is kin to a prompt, not to code
(ADR D10). Lifecycle contract:

- The author (typically an agent) writes the workflow **as a `.hy` DSL
  file** to **any throwaway location** (`/tmp`, a session directory) — never into the target
  repository. Committing a per-request workflow is an anti-pattern: it
  rots into unowned state referencing branches/issues that no longer
  exist.
- `conductor plan` / `conductor run` **snapshot the workflow source into
  the run state directory**, alongside the journal. The snapshot is the
  authoritative source for that run: resume — including edit-and-resume —
  reads and modifies the snapshot; the original file may vanish freely.
- The only durable workflow-shaped artifacts are **named, parameterized
  templates** (library code instantiated via `:params`); the
  instantiation is still snapshotted per run.
- Version-controlled: ADR/spec, environment config (profiles, router
  policy), templates. Run-state-controlled: journal + workflow snapshot
  (the audit record). Ephemeral: everything else.

## 5. L2 — the session algebra

### 5.1 Core (total: every substrate must implement)

```
Launch      (spec)            → handle
AwaitResult (handle, timeout) → Outcome
FollowUp    (handle, message) → handle
Stop        (handle, reason)  → ()        ;; idempotent abort
Release     (handle)          → ()        ;; resource reclamation
```

- **`Launch`** — `spec` carries: a **deterministic session name** derived
  from `(run-id, node-id, attempt)` (no randomness — required for replay
  and re-adoption); the site-local workspace view (if any); adapter +
  identity env from the resolved profile; the prompt envelope; lifecycle
  and non-interactive policy. **Idempotent:** if a session with the derived
  name already exists, the handler re-adopts it (level-triggered resume
  safety; doeff-agentd is the supervising daemon for the tmux substrate).
- **`AwaitResult`** — blocks until terminal / awaiting-input / timeout and
  returns
  `Outcome {status: exited | awaiting-input | timed-out, exit-code?, result: payload | absent}`.
  One journal entry. This is agentd's `session.await_result` generalized to
  a contract; the former Await/Fetch split is rejected (two journal entries
  and a race window between exit and result flush).
- **`FollowUp`** — the **total continuation primitive**: interactive
  substrates send into the live session; batch substrates relaunch with
  reconstructed context (adapter-private, e.g. CLI `--resume`). Used by the
  L3 schema-retry loop. Returns a possibly-new handle.

Status vocabulary is exactly `starting | running | awaiting-input |
exited`. There is no session-level DONE/FAILED: **success is decided solely
by the result artifact** (present + schema-valid + its own status fields).
Terminal-text heuristics survive only inside the L1 tmux adapter for
liveness/awaiting-input detection, and never produce domain facts or
workflow-facing results (see ADR 0001 "Condemned existing code").

### 5.1.1 Handler composition law (L-K4-1/L-K4-2)

The runtime executes as `run(scheduled(WithHandler(conductor_handler,
program)))` — a cooperative scheduler that yields between tasks only when
a handler returns or yields a scheduler effect. Two laws constrain how
handlers interact with the scheduler:

- **L-K4-1 (non-blocking handler):** An effect handler must not perform
  unbounded blocking I/O synchronously. Unbounded waits enter ONLY via
  the scheduler's external-completion path (`CreateExternalPromise` +
  daemon thread + `Wait(promise.future)`). Bounded fast RPCs (e.g.
  `Launch`, `FollowUp`, `Send`) may remain synchronous. This ensures the
  cooperative scheduler keeps dispatching sibling tasks while any single
  handler waits for an external result.

- **L-K4-2 (overlap observable):** For pending parallel agent nodes a, b:
  session lifetimes must intersect, and
  `wall_clock(parallel(a, b)) < wall_clock(a) + wall_clock(b)`. This is
  a direct consequence of L-K4-1 applied to the `agent!` handler's
  `AwaitResult` calls: since they yield to the scheduler rather than
  blocking, sibling branches' launches and awaits can interleave.

The `agent!` handler bridges its `AwaitResult` RPC through
`make_offloaded_scheduled_handler` (a `CreateExternalPromise` + daemon
thread pattern), while `Launch`, `FollowUp`, and `Release` remain
synchronous (all are bounded fast RPCs).

**Wall-clock deadline ownership (L-K4-3, ADR D13 — closes §11 item 7):**

- **L-K4-3 (deadline ownership):** `deadline(agent node) ∈ node spec`;
  `renewal(deadline) = gate answer ∈ journal`; transport await carries
  no deadline semantics beyond keep-alive.

Operationally: the workflow declares `:deadline-seconds` on the `agent!`
node (validated at expansion: positive, finite, numeric); the L2 attempt
loop (`_run_agent_task`) observes the clock against the node-spec
deadline and re-awaits in heartbeat-bounded rounds
(`DEFAULT_AWAIT_BUDGET_SECONDS`, capped to the remaining window) until a
terminal outcome; on exceed it raises the typed
`AgentDeadlineExceededError` and the L3 runtime parks the
`deadline-exceeded` K5 gate. A TIMED_OUT await (heartbeat expiry) is
transparent: never a node failure, never a retry-attempt burn. The L2
attempt loop remains the single authority for validation-failure
retries; there is no task-level transport timeout (the former
`timeout_seconds` field is deleted; semgrep rule
`k4-deadline-not-transport-timeout` bans its return). Like `budget` and
`:retry`, the deadline does not enter the replay cache key (§9
criterion: it bounds whether a result arrives, not the result
distribution).

### 5.2 The `agent!` handler is the composition proof

```
Launch → AwaitResult → schema-validate
  absent/invalid → FollowUp(validation error appended) → AwaitResult …
  (bounded by resolved :retry) → typed failure
  valid → return artifact → Release
```

The distinction *absent* (worker produced nothing — retry says "report your
result") vs *invalid* (present but schema-rejected — retry carries the
validation errors) is preserved end to end, never collapsed.

### 5.3 Result channel (handler-private)

The contract is only "AwaitResult returns the result payload attributable
to this session and attempt". **Workers launch ONLY as
doeff-agentd-supervised sessions** — observable (attach/capture),
steerable, durable across conductor restarts. Direct subprocess execution
of a worker is forbidden (an unsupervised worker is invisible and dies
with its parent); if agentd is unreachable, the handler fails loudly with
start instructions — never a fallback. Result-channel mechanisms are
adapter-private, operate inside the supervised session, and MUST work
inside the worker's sandbox boundary (no out-of-workspace writes):

- in-workdir reserved file (agentd: `.agentd-result.json`), mechanically
  excluded by the conductor from merges and `:files` checks;
- an MCP result tool exposed by the conductor (schema enforced at the
  tool-call boundary).

Handler obligations regardless of mechanism: attribution (correct session
and attempt, never a stale prior-attempt result), atomicity (no
partially-written payload observed), and absent-vs-invalid distinction.
The prompt envelope splits accordingly: L3 contributes intent + schema +
contract framing; L1 appends the mechanism instruction. **The journal
records the L3 view only** — envelope materialization happens below the
journaling boundary, so changing adapters does not invalidate caches.

### 5.4 Ops capabilities (never DSL-emitted)

```
AttachTTY(handle)            requires capability: interactive
CaptureTranscript(handle)    human-facing transcript rendering
Steer(handle, message)       ops-initiated mid-run injection
```

These exist for the overseer's escape hatches (`conductor attach` etc.) and
are reachable only from the ops CLI. Capability names (initial registry):
`interactive`, `durable-sessions` (sessions survive conductor death;
affects resume guarantees — `plan` warns when absent).

### 5.5 Sibling site-bound families

- **`Exec(cmd, workdir) → {exit-code, log-path}`** — deterministic gates
  (ADR D3). The handler tees full output to the log file as mechanism
  (never `head`/`tail` truncation); `gate!` compiles to this.
- **Workspace family** (§6) — same-site co-location with sessions.

## 6. Workspace and medium model

### 6.1 Three nouns

| Noun | Meaning | Multiplicity | Visibility |
|---|---|---|---|
| **site** | execution host (processes + filesystem) | 1 per run (v1) | system only (interpreter-bound) |
| **workspace** | logical unit of mutable state; for the git family: `(repo, ref)` | many per run | first-class DSL value |
| **worktree** | site-local materialization of a workspace | handler's business | nobody (L1-private) |

Workspaces are dataflow values: `(workspace! :from ref)` creates one,
`agent!` consumes and returns one, `merge!` combines several, `gate!` runs
on one. Paths never appear. A workspace's portable identity is the **ref**;
the worktree is a site-local cache — which is why multi-site later is a
handler extension (clone + push/fetch as transport) with zero DSL change.

Workspace identity is **resume-stable by construction**: every
workspace-producing node — explicit `workspace!`, the implicit per-`agent!`
workspace, `merge!` — derives its workspace id, and therefore its branch and
site-local worktree, deterministically from `(run-id, node identity)`,
mirroring deterministic session names (§5.1). Materialization is idempotent
ensure-style: branch and worktree both present ⇒ re-adopted as-is
(uncommitted changes preserved); worktree missing but branch present ⇒
re-materialized from the branch, never from the base ref; creation from the
base ref happens exactly once per `(run-id, node)` lifetime. For explicit
`workspace!`, node identity is the expression's source-order occurrence
(loader-reset per module), never the evaluation site — one `workspace!`
value shared by several nodes is ONE workspace.

Multi-repo is a v1 requirement (the k2-k3 reference's TASK D edits a second
repository): `(workspace! :repo "name" :from ref)`, with repo names
resolved by the environment.

### 6.2 Medium families

The general rule the core owns: **concurrent mutators of shared state must
declare one of two disciplines** —

1. **fork / reconcile** — isolated views + a deterministic reconcile node;
   conflicts bounce back as fix tasks; or
2. **write-set partition** — statically disjoint declared write sets on a
   shared view.

The git family implements both (worktree-per-task → branch → `merge!` →
tier-0 gate; or shared workspace + `:files`). Other media (document vaults,
asset directories, databases) would arrive as new closed vocabulary
families with their own checks — added by system designers, never by
authors. **v1 implements the git family only** (a scope decision, not a
structural constraint).

Stage C4 uses public workspace vocabulary; site-local materialization nouns
remain handler-private.

## 7. Verbs and interpreters

Interpreter selection is system-internal. Named interpreter constants
(committed, module-level, per doeff convention) back the verbs:

| Verb | Interpretation | Cost | Guarantees |
|---|---|---|---|
| (expansion) | none — macro checks | 0 | §4.5 list |
| `conductor plan` | record/estimate handlers | 0 | binding plan: §3.4 list |
| `conductor validate` | **stub scenario simulator** | 0 tokens | control flow, joins, schema plumbing, closure law (dynamic), resume |
| `conductor run` | production handlers | real | the run |

The stub is **scenario-driven, not happy-path**: deterministic scripts
("gate fails twice then passes", "all retries exhausted") drive every
branch, making the closure-law test a model check over failure scenarios,
runnable in CI on every commit. The C3 kill-and-resume test runs under the
same interpretation. `validate` belongs to the author⇆system loop and CI;
overseers ask for outcomes (`plan`, `--dry-run`), never name interpreters.

## 8. Overseer contract

- **Progress is contract, not telemetry.** Phases, labels, and node states
  materialize from the journal into queryable views (`conductor ps / show /
  watch`), delta-oriented ("what changed", "what is blocked on me").
- **Gate queue.** Every parked failure and tier-3 escalation appears as an
  open gate carrying stakes metadata (verification class, blast radius,
  reversibility) and **closure-preserving options** — each option states
  its outcome for the whole run (ADR 0008 R2b discipline).
- **Resume is an overseer verb.** Stable run identity; journal +
  fingerprints give longest-valid-prefix caching; sessions re-adopt by
  deterministic name where `durable-sessions` holds, and workspaces re-bind
  by deterministic identity (§6.1). The overseer chooses: resume as-is,
  resume with an edited workflow, or abort.
- **Tolerated degradation is visible.** Quorum losses, budget-pressure
  downgrades, deferred reviews — all surface in the run report; nothing is
  silently skipped (ADR D4).

The closure law bounds the overseer's cognitive load: there is no "silently
wedged" state to hunt for; every terminal is a verdict, an artifact, an
escalation, or an open gate.

### 8.1 Supervision policy — the in-the-loop dial

The overseer is in the loop by default only through filtered channels:
escalation gates (BLOCKERs, tier-1 disagreements — blocking only the
dependent subtree), calibration samples, and failure parks. Beyond that,
**how synchronously the overseer supervises is a run-scoped policy the
overseer sets at plan approval** — never written in the workflow text,
because where to pause is a trust/stakes judgment extrinsic to the task:

```
:supervision  autonomous                  ;; default: gates/escalations only
            | (checkpoints [PhaseName …]) ;; checkpoint gates after named phases
            | phase-checkpoints           ;; every phase boundary
            | step                        ;; every node (debugging)
```

- A **checkpoint** is an ordinary gate, auto-inserted at the phase
  boundary, closure-law compliant, blocking only the nodes data-dependent
  on the phase's results. It presents the phase's **artifact summaries and
  binding deltas — not diffs**; if adjudication requires reading a diff,
  that is an escalation and the tier economics of §3/ADR D4 apply.
- Checkpoint options are closure-preserving: **proceed** / **redirect**
  (edit-and-resume, §8) / **abort**.
- Authors may declare intrinsic `:stakes` on phases (§4.2); the policy maps
  stakes to checkpoint defaults (e.g. `high`-stakes phases checkpoint even
  under `autonomous` until the dial is explicitly lowered).
- Supervision is a **trust dial**, not a quality mechanism: reviewing every
  boundary by default would resurrect the cost problem attention tiers
  solve. First runs of a new workflow run supervised; the dial relaxes
  toward `autonomous` as calibration escape rates earn it.

## 9. Replay, caching, durability

- **Journal levels.** The effect journal records L3 `agent!` entries
  (author prompt, schema, resolved identity fingerprint) and L2 entries as
  handler-internal detail. Cache identity lives at L3.
- **Cache-key criterion.** A field enters the cache key iff it affects the
  result distribution: prompt, schema, adapter kind, model/identity/effort
  fingerprint. **Substrate never enters** — a run started on a laptop
  (tmux) resumes in CI (headless) with caches intact. If a cache key ever
  *needs* a substrate noun, a layer has leaked.
- **Fingerprint, not name.** Profiles are indirection; hits require the
  recorded resolved fingerprint to match the current resolution.
- **Resume.** Unfinished `Launch` at crash → idempotent re-adoption by
  deterministic session name (level-triggered, supervised by agentd on the
  tmux substrate).
- **Gate answer journal.** Gate answers (`proceed` / `extend` /
  `redirect` / `abort`) are recorded in `gate-answer-journal.jsonl` in
  the run directory. Each
  entry carries `gate_id`, `workflow_id`, `option`, `outcome` (from the
  selected `GateOption`), `note`, `answered_at`, and
  `terminal_kind="gate-answer"`. The journal is append-only; re-adjudication
  appends a new entry rather than editing in place (L-K5-2). `resume`
  reads `latest_answers()` — the last answer per gate — as the sole
  authoritative source of answered gate options. Pre-journal runs are
  dead runs; no fallback to `run-state.json`. The `extend` answer is the
  deadline-gate renewal (L-K4-3): the journaled answer IS the grant of
  one more deadline window — extensions are replay inputs, never an
  automatic policy.
- **Workspace effects are journaled for resource coverage (L-K3-3).**
  `workspace-journal.jsonl` records each workspace materialization:
  `workspace_id`, `repo`, `branch`, `worktree_path`, `base_ref`,
  `issue_id`, `created_at`, and `terminal_kind="workspace-created"`.
  The journal is flat append-only with last-wins-per-`workspace_id`
  semantics — no generation/entry_index since workspace identity is
  deterministic from `(run_id, workspace-node identity)`.
  `JournaledWorkspaceHandler` wraps the delegate handler: first call
  for a `workspace_id` delegates AND appends; subsequent calls (resume)
  delegate but do NOT double-append.  Pre-coverage detection: if an
  agent journal exists but no workspace journal does, the handler raises
  `PreCoverageRunError` — forcing the operator to start a fresh run.

## 10. Enforcement summary

- expansion-time checks: §4.5 (each rule ships with a failing-case test);
- nondeterminism check: §4.4 — enforced by the workflow loader at load
  time (no separate linter; no suppression);
- workflow source snapshot: §4.7 — every `plan`/`run` captures the source
  into the run state dir; resume reads the snapshot;
- layer-boundary lint: workflow modules must not import L1 handler modules
  or the profile registry (same enforcement pattern as agent-control-plane
  ADR 0005's semgrep boundary rules);
- closure-law model check: stub scenario suite in CI (§7);
- gate answer validation: `GateOption.outcome` must be in
  `VALID_GATE_OUTCOMES` (`resume` | `abort`); `answer` records a durable
  journal entry before any state transition (L-K5-1);
- condemned-code list: ADR 0001 "Condemned existing code" — deletions, not
  deprecations.

## 11. Open items (v1 decisions pending)

1. **`pipeline` with runtime fan-out** — requires item-keyed replay
   identity for dynamically-sized `Gather`s; design together with C2/C3
   replay keying, or defer (static `parallel` / `parallel-for` cover the
   pilot).
2. **`loop :until` predicate form** — pure glue function vs a dedicated
   restricted form.
3. **Exact `Outcome` payload shape** and the capability-name registry
   beyond `interactive` / `durable-sessions`.
4. **Calibration state store** — cross-run, level-triggered; v1 ships
   manual sampling rates + escape recording only (no autonomous rate
   adjustment).
5. **Runtime auto-commit captures session state — fixed e206be17 and
   conductor-workspace-exclude-not-gitignore.** Conductor-created workspaces
   now install repository-local `info/exclude` entries for in-worktree runtime
   state (`.agent-home/`). The exclude lives at workspace initialization, so
   post-agent-node `Commit`, manual `git add -A`, and future workspace
   consumers all share the same behavior without dirtying tracked files.
6. **Await budget axis — resolved twice; final owner ratified
   (§5.1.1, ADR D13, L-K4-3).** The D11 resolution (L2 attempt loop as
   single timeout/retry authority with `timeout_seconds` flowing to
   agentd) bounded the defect but left the wall-clock axis owned by a
   transport constant. D13 settles it: the deadline is an `agent!`
   node-spec attribute (`:deadline-seconds`), the L3 runtime parks the
   `deadline-exceeded` K5 gate on exceed, extension is only the
   journaled `extend` gate answer, and the transport await budget is a
   pure keep-alive heartbeat (the former task-level `timeout_seconds`
   is deleted).
7. **agentd raw status misreads working claude sessions as `blocked`**
   (the `❯` input box is visible mid-work). Cosmetic — contract
   validation, not status labels, decides completion — but operators and
   tooling read it; consider the activity signal for `running`.
9. **§8 attention-tier live machinery — resolved 2026-06-11.**
   Live retry-budget exhaustion parks an open gate with stakes metadata
   instead of raising a terminal workflow error; `conductor gates` lists
   open gates; `conductor answer RUN GATE_ID proceed|redirect|abort`
   records the answer and uses the §9 journal/snapshot resume path; and
   `--supervision phase-checkpoints` inserts live checkpoint gates with
   artifact summaries and binding deltas rather than diffs.
10. **`random!` true randomness + journal replay fixed `bc48f890`**:
   `random!` now emits a workflow effect handled through
   `effect-journal.jsonl`; first execution draws from OS entropy and replay
   returns the recorded value.
11. **`doeff-agents` CLI is blind to agentd sessions** (it reads the
   local-handler store) and `doeff-agentd` has no client subcommands;
   session-level monitoring currently requires reading agentd's sqlite.
