;;; Executable ADR: agent execution = effects+handlers, agentd = Hy session host.

(require doeff-adr.macros [defadr defsemgrep rule law])
(require doeff-hy.macros [deftest])
(import doeff-adr.macros [fact interpretation counterexample])


(defadr ADR-DOE-AGENTS-004
  :title "Decompose agent execution into an effect vocabulary + per-kind defhandlers, and reimplement agentd as a Hy session host that provides only 'exteriority of lifetime' (a mini control plane with sessions as resources) — conformance first, replacing Rust as the oracle"
  :status "accepted"
  :scope ["packages/doeff-agents"
          "packages/doeff-agentd (retired Rust — rollback coordinate = git tag agentd-rust-final. No in-tree frozen copy is kept: removed in issue #575 M3)"
          "docs/adr/defadr_doeff_agents_004_effects_session_host.hy"]
  :problem
    [(fact
       "agentd became a god daemon (argv assembly, trust, monitor, solicitation, judge, taxonomy, DB, socket), and the protocol physics were implemented twice, in the Rust builder and the Python adapter, with only the claude side rotting (the trust dialog permanent hang and the hooks inheritance death were fixed only on the Rust side)."
       :evidence "doeff 42fb28fa / 49b3549b (2026-07-05 live scars)")
     (fact
       "ACP is coupled not to doeff-agents but to agentd's implicit wire, and the only path for adding a new CLI (opencode/kimi/AWS family) is 'edit the Rust god daemon'."
       :evidence "ACP src/Acp/App/Agentd*.hs")
     (fact
       "session_env on the launch surface (a general-purpose env dict) was a structural back door through which auth physics (synthesized CODEX_HOME / CLAUDE_CONFIG_DIR) flowed in from the effect user — FORBIDDEN_AGENT_ENV_KEYS (a blocklist of provider API keys) already encoded the intent 'do not let env carry auth', yet the profile-directory keys could not be put on the list because the current architecture itself needed them (the very rot of a blocklist). The effect user should not know the details of auth (decided 2026-07-07)."
       :evidence "doeff_agents/_shell.py FORBIDDEN_AGENT_ENV_KEYS; CODEX_HOME resolution via session_env in the old impls")]
  :context
    [(interpretation
       "agentd's reason to exist is not interpreting effects but the exteriority of lifetime (sessions outlive their callers; there is one truth across multiple users). The semantics are doeff-native, and the daemon is merely the place that hosts the handler stack outside the lifetime. The multiplexing point is inside the host = isomorphic to kubelet/CRI, and ACP does not have multi-backend.")
     (interpretation
       "The implementation language is Hy + doeff. The monitor is not a resident continuation but a level-triggered reconciler program that re-derives from session rows (store-as-truth) every cycle — agentd becomes a mini control plane with the same ontology as ACP and needs no new theory.")]
  :decision
    [(rule R1 "The effect vocabulary is the interface: BuildLaunch / PreLaunchSetup / ClassifyPane / DeliverMessage / WireResultChannel (+substrate: SessionStore / Tmux / Fs / Env / Clock / Proc). There is one shared policy program (monitor, bounded solicitation, judge, taxonomy, result validation), and impls do not write it. The substrate's Fs / Env were added in C2 — per-kind trust physics (S11/S12: claude .claude.json temp+rename, codex config.toml, process env fallback) are kind-owned, but impls/ is substrate-clean and cannot hold raw IO, so FS / env reads also go through the effect boundary.")
     (rule R2 "Adding a kind = 1 defhandler module + kind schema + conformance green. The same module works under both direct binding (inside the caller process) and host binding (RPC forwarding) — the impl is substrate-clean (raw IO forbidden; it only yields substrate effects).")
     (rule R3 "agentd (Hy) owns only 4 points of exteriority: the socket, the single writer actor (SQLite session rows), starting the reconciler every cycle, and the lease. RPC methods are mapped to programs, which the handler stack interprets. Continuations are not persisted — the truth is only the rows.")
     (rule R4 "Conformance first: first build a black-box contract suite with the Rust agentd as the oracle (precedent: mini_conformance) + a script-driven conformance-agent (a fake CLI that consumes no real quota), and switch to the Hy implementation once it reaches parity. Retire Rust after crystallizing cargo's 93 tests + the 2026-07-05 trust/hooks scars as behavior.")
     (rule R5 "The host advertises {kind, apiVersion, schema} via kinds.list, and ACP checks against it at declaration time and fail-loud rejects unknown kinds/versions. The wire is limited to a finite versioned vocabulary (remote forwarding of arbitrary effects is forbidden). [Landed by the 2026-07-08 reduction ruling (plan ruling 9): the advertisement is only a finite table of {kind, agent_type, required_field, api_version} — the host is not given alias resolution or registry forwarding. The table and the advertisement function (the BINDING-KIND-* tables in policy.hy + binding-kind-advertisement) are the single home of the schema, and host.hy's kinds.list dispatch is pure and store-independent. 'Checks at declaration time' is refined: declaration-time fail-loud against ACP's own vocabulary is handled by ACP admission (0044 R3), and the cross-check against the host advertisement is handled by the ACP daemon's level-triggered periodic check (verifyBindingKindsOnce → BindingKindUnsupported condition) — registration and host liveness are not coupled, and the CLI `doeff-agents agentd kinds` is a pure read that does not ensure/spawn; host unreachable = no observation ≠ violation. The home of the checking law is ACP 0044 kind-verification-is-level-triggered. deftest: sessionhost_host_deftests.hy test-dispatch-kinds-list (turns red on divergence between table and advertisement).] [The same day, #15 made the version per-kind: api_version is the BINDING-KIND-API-VERSION table (claude-code = v1 / codex = v2). codex v2 = an extension of the accepted form ({codex_home} XOR {auth_file, profile_dir}, BINDING-KIND-SHAPES), and required_field became a human-readable label (the machine surface of the check is only kind+api_version — a shapes DSL was rejected as YAGNI with no machine consumer). Changing the schema while keeping the version makes the versioned vocabulary hollow and violates this rule.] [As of 2026-09 (metered-billing lane A) there are 4 kinds: flat-rate codex (v2) / claude-code (v1), and metered claude-code-metered {config_dir} (v1) / codex-metered {auth_file, profile_dir} (v1). The metered kinds are new vocabulary, so their first version is v1, and the shape, version, and required_field of the existing kinds do not change by a single character (advancing the version of a kind whose accepted form has not changed invites a false BindingKindUnsupported — per the ruling above). ⚠ The billing class is not put in the advertisement: the machine surface of the check remains (kind, api_version), and the class is carried by the kind name (table policy.hy BINDING-KIND-BILLING). It is intentional that codex-metered does not take the native form {codex_home}, so as not to create a shape where the billing class could be picked up from a home lying around in env / on the machine. deftest: sessionhost_host_deftests.hy test-dispatch-kinds-list (advertisement of 4 kinds).]")
     (rule R6 "Deployment is from a frozen environment (a dedicated pinned env). Self-reference depending on the dev venv / target-debug (the babysitter depending on the development environment it babysits) is forbidden.")
     (rule R7 "The canonical executor after retirement is the host's entry module doeff_agents._sessionhost.host: ensure_agentd starts the host via the effect StartSessionHost, and its handler starts DOEFF_AGENTD_BIN (an explicit seam) if present, otherwise `-m doeff_agents._sessionhost.host` of the running interpreter. There is no CLI command, no PATH search, and no subcommand, and the result MCP server is a separate entry, doeff_agents._sessionhost.result_mcp (2026-10-27 revision: adopted the split-entries form — the old clause's console script doeff-sessionhost and its serve / report-result-mcp subcommands were removed). The retired Rust binary is not among the resolution targets (eradicating silent rollback, ACP ADR 0045 R5). Rollback availability is provided only by git history: no in-tree frozen copy of the retired Rust is kept, and the tag agentd-rust-final placed on the commit just before deletion is the only rollback coordinate [2026-07-30 revision (issue #575 M0): the old clause 'the only reason to keep the Rust binary/source is rollback availability' implied in-tree preservation and produced (a) bare pytest bound to the retirement gate (27 known red, #556), (b) double maintenance of defect fixes (#573/PR #574 — the same defect shipped to both the Rust and Hy implementations), and (c) a rollback target still carrying old defects that fixes never reached, so it was abolished]. The prohibition on referring to it as the standard of correctness is unchanged (U1: it was never an oracle but a partial-unreliable-impl). Porting provenance notes ('oracle = main.rs:line' style docstrings) are re-anchored to the agentd-rust-final:src/main.rs:line form, resolvable in git via the tag (issue #575 M3), and the source of truth for behavior contracts is the conformance suite (the F-* table in the README).")
     (rule R8 "The JSON Schema specification is the sole authority for the semantics of result-contract validation (U1 ruling): the validator is an import of a conforming reference implementation (jsonschema), and no subset is implemented in-house. Spec conformance is verified by vendoring the official JSON-Schema-Test-Suite into the repo and running it directly through the adapter (all 1260 required draft2020-12 cases green. All 21 skips have recorded rulings: depends on a remote registry = contracts are assumed self-contained, so an unresolvable $ref is fail-loud / ECMA \\p regex = admission rejects it fail-closed at launch — with a corresponding deftest). The schema itself is validated fail-closed against the meta-schema at launch (no session is created with a broken contract). Tests that fix the old Rust implementation's fail-open behavior as expected are not kept, not even as historical pins.")
     (rule R9 "The public launch surface (both the effect vocabulary and wire launch) is auth-blind: auth/profile physics (CODEX_HOME / CLAUDE_CONFIG_DIR / raw keys) are carried only in a typed `binding` (a serialization of the bind-time configuration, a kind-discriminated schema: codex {codex_home} / claude-code {config_dir}). session_env shrinks to a non-auth overlay — any binding-owned key mixed in is typed-rejected before any side effect (ownership-based: an enumeration of known bad keys rots, ownership does not. The FORBIDDEN blocklist of provider API keys coexists as a defense at the substrate boundary). Non-auth per-launch env (observation flags, result channel wiring values, etc.) is legitimate as an overlay and is not pushed into the handler configuration (ruling: env has 2 residents, auth and run intent, and their homes differ). The per-kind auth material schema is the handler's bind-time configuration — local binding = main, host binding = the control plane that holds the binding registry (the same line as ACP 0044 R2/R5). Binding admission follows the same philosophy as ADR 0044 R3: unknown kind / kind↔agent_type mismatch / missing required field / unknown field are typed-rejected. [2026-09 revision (metered-billing lane A): the billing class is carried on the same auth-blind line — (1) **the declaration is the kind (type)**: a metered home is named by binding kind claude-code-metered / codex-metered, and the class table is the single point policy.hy BINDING-KIND-BILLING (binding-billing-class). (2) **permission is host policy**: the host's startup flag --allow-metered-billing (default off, argv only, no env of the same name is created — for the same reason as R10(d), an env declaration does not become a billing gate). A host without the flag refuses metered kinds before any side effect, and resume / fork go through the same single point (admit-launch). (3) **agreement is checked by the per-kind impl before startup**: if the home has no metered declaration (claude = apiKeyHelper in settings.json or the Vertex env pair / codex = a **non-empty** OPENAI_API_KEY in auth.json), PreLaunchSetup typed-rejects (no row created). ⚠ The test is 'the field is present and its content is non-empty' — not the presence of the field (the codex CLI writes OPENAI_API_KEY as null even for a flat-rate login. Measured: all three of the existing homes have the field with empty content. Judging by the field's presence would misclassify every flat-rate home as metered). The key's **value** never passes through the host: the policy's pure function returns only the name of the declaration, and the row's effective_identity carries only the home path and the mark billing = metered (+ for codex, the two-axis declaration codex_auth_file / codex_profile_dir for resume — spellings that are not env names, and not added to BINDING-OWNED-ENV-KEYS). The exclusion of *_API_KEY from session_env and the substrate's hard reject are **unchanged regardless of the flag**.]")
     (rule R10 "The substance of single-instance exclusion is the socket bind, and the lease is its shadow (an observation surface). This primary/secondary relation is protected by ordering and predicates: (a) host startup order is bind → store open → lease acquisition → latch clear — a competitor that loses the bind dies without touching the store or the lease. (b) ensure's spawn predicate is only 'there is no live listener on the socket' (path absent / ECONNREFUSED) — when a live listener is slow to answer the status probe, retry with a long budget (AGENTD_BUSY_STATUS_TIMEOUT_SECONDS), and if that still fails, do not spawn but error loudly (slow ≠ dead. Do not create a competing host on an unproven death). (c) heartbeat reacquires an expired lease held under someone else's name (level-triggered self-repair — the bind holder recovers from the debris of a competitor that stole the lease and died), but an unexpired lease under someone else's name remains a loud error (misconfiguration of a different socket with the same DB = the detection surface for a live double host). The decision and upsert are made atomic with BEGIN IMMEDIATE. (d) Single-supervisor principle (2026-07-26, bundled with doeff#558 / the ACP ADR 0024 revision): when the canonical socket is declared supervisor-managed in agentd.supervisor.json in the state dir, ensure does not self-spawn even on a death proven per (b) — the owner of startup and restart is the supervisor (launchd, etc.), and ensure only delegates to the declared kick_command (configuration-injected argv — doeff hard-codes neither launchd nor a label) + waits for readiness, and fails loudly if there is no kick_command. A broken declaration is fail-closed (typed AgentdSupervisorConfigError) with no silent fallback to self-spawn. The declaration lives in machine state (a file in the state dir), not in caller env — an env declaration cannot close the hole where an arbitrary caller that does not inherit env binds a stray host during a supervisor downtime window (bootout, crash throttle).")
     (rule R11 "The store-of-record does not grow in proportion to wall-clock uptime (root fix for the 2026-07-26/27 socket unresponsiveness wedge; an intentional divergence from the oracle): (a) audit events in the status-derivation family are recorded only on status transitions (row status at cycle start ≠ derived status) — a quiescent tick only upserts and does not append to the journal (the oracle recorded every tick: main.rs:4128). (b) Audit history (agent_session_events / agent_session_commands — no reader at runtime) is bounded by a retention boundary (DOEFF_AGENTD_HISTORY_RETENTION_DAYS, default 14 days): batched prune at startup + hourly (a separate actor op per batch — client ops can interleave); physical reclamation (VACUUM) only at startup (before accepting) when the freelist dominates, and forbidden while serving. (c) The hot-path listing (ListActive every tick) applies the status filter on the SQL side (idx_agent_sessions_status) and does not read terminal history rows. Measured basis: on the real incident DB of 1.54GB (session_blocked 251k rows / 1.16GB = 95%), an unfiltered listing took 165ms per call → 0.7ms with the filter; the FIFO queue delay of the single StoreActor exceeded the client timeout (probe 1s), and every agent startup failed.")
     (rule R12 "Identity of the herdr substrate (the second substrate) and keeping up with its protocol (2026-08-17 revision, measured on herdr 0.7.5 / protocol 17. Re-fired on herdr 0.8.2 / protocol 20 on 2026-09-15 with the same conclusion — agent.start's required fields {name, kind, pane_id} and workspace.create's fields {label, cwd, env, focus} are unchanged, and the added strip_ansi of pane.read [default true] does not apply to format=ansi reads): (a) The identity anchor of a doeff-owned session is the workspace label (owned by doeff via workspace.create {label, cwd, env} — protocol 17 accepts cwd/env directly, and the root pane is the session pane as is). Liveness = the label holder set is non-empty, attribution = the union of panes over all holders, kill = closing all holders, duplicate rejection = pre-query + agreement on concurrent creation (herdr-new-session-verdict). The holder set has no order (workspace_id is non-monotonic in creation order — measured w3NZ → w3N0). (b) herdr's agent.start was reshaped in protocol 17 into an API requiring {name, kind, pane_id} that 'has herdr itself start claude/codex etc. in an existing pane and wait for detection' — the kind vocabulary is the type of agent herdr starts, and its semantics do not fit TmuxNewSession (create a named shell pane; doeff sends the startup command afterward). So the correct way to keep up is not 'send kind' but not calling agent.start at all (even the old payload + kind fails with missing field pane_id — measured). (c) herdr's agent registry cannot carry session identity: when a real agent starts in the pane, herdr's detection erases the externally attached name tag within ~2 seconds (measured n=4). The registry may be read at exactly one point, **checking the existence of externally named seats** — a seat name that `ai` etc. attached in the registry (koine session.adopt's session_name) has no doeff-owned workspace, and its label is the repo name (measured: own seat = agent name s-7bfc5d5028 / label 'doeff'), so only for names with no label holder, agent.get {target: name} (exact match on the response's name) answers has-session / session-pane-ids (herdr-external-agent-pane-id-io — a single call directly under the semgrep waiver marker). It is never used for kill, duplicate rejection, or doeff-owned identity, and externally named seats are not closed by name (seats doeff did not create are observed only). The source of truth for the measurement record is conformance/herdr-physics.md.")
     (rule R13 "Installing hooks for unattended seats (2026-09-21): the delegate of `session_hooks = inherit` (2026-08-18 route-c03fe34745) = the owner of the config-dir = **doeff itself** — after credential custody, a seat's config-dir is a home doeff creates at borrow time (agentd-homes/<uuid>, no settings.json, shared by multiple seats), so merely delegating leaves the hook layer empty, and the 14 hooks that dotfiles hooks.d declares it distributes to unattended seats were not called even once, neither on pods nor on the company Mac (measured 2026-09-21; only seats with cwd=$HOME happened to read ~/.claude/settings.json as the project layer). So doeff carries it: the seat settings file named by the machine's join declaration `[agentd].claude_settings_file` (its content and semantics are dotfiles `claude-hooks/seat-settings.json` = one sheet of hooks.d proxy registrations; per-host differences are in that host's join declaration [the Mac's toml / the pod's ConfigMap]) is put, together with `autoMemoryDirectory`, into one JSON at the **single merge point** of `build-claude-argv`'s `--settings` (both tui and headless — headless_argv borrows build-claude-argv). Known shape = kubelet-style (same as ADR-012 R51's seat_env: declaration → JoinSpec → env DOEFF_AGENTD_CLAUDE_SETTINGS_FILE → read at every startup → argv). The join gate 'refuses only errors in the declaration itself' (join.claude-settings-declaration-of + runtime.join_plan): (d) session_hooks is inherit (**regardless of whether the file exists** — a mismatch between 2 lines of the declaration file) (b) if present, a JSON object (c) if present, does not contain keys doeff places (impls/claude_code.hy CLAUDE-SETTINGS-OWNED-KEYS = disableAllHooks / autoMemoryDirectory) — an agentd with an off declaration does not join, with a reason. ⚠ **A missing file is not a reason to refuse** (correction of 2026-09-21 — request §10-2, acceptance 8): the host's entry has a legitimate degrade, 'on days when it cannot align to the tip, fall back to the image's lower bound and start' (the note on init in ACP acpcluster.yaml, real case 2026-09-18), and that day's checkout has no seat-settings.json. Refusing to join there would turn that degrade into **capacity 0 for the whole pool** (worse than today's defect). So absence joins and **announces itself**: one line at join time (seat-settings-file-absent), one line **at every seat startup** (launch.claude-settings-declaration — the argv is byte-identical to days without the name), and labels.seat-settings = present / missing on the node row (judgment.node-labels-of; the observation fields of the contract are not extended, because while ACP's kind registration is stale such writes are refused with 400 and the whole observation freezes). 'Do not silently start a seat without hooks = do not silently create 49b3549b's \"all safety hooks gone\"' is guaranteed by these 3 announcements, not by refusal. Even when absent, the path is put in env (it is read at every startup, so hooks arrive from the moment the host's checkout catches up). A key collision fails loudly at the argv merge point. On machines without the declaration, the argv does not change by a single byte (S13's pin). Nothing is written into the home (config-dir) — an external writer to a home shared by multiple seats would race with startup and create 2 definition points for the installation (rejected option B). To roll back = delete the one line of the declaration and roll agentd once. An agentd that does not know this key refuses to join with 'key not in the declaration' (so old machines do not silently drop the name), so the declaration lands after the machines that have this version of doeff. deftest: sessionhost_impls_deftests.hy test-claude-argv-merges-the-declared-seat-settings / test-claude-argv-refuses-seat-settings-that-collide-with-doeff-keys / test-claude-argv-without-declared-seat-settings-is-byte-identical, tests/test_sessionhost_acp.py test_join_claude_settings_file_*, sessionhost_launch_deftests.hy test-launch-claude-carries-the-declared-seat-settings-file / test-launch-claude-refuses-a-broken-seat-settings-file / test-launch-claude-names-an-absent-seat-settings-file-and-still-opens-the-seat, tests/test_sessionhost_acp.py test_join_plan_admits_the_seat_settings_file_and_joins_when_it_is_absent / test_node_row_names_whether_the_seat_settings_file_was_there.")
     (rule R14 "⚠ Retired (2026-09-26): the dedicated cache ping operation below (cache_host / the cache arm of acp) left doeff together with agentd. The law the-host-reports-observations-and-the-control-plane-decides-the-deadlines and its check also withdrew. The principle (the container carries observations; deadline decisions belong to the control plane) remains. The container's host **holds only mechanism, not policy** — what goes on the wire is observed fact, and the 'until when / when' decision is held by the control plane (2026-09-22): the deadline for **how long to keep** the target of the dedicated operation (cache ping) as a warm session moved from the host's `cache-resident-retention` to the single point `judgment.cache-resident-retention-of` in the control plane. What the host announces is exactly `cache_last_success_at_ms` on the wire (the completion time of the last **successful** dedicated operation in that session; read via cache_host_store.cache_last_success_at = a single index read of success receipts; None if absent), and it holds neither the eligibility filter (claude / headless / multi_turn / running / has a conversation) nor the retention budget (CACHE_RESIDENT_IDLE_MS). ⚠ **Executing** the dedicated operation (starting the process, reading events, persisting the receipt) is host mechanism and stays in the host. Root = the premise for splitting ownership of the ACP arm and the container into separate processes (ADR-DOE-AGENTS-012 R57) is 'the host holds no policy outside itself'; if policy stayed on the host side, every change to that policy would mean taking down the container's process.")
     (rule R15 "A store write failure is announced **in its original form**, and if it persists the host drops readiness (real case 2026-09-25: the state volume agentd-state (2Gi) of k3s agentd-pool-0 / -1 reached 100%, and sqlite's COMMIT failed with `database or disk is full`. The 3 lease functions did an unconditional ROLLBACK in except, so `cannot rollback - no transaction is active`, after SQLite had already rolled back on its own, hid the original failure. On pool-1 the host kept restarting; pool-0 stayed Running with every write failing, and readiness (socket connect) stayed green while it kept accepting turns). (a) Explicit transactions are opened only at the single point store.hy `db-immediate-transaction` — BEGIN IMMEDIATE → body → COMMIT; on failure, ROLLBACK only when `conn.in_transaction` is true, and re-raise the original exception as is (a rollback failure is attached as a note, not overwritten). (b) Storage write failures (SQLITE_FULL / IOERR / READONLY / CANTOPEN / CORRUPT / NOTADB — store_health.storage_failure_name) are counted at the single point of the store's actor, and the count resets once a write goes through (level-triggered). When the consecutive count reaches DOEFF_AGENTD_STORE_WRITE_FAILURE_LIMIT (default 3), `daemon.status` announces ready = false with a reason, and `doeff-sessionhost ready --socket <path>` exits with 1. ⚠ Switching the k8s readinessProbe to this port is a deployment change (agent-control-plane's deploy/acp-control/acpcluster.yaml) and is not included in this version.")]
  :laws
    [(law store-transactions-surface-the-original-failure
       :statement "for_all explicit transaction t on the store connection c: t is opened and closed only by store.db-immediate-transaction (db-acquire-lease, db-heartbeat-once and db-release-lease call it and spell no BEGIN / ROLLBACK of their own; no other sessionhost code spells BEGIN / COMMIT / END / ROLLBACK / SAVEPOINT / RELEASE, commit, rollback or executescript on c — db-migrate and db-seed-from are the named exceptions; a body that closes t anyway is named: raised on success, noted on the failure it raises); t runs on the store actor's thread (an actor op that returns an unexecuted Program / effect is refused with TypeError and writes nothing); for_all failure e raised inside t (statement or COMMIT): the exception that leaves t is e itself (same type and message), c.in_transaction = False afterwards, and ROLLBACK is issued iff c.in_transaction held at the time of the failure"
       :counterexamples
         [(counterexample "unconditional ROLLBACK in except (until 2026-09-25): after SQLite rolled back on its own on SQLITE_FULL, the ROLLBACK emits `cannot rollback - no transaction is active`, only the rollback message remains in the log, and the disk full cannot be read")
          (counterexample "raising a ROLLBACK failure instead of swallowing it — the second exception overwrites the first. A rollback failure is attached as a note to the original exception")
          (counterexample "writing try / ROLLBACK in each lease function — on the day only one of the 3 places is fixed, the same disease remains. The transaction shape lives in one point")
          (counterexample "calling a helper that commits on its own from the transaction body (until 2026-09-26 cache_receipt_put fired conn.commit()) — the transaction ends in the middle of the body, and the writes up to that point become final. The success path turns into `cannot commit - no transaction is active`, and on the failure path the finalized writes silently remain (blind test A)")
          (counterexample "an actor op returning the transaction's Program without executing it — the Program runs outside the actor's thread using the actor's connection, bypassing serialization, write-health counting, and the journal signal (blind test B)")]
       :enforcement ["packages/doeff-agents/tests/test_sessionhost_store.py::test_lease_transaction_surfaces_disk_full_not_the_rollback"
                     "packages/doeff-agents/tests/test_sessionhost_store.py::test_lease_acquire_and_heartbeat"
                     "packages/doeff-agents/tests/test_sessionhost_store.py::test_lease_release_owner_idempotent_and_successor"
                     "packages/doeff-agents/tests/test_sessionhost_store.py::test_transaction_refuses_a_body_that_closes_the_transaction"
                     "packages/doeff-agents/tests/test_sessionhost_store.py::test_store_actor_refuses_an_op_that_returns_a_program"
                     "packages/doeff-agents/tests/test_sessionhost_transaction_owner.py::test_only_the_transaction_type_opens_and_closes_transactions"])
     (law host-readiness-falls-when-store-writes-keep-failing
       :statement "for_all host h with store actor a and limit L = DOEFF_AGENTD_STORE_WRITE_FAILURE_LIMIT (default 3): for_all op o run by a: storage_failure_name(error(o)) ≠ None ⇒ consecutive_failures += 1; o changed the store (total_changes advanced) without such a failure ⇒ consecutive_failures = 0; any other op leaves it unchanged; daemon.status(h).ready = (consecutive_failures < L) and, when false, not_ready_reason names the count, the first failing time and the last SQLite error name; `doeff-sessionhost ready --socket <path>` exits 0 iff the answer says ready = true (no answer, an error or a missing field ⇒ 1); constraint violations, program errors and lock waits are never counted"
       :counterexamples
         [(counterexample "measuring readiness by socket connect alone (pool-0 on 2026-09-25): a host that cannot write keeps answering, stays green, accepts turns, and all of them fail at the write stage")
          (counterexample "counting failures per caller (heartbeat, monitor, RPC) — the counting points split, and when any one of them succeeds the others' failures become invisible. The counting point is the single point of the actor that every op passes through")
          (counterexample "resetting the count on a successful read — reads succeed even when capacity runs out, so it returns to ready while still unable to write")
          (counterexample "dropping readiness even on IntegrityError (a caller bug) — removing the host from placement does not fix it, and one wrong call reduces the pool's capacity")
          (counterexample "once dropped, never recovering until restart — it does not accept turns even after capacity returns. Recover once a write goes through (level-triggered)")]
       :enforcement ["packages/doeff-agents/tests/test_sessionhost_host.py::test_daemon_status_drops_readiness_when_store_writes_keep_failing"
                     "packages/doeff-agents/tests/test_sessionhost_host.py::test_store_health_counts_only_storage_failures"])
     (law protocol-physics-has-one-home
       :statement "protocol_physics(kind) => single_defhandler_module never_duplicated_across_languages"
       :counterexamples
         [(counterexample "implement the same CLI's physics twice, in the Rust builder and the Python adapter, and fix only one of them")])
     (law daemon-owns-only-exteriority
       :statement "session_host => socket + writer_actor + reconciler_schedule + lease; semantics_lives_in_programs_and_handlers"
       :counterexamples
         [(counterexample "bake the solicitation count or taxonomy into host-specific code")])
     (law truth-is-rows-not-continuations
       :statement "monitor => level_triggered_rederivation_from_session_rows; continuations_never_persisted"
       :counterexamples
         [(counterexample "serialize the continuation of an interrupted program and restore it on restart")])
     (law store-growth-is-bounded-by-recent-activity
       :statement "store_size => f(activity_within_retention_window) not f(wall_clock_uptime); quiescent_tick => upsert_only_never_journal_append; hot_path_list => sql_side_status_filter"
       :facts
         [(fact
            "R11 implementation landed (2026-07-27): finalize's event recording is transition-guarded (policy.hy entry-status), retention prune of events/commands (store.hy db-prune-history / actor-prune-history, timestamp-column index) + conditional VACUUM at startup (db-vacuum-if-bloated), status pushed down into SQL in db-session-list. deftest: sessionhost_policy_deftests test-blocked-quiescent-tick-records-no-event / test-blocked-transition-records-single-event-across-cycles / test-running-quiescent-tick-records-no-observed-event, sessionhost_store_deftests test-session-list-status-filter-applied-in-sql / test-prune-history-retention-and-batching / test-actor-prune-history-converges / test-vacuum-if-bloated-threshold."
            :evidence "2026-07-27 real incident: agentd.sqlite 1.54GB (session_blocked 251k rows / 1.16GB), process sample = all handle_stream threads waiting on locks, store thread spilling an external sort (~/.local/state/doeff/sessionhost-wedge-sample-20260727.txt)")]
       :counterexamples
         [(counterexample "keep INSERTing a full-snapshot event every tick for a row that stays quiescent in blocked (the mechanism of the 2026-07-27 real incident)")
          (counterexample "ListActive full-scans all history rows including terminal ones + external sort + JSON-decodes every row, and then filters on the Python side")
          (counterexample "run VACUUM in an actor op while serving, starving the client probe (1s) for several seconds")])
     (law effect-user-is-auth-blind
       :statement "public_launch_surface_args => intent_plus_nonauth_overlay_only; auth_material_rides_typed_binding_owned_by_handler_binder"
       :facts
         [(fact
            "R9 implementation landed (2026-07-07): sessionhost's launch admission performs the binding check + rejection of overlay-owned keys before any side effect, and tmux env = overlay ∪ binding-derived auth env (the only source of synthesis is the per-kind impl's identity — the trust write target and the agent's read structurally agree). The ACP engine sends bindingWireValue as a typed field, retiring direct stuffing of auth into launch env. Live verification: auth-in-env is loudly rejected / binding-derived CLAUDE_CONFIG_DIR and the non-auth overlay arrive side by side in the tmux session env."
            :evidence "doeff agentd-retire-rust 0ce87c84 (sessionhost/policy.hy BINDING-OWNED-ENV-KEYS + binding-admission-error / launch.hy R7 admission / sessionhost_launch_deftests.hy test-launch-allows-non-auth-overlay-env, test-launch-rejects-auth-in-session-env) + doeff-agent-haskell 789e3a8 + ACP 7b7cac6")
          (fact
            "The local-binding (in-process) surface of R9 is also complete (2026-07-08): as the home of auth = bind-time configuration, CodexRuntimePolicy (codex_home) was added (the codex counterpart of ClaudeRuntimePolicy), and TmuxAgentHandler / DaemonAgentHandler / all factories / tmux-agent-defhandler / codex-handler (Hy, `codex-home` param) receive it via constructor injection. The overlay guard assert_session_env_is_non_auth_overlay is laid at every local launch entry (production / daemon / session.py / claude.hy / codex.hy) — the ownership set is a single implementation that imports overlay-env-offenders from sessionhost/policy.hy, so the local guard and host admission cannot drift. DaemonAgentHandler forwards codex auth as a typed wire binding (policy > binder process env), retiring direct stuffing into session_env — a repair of a path that was broken live against R9 host admission (a landmine that conductor's codex startup stepped on). The local codex trust write on the daemon path was also retired (pre-launch physics are solely owned by the host per-kind impl). Mutation-verified (killing _wire_binding → red)."
            :evidence "packages/doeff-agents: runtime.py CodexRuntimePolicy / shell.py assert_session_env_is_non_auth_overlay / handlers/{production,daemon}.py / handlers/{codex,claude,effectful}.hy / tests/test_agentd_client.py test_daemon_agent_handler_sends_codex_binding_from_policy, test_daemon_agent_handler_rejects_codex_home_in_session_env / tests/test_session_backend.py test_tmux_agent_handler_rejects_codex_home_in_session_env")
          (fact
            "The accepted form of the codex binding was extended in #15 (2026-07-08, v2) to an XOR of two forms: {codex_home} (native home — the permanent residents are the daemon local binding CodexRuntimePolicy, the conformance harness, and the CODEX_HOME= escape hatch) or {auth_file, profile_dir} (the control plane's two-axis declaration — the host's per-kind impl synthesizes a view in $XDG_STATE_HOME/doeff/agent-homes/ via FsComposeHomeView and merges into the native form). Mixed, partial, or unknown fields match no shape and are typed-rejected (exact match against BINDING-KIND-SHAPES). If a binding is present, the process env fallback is never reached (pinned with a decoy in a deftest). Verifying the existence of the declared paths is a typed fail at the start of synthesis — the receiver for retiring ACP-side ensure-agent-home (registration-time verification) (ACP 0040 R2 revision)."
            :evidence "packages/doeff-agents: sessionhost/policy.hy BINDING-KIND-SHAPES/binding-admission-error / sessionhost/impls/codex.hy codex-view-root, codex-pre-launch / sessionhost/effects.hy FsComposeHomeView / sessionhost/substrate.hy compose-home-view / tests/sessionhost_launch_deftests.hy test-launch-composes-view-for-two-axis-codex-binding, test-launch-rejects-malformed-binding (XOR mutation → red)")
          (fact
            "Adding the billing class (2026-09, metered-billing lane A) also leaves the public launch surface unchanged: only 2 kinds and 1 host flag were added, and not one thing changed in the effect vocabulary, the wire launch arguments, or the meaning of session_env (auth material stays in the typed binding; key values appear on no surface). A caller using a metered home only states the kind and does not know the authentication physics (where the key lives, how it is read)."
            :evidence "packages/doeff-agents: sessionhost/policy.hy BINDING-KIND-BILLING / binding-billing-class / claude-home-metered-reading / codex-home-metered-reading, sessionhost/launch.hy the billing-class checkpoint in admit-launch, sessionhost/host.hy --allow-metered-billing / tests/sessionhost_launch_deftests.hy test-launch-admits-metered-kind-when-host-allows (pins that keys are not put on the row or in env)")]
       :counterexamples
         [(counterexample "the effect user assembles a synthesized CODEX_HOME and passes it via session_env (the old form — launch admission typed-rejects it)")
          (counterexample "add an authFile argument to LaunchAgent / wire launch, so the user program knows account physics")
          (counterexample "remove the ownership guard on session_env because non-auth per-launch env became necessary (the overlay stays non-auth only; auth goes to the binding)")
          (counterexample "the same program does not run in test and production (because auth sits in effect arguments, the substitution point becomes caller code rather than configuration)")])
     (law liveness-authority-is-the-socket
       :statement "exclusivity => socket_bind_primary AND lease_is_shadow; ensure_spawn_predicate => absence_of_live_listener_only AND socket_not_supervisor_declared; slow_status_never_spawns_competitor; supervised_socket => delegate_to_kick_command_or_loud_fail_never_self_spawn"
       :facts
         [(fact
            "Origin of R10 = the 2026-07-07 ensure spawn spiral (live outage): ensure_agentd misdiagnosed daemon.status's 1s timeout as 'dead' → spawned a competing host on the same DB → the child (because of the old startup order store→lease→bind) first stole the expired lease, then died on the socket collision → the real heartbeat endlessly repeated 'lease owner changed: expected 68021 got 88831' and the lease rotted under a dead pid's name → attend launch was delayed 11 minutes and wrongly judged orphan. Three-layer root fix = R10 (a)(b)(c). Mutation-verified (inverted predicate / reversed startup order / removed expiry check → each pin red)."
            :evidence "packages/doeff-agents/tests/test_agentd_client.py test_ensure_agentd_never_spawns_against_live_but_slow_listener, test_ensure_agentd_waits_out_slow_status_from_live_listener, test_ensure_agentd_spawns_when_socket_file_is_stale / tests/sessionhost_host_deftests.hy test-main-loser-dies-at-bind-before-touching-store / tests/sessionhost_store_deftests.hy test-lease-acquire-and-heartbeat (the heartbeat reacquisition phase for an expired lease under someone else's name)")
          (fact
            "Origin of R10(d) = doeff#558 (2026-07-26): the live deploy put sessionhost under launchd management (KeepAlive=true, ThrottleInterval=5), but ensure's (b) predicate still justified self-spawn on 'proof of no listener' alone, so the same socket/store had 2 supervisors. When ensure ran during a launchd downtime window (bootout maintenance, throttle after a crash, plist replacement), a stray host not managed by launchd grabbed the bind first, launchd-side restarts repeatedly lost and died under bind-first (KeepAlive churn), and the store was held by a process invisible to launchctl, so the next maintenance operation split-brained (isomorphic to the 2026-07-23 migration split-brain incident). Root fix = declaration of supervision ownership (agentd.supervisor.json) + ensure's delegation/loud-fail (self-spawn forbidden) + .semgrep.yaml doeff-agents-agentd-spawn-only-via-ensure-boundary (sole ownership of the spawn path). Shipped together with the simultaneous revision of ACP-side ADR 0024."
            :evidence "packages/doeff-agents/tests/test_agentd_client.py test_ensure_agentd_supervised_socket_refuses_self_spawn_without_kick, test_ensure_agentd_supervised_socket_delegates_to_kick_command, test_ensure_agentd_supervised_kick_failure_is_loud, test_ensure_agentd_supervised_kick_then_still_dead_is_loud, test_ensure_agentd_supervisor_declaration_for_other_socket_is_inert, test_ensure_agentd_supervised_ready_daemon_needs_no_kick, test_ensure_agentd_malformed_supervisor_declaration_is_loud")]
       :counterexamples
         [(counterexample "treat a status probe timeout as death and spawn (slow ≠ dead. The measured class of a busy writer actor)")
          (counterexample "the host takes the lease before binding the socket (the loser touches the lease/latch before dying)")
          (counterexample "heartbeat keeps raising unconditionally on owner change and never recovers from expired debris")
          (counterexample "heartbeat even takes back an unexpired lease under someone else's name (the detection surface for a live double host disappears)")
          (counterexample "for a socket deployed under launchd (KeepAlive), ensure proves the absence of a listener during a bootout / throttle downtime window and directly spawns an unsupervised host (doeff#558 — 2 supervisors. When the stray host wins the bind, the store passes to a process invisible to launchctl, causing split-brain)")
          (counterexample "carry the supervisor declaration in an env var, leaving the self-spawn path open for callers that do not inherit env")
          (counterexample "ignore a broken declaration file with only a warning and fall back to self-spawn (fail-open — a typo silently disables delegation)")])
     (law reads-never-start-a-host
       :statement "cli_observation_verb => client_bound_to_expected_socket_only AND never_ensure; unreachable_host => no_observation_and_nonzero_exit_naming_the_start_command; host_spawn_entry_points => ensure_verb_xor_explicit_ensure_flag; admission_is_structural_never_an_interactive_confirmation"
       :facts
         [(fact
            "Origin = 2026-09-15 (inventory of the surfaces a handover recipient fires first): the CLI's observation verbs (`ps` / `watch` / `output` / `agentd by-conversation`) went through `_agentd_client_or_exit` → `ensure_agentd`, so on a machine where a resident service (launchd / systemd) owns the host, firing an observation at a moment when the expected socket has no listener (bootout, crash throttle, plist replacement) could structurally start a competing host. R10(d) closed ensure's own self-spawn with the supervisor declaration, but machines without the declaration, and observations naming a socket other than the declared one (when `DOEFF_AGENTD_SOCKET` points at the join location), fall into the self-spawn branch. Root fix = detach the single point that creates the observation client (cli.py `_observing_client`) from ensure, and close the ways to start a host to two: `agentd ensure` and the explicit `--ensure`. It extends to all observation verbs the property `agentd kinds` already had per R5's fact ('host unreachable = no observation ≠ violation'), and kinds reads the same single point."
            :evidence "packages/doeff-agents/tests/test_cli_agentd.py test_observation_verbs_never_start_a_host, test_observation_verbs_start_a_host_only_with_the_explicit_flag / .semgrep.yaml doeff-agents-cli-monitoring-uses-agentd")
          (fact
            "2026-10-27: each CLI verb now runs a program that emits effects under a handler, and the single point that creates the client moved from the CLI (`_cli.py`) to the handler side — `session-host-handlers` in `handlers/_session_host.hy` creates exactly one `LazyAgentdClient :start-host`, and that one client answers both the public session effects (daemon-agent-handler) and the effects inside the host's port. All the CLI passes to the handler is the `start_host` flag, which is False for observation verbs (True only with an explicit `--ensure`). A False client talks only to the named socket (or the default path if none, including `DOEFF_AGENTD_SOCKET`) and does not call `ensure_agentd`."
            :evidence "packages/doeff-agents/tests/test_cli_agentd.py test_observation_verbs_never_start_a_host, test_cli_builds_no_client_and_runs_only_effect_programs / this ADR's deftest test-adr-doe-agents-004-reads-never-start-a-host")]
       :counterexamples
         [(counterexample "an observation verb reads unreachability as 'a signal to start' and starts a host (a stray host wins the bind during the resident service's downtime window, and the store passes to a process invisible to launchctl — isomorphic to R10(d))")
          (counterexample "decide whether to start by an interactive confirmation (y/n) (in non-interactive agent operation the confirmation never appears and it silently falls to the default side)")
          (counterexample "make 'start' the default for reads and make not-starting the flag (the default is the dangerous side — a caller who forgets the flag causes an accident)")
          (counterexample "assemble the observation client per verb, with only one of them reverting to ensure (a second definition point — new observation verbs are silently born on the starting side)")
          (counterexample "treat stop (`stop`) the same as observation and kill the substrate directly without going through the host (the session ends outside the ledger, and the host's row remains running)")])
     (law conformance-before-cutover
       :statement "rust_retirement => hy_impl_passes_oracle_conformance including_trust_and_hooks_scars"
       :facts
         [(fact
            "The first half of the C0-2 cutover gate is achieved: conformance suite 31/31 green on the Rust oracle (all P green; S14 recorded as X, expected-red) + cargo test -p doeff-agentd 94 passed / 0 failed."
            :evidence "packages/doeff-agents/conformance @ doeff 0b67cd5c (2026-07-05): pytest 31/31, cargo 94/0")
          (fact
            "The second half of the C3 cutover gate is also achieved: the Hy session host became all green on the hy gate (S14 on the positive side), and the canary cutover happened on 2026-07-06 (same socket, same 4.6GB store). After the cutover, verified through a real steward attend, a 24KB synthetic session, making it a resident launchd service, and the identity probe (ACP ADR 0045)."
            :evidence "doeff agentd-c1-base fa41774d (C3-1 LANDED) + ACP docs/acp-2026-07-06-executor-cutover-closure-architecture-plan.md F1-F7")
          (fact
            "Rust retirement got GO on 2026-07-06 and was executed: E.2's 1-week no-regression window was shortened. Rust is preserved as both binary and source — but only for rollback availability, not as the standard of correctness."
            :evidence "ACP plan U1 / ruling ledger 8 (docs/acp-2026-07-05-agentd-hy-session-host-plan.md)")
          (fact
            "The above 'Rust is preserved as both binary and source' was revised on 2026-07-30 (issue #575 M0): the home of preservation moved from in-tree to git history (the tag agentd-rust-final on the commit just before deletion). The current law's source of truth is the R7 revision and the law retired-impl-lives-only-in-git-history."
            :evidence "doeff issue #575 / this ADR's R7 revision (2026-07-30)")
          (fact
            "U1 ruling on the same day: the Rust implementation's schema validation was an unsanctioned subset (silently ignoring items/enum/additionalProperties, etc. = fail-open), and the parity port had laundered this omission into the contract (exposed by a real ACP steward outage). Fix = replace the validator with an import of jsonschema (the reference implementation), and S20 freezes the restored contract (in-session reject→fix on an items violation / launch rejection of a malformed schema). Lesson: the definition of correctness at a contract enforcement boundary (a coupling core) must not be grounded in measurements of an implementation produced below the frontier — if a specification exists, the specification is the oracle."
            :evidence "doeff#482 / conformance test_s20_schema_vocabulary.py / sessionhost/schema.hy / ACP sandbox invocation inv_wi_57cbac033483bed5_a1")]
       :counterexamples
         [(counterexample "switch to the Hy version without conformance, regressing the solicitation/turn-end hardening")])
     (law retired-impl-lives-only-in-git-history
       :statement "retired_implementation => git_history_only(rollback_tag agentd-rust-final); in_tree_frozen_copy_forbidden AND new_code_reference_forbidden(packages/doeff-agentd, doeff_agentd); porting_provenance => tag_qualified_form; behavior_contract_authority => conformance_suite_F_table"
       :facts
         [(fact
            "Measured costs of keeping the crate frozen in-tree even after retirement GO (2026-07-06): (a) bare pytest bound to the retired Rust gate with 27 known red (#556), (b) double maintenance of the same defect — the fix for #573's claude spinner false detection became a double shipment to both the Rust and Hy implementations (PR #574), (c) a landmine where the rollback target keeps carrying old defects that fixes never reach. The 2026-07-30 revision (issue #575 M0) abolished the in-tree preservation clause and unified the rollback coordinate into the git tag agentd-rust-final on the commit just before deletion. Renaming contract vocabulary (agentd_client, agentd.sqlite, socket name, CONFORMANCE_AGENTD_BIN, wire method names) is outside the scope of issue #575 and left as is — only 2 forms are forbidden: the crate path packages/doeff-agentd and the module name doeff_agentd."
            :evidence "doeff issue #575 (initial inventory 2026-07-30) / #556 / #573 / PR #574")]
       :counterexamples
         [(counterexample "keep a frozen copy of the retired implementation in-tree and keep shipping the same defect fix to both the active sessionhost and the retired Rust (the double maintenance of #573/PR #574)")
          (counterexample "the default path of conformance / bare pytest is bound to building the retired implementation (cargo), making known red the norm (#556)")
          (counterexample "new code references packages/doeff-agentd / doeff_agentd, reviving dependence on the retired implementation")
          (counterexample "delete the crate without placing a rollback tag, making rollback availability a verbal promise")])
     (law capacity-counts-only-launch-owned-rows
       :statement "launch_admission_denominator => active_and_not_adopted_rows_only; adopted_rows_are_observations_not_capacity_consumers; ownership_predicate_not_lifecycle_predicate; cap_breach_remedy_is_never_reaping_exempt_rows_nor_raising_the_cap"
       :facts
         [(fact
            "Starting measurement (2026-08-17, read-only diagnosis by the wave 1-S1 seat): in the interactive-seat SessionHost ledger (agentd-herdr.sqlite), all 32 active rows were adopted=1, lifecycle=interactive (registered by a one-off manual run on 07-21), and because launch's admission check (the max_running check in launch.hy) counts all rows in the active state, 32 ≥ the limit 10 and session.launch was permanently rejected 100%. Root cause = adopted/interactive rows have no writer of terminal transitions (ADR-007 interactive-rows-are-never-reaped correctly forbids it), so the active count only increases monotonically, while the capacity guard counted rows registered as observations as capacity consumers — a type mismatch in the denominator. Raising the limit, however far, recurs. Root fix = change the denominator predicate to 'launch-owned rows = active ∧ not adopted' (policy.hy counts-toward-launch-capacity — a pure predicate owned by policy, like reap-exempt). It does not narrow by lifecycle: rows started by launch actually consume the substrate even if interactive. Both wave 1-S1 (adopt's 3 per-conversation branches — substrate reuse adds new rows) and wave 1-S2 (periodic registration — adopt rows to about 90 slots) push toward more adopted rows, and this law fixes their premise (registering observations does not mix into capacity)."
            :evidence "measurement by the wave 1-S1 seat (conversation ac0301fa-228a-41ad-ae89-2122aec051f8, 2026-08-17) / 32-row snapshot of agentd-herdr.sqlite / packages/doeff-agents/tests/sessionhost_launch_deftests.hy test-launch-capacity-ignores-adopted-rows, test-launch-capacity-counts-launch-owned-rows")]
       :counterexamples
         [(counterexample "the admission check counts all rows in the active state (including adopted), and registering observations eats up launch capacity (measured 2026-08-17: 32 ≥ 10, session.launch permanently rejected 100%)")
          (counterexample "keep the denominator and treat the symptom by only raising the value of max_running (adopted rows increase monotonically — it always recurs)")
          (counterexample "reintroduce reaping of interactive / adopted rows to free capacity (violates ADR-007 interactive-rows-are-never-reaped)")
          (counterexample "narrow the denominator by lifecycle (run_to_completion only) (interactive rows started by launch actually consume the substrate — narrow by ownership, not lifetime)")
          (counterexample "drop the leading verbatim 'max running agent sessions reached' from the rejection wording (a frozen surface consumed by the ACP Scheduler's infix match for throttle classification — releasing the seat and retrying)")])
     (law metered-billing-is-declared-by-kind-and-allowed-by-host-policy
       :statement "metered_kind_admitted <=> host.allow_metered_billing AND home_declares_metered; billing_class = f(binding.kind) never f(env) never f(host_flag_alone); credential_value not_in (host_memory + log + row + argv + spawn_env); resume_of_a_metered_row => metered_kind_or_typed_reject never_subscription_fallback"
       :facts
         [(fact
            "Starting point (2026-09-15): the recipient needed a path to run internal agents with their own metered credentials, but today's contract rejects *_API_KEY-shaped env on every path (policy.hy metered-credential-env-offenders, the substrate's hard reject). The 3 options — relax the rejection rule / add an env exception / merely document the practice of putting keys in the home — all break invariants (the home of authentication is the single point of the binding, there is no default authentication, do not bring metered billing into the system). Adopted form = declare the class by kind, permit it by a host flag, and check agreement with the home's contents before startup. The maintainer's deployment does not set the flag, so behavior is unchanged. ⚠ The in-home declaration is judged by 'the field is present and its content is non-empty' — judging by the field's presence would misclassify every codex flat-rate login home (OPENAI_API_KEY is null) as metered (measured: all three of the maintainer's homes)."
            :evidence "packages/doeff-agents/tests/sessionhost_launch_deftests.hy test-launch-rejects-metered-kind-when-host-forbids-metered-billing, test-launch-admits-metered-kind-when-host-allows, test-launch-rejects-metered-kind-without-home-declaration, test-launch-still-rejects-metered-credential-in-overlay-with-allow-flag / sessionhost_resume_deftests.hy test-resume-reconstructs-metered-kind-from-identity, test-resume-rejects-metered-kind-when-host-forbids-metered-billing, test-resume-refuses-to-downgrade-metered-row-to-subscription-kind / sessionhost_impls_deftests.hy test-claude-metered-pre-launch-requires-declared-credential-in-settings, test-codex-metered-pre-launch-requires-api-key-field-in-auth-file / sessionhost_host_deftests.hy test-parse-args-allow-metered-billing, test-dispatch-kinds-list")]
       :counterexamples
         [(counterexample "a flat-rate kind turns metered by the host flag alone (the flag only permits 'accepting a declared metered home' — the class is carried by the kind)")
          (counterexample "a host without the flag passes a metered launch based on the kind alone (the default is fail-closed — this would be the kind overriding the deployment's policy)")
          (counterexample "permit metered billing by a single env word (DOEFF_*_ALLOW_METERED_BILLING, etc.) (the same hole as R10(d) — an env declaration does not become a supervisory gate)")
          (counterexample "relax session_env's *_API_KEY rejection or the substrate's hard reject because the flag is set (env exclusion does not depend on the flag — key values never pass through the host)")
          (counterexample "judge the in-home declaration by the field's presence (codex writes OPENAI_API_KEY as null even for a flat-rate login, so all of the maintainer's homes become 'metered' and the deployment stops)")
          (counterexample "downgrade to a flat-rate kind when the kind cannot be reconstructed on resume of a metered row (the billing class silently changes at the moment of resumption, and even passes through admission on a host without the flag)")
          (counterexample "copy the key value into any of identity / row / log / argv / spawn env (only the home path and the class mark go through)")])
     (law herdr-session-identity-is-workspace-label
       :statement "herdr_session_identity(doeff_owned) => workspace_label_holders_as_a_set; agent_start_protocol_17 => start_managed_agent_in_existing_pane not_a_named_shell_pane_factory => never_called_by_TmuxNewSession(no_kind_value_is_right); agent_name_registry => existence_probe_only(has_session_disjunct, session_pane_ids_when_no_label_holder) never_identity_never_kill_never_duplicate_gate; externally_named_seats => observed_never_closed_by_name"
       :facts
         [(fact
            "agent.start in protocol 17 (herdr 0.7.5) is an API requiring {name, kind, pane_id} that 'has herdr itself start an agent in an existing pane and wait for detection' (bundled schema $defs.AgentStartParams, fired live at the running server 2026-07-29 / 08-17). Current main (the protocol 14 form {name,cwd,argv,env,workspace_id,focus}) fails with missing field `kind`, and even with kind added, missing field `pane_id`. The kind vocabulary is the type of agent herdr starts (claude/codex/…) and does not fit TmuxNewSession's semantics (create a named shell pane; doeff sends the startup command afterward). Keeping up = workspace.create {label, cwd, env} (accepts cwd/env directly, root pane = session pane, fired live). **Same in protocol 20 (herdr 0.8.2)** (measured 2026-09-15: AgentStartParams.required is still {name, kind, pane_id}; unmodified main fails with the same `missing field kind`, 5 failed / 6 passed; after switching the binding, the same suite has 18 passed)."
            :evidence "conformance/herdr-physics.md addenda (2026-07-29 / 08-14 / 08-17 / 09-15) / launch rejection wording `missing field kind at line 1 column 226` from the real-DB recovery check 2026-08-16 20:19 / socket live fire 2026-08-17 (nonexistent names, no side effects): A old payload → kind missing, B +kind → pane_id missing, C p17 form + nonexistent pane → agent_pane_not_found")
          (fact
            "An externally attached agent name tag is erased by herdr's detection within ~2 seconds when a real agent starts in the pane (n=4 in total). The workspace label remains. herdr does not enforce label uniqueness (a second one with the same label also succeeds), so duplicate rejection is on the doeff side. workspace_id is non-monotonic in creation order (w3NZ → w3N0)."
            :evidence "PR #569 review measurement 2026-08-14 (n=4) / PR #587 revision 2 (w3NZ → w3N0, w3ZZ → w303) / deftest test-herdr-identity-survives-agent-name-loss, test-herdr-new-session-verdict-ignores-id-order")
          (fact
            "A seat name that `ai` etc. attached in herdr's registry (koine session.adopt's session_name — dotfiles koine_adopt.py carries the name from `herdr agent list` as is) has no doeff-owned workspace, and the workspace label is the repo name (own seat w4XN:p9 = agent name s-7bfc5d5028 / label 'doeff'. In the interactive-seat host's ledger all 134 rows are adopted with session_name = the registry name). A label-only has-session rejects every adopt (adopt_target_not_found) and flips substrate_present of existing adopted rows to false all at once (an undetected regression in PR #587's first version — the conformance adopt fixture only created the doeff-owned form with label = name). The registry's agent.get also accepts a pane_id as AgentTarget, so it is narrowed by exact match on the response's name."
            :evidence "measured 2026-08-17 (cross-check of herdr agent list / pane list / workspace list, resolution of agent.get {target: 'w4XN:p9'}) / deftest test-herdr-external-seat-visible-to-has-session-but-not-killable, test-herdr-registry-agent-pane-id-requires-exact-name / conformance S29")]
       :counterexamples
         [(counterexample "'keep up' with protocol 17 by adding a single kind field to agent.start (rejected with missing field pane_id, and adding pane_id makes herdr start an agent on its own — a violation of TmuxNewSession's semantics)")
          (counterexample "use the pane.report_agent → agent.rename name tag as session identity (it disappears ~2 seconds after a real agent starts — liveness, attribution, and kill all fail)")
          (counterexample "answer has-session from label holders only (every externally named seat is 'absent' — all adopts rejected, substrate_present false across the board)")
          (counterexample "kill externally named seats by name / use them as grounds for the duplicate gate (closing seats doeff did not create, calling agent.get outside the waiver marker)")
          (counterexample "pick from the label holder set by index / decide precedence by id order (workspace_id is non-monotonic in creation order)")])]
  :enforcement
    ;; Made substrate-clean real enforcement in the same changeset as C1 (effect vocabulary + policy program).
    ;; The conformance suite gate went green in C0-2
    ;; (see the :facts of the conformance-before-cutover law).
    [(defsemgrep no-new-agent-physics-in-rust-agentd
       :languages ["generic"]
       :pattern "fn build_kimi_argv"
       :message "Adding a new CLI's protocol physics to the Rust agentd violates ADR-DOE-AGENTS-004 R2. Adding a kind = a doeff-agents defhandler module + conformance."
       :bad ["fn build_kimi_argv(params: &LaunchParams) -> Vec<String> {"]
       :good ["; write a defhandler in doeff-agents/impls/kimi.hy"])
     (defsemgrep no-auth-material-in-launch-effect-args
       :languages ["generic"]
       :pattern "LaunchAgent :auth-file"
       :message "Putting auth physics in the arguments of the public launch effect violates ADR-DOE-AGENTS-004 R9. Auth material is injected as the handler's bind-time configuration (local = main, host binding = the control plane's binding registry) and carried on the wire as a typed binding. session_env is for the non-auth overlay only."
       :bad ["(LaunchAgent :auth-file \"~/.codex/auth.json\" :prompt p)"]
       :good ["(LaunchAgent :kind \"codex\" :binding \"company\" :prompt p)"])
     ;; R2 substrate-clean: impls/ (where per-kind defhandlers live) only yields substrate
     ;; effects (SessionStore / Tmux / Fs / Env / Clock / Proc) — calling raw IO
     ;; (subprocess / sqlite3 / open / os.system) directly is
     ;; forbidden. The glob reserved `packages/doeff-agents/impls/` ahead of time in C1, and at
     ;; C2 implementation time it was concretized to the importable in-package path
     ;; `packages/doeff-agents/src/doeff_agents/_sessionhost/impls/`
     ;; (doeff-agents uses the hatchling src-layout — modules outside src are not included in the wheel
     ;; and cannot be imported). The installed rule is .semgrep.yaml's
     ;; doeff-agents-substrate-clean-impls.
     (defsemgrep substrate-clean
       "doeff-agents-substrate-clean-impls"
       [{"relative-path" "packages/doeff-agents/src/doeff_agents/_sessionhost/impls/kimi.hy"
         "source" ";; violation: a per-kind impl calls subprocess directly\n(import subprocess)\n(defn launch [argv] (subprocess.run argv))\n"}
        {"relative-path" "packages/doeff-agents/src/doeff_agents/_sessionhost/impls/opencode.hy"
         "source" ";; violation: a per-kind impl reads sqlite3 directly\n(import sqlite3 [connect])\n"}
        {"relative-path" "packages/doeff-agents/src/doeff_agents/_sessionhost/impls/awskind.hy"
         "source" ";; violation: a per-kind impl does file IO directly\n(defn read-home [path] (with [f (open path)] (.read f)))\n"}
        {"relative-path" "packages/doeff-agents/src/doeff_agents/_sessionhost/impls/geminikind.hy"
         "source" ";; violation: a per-kind impl calls the shell directly\n(defn kick [cmd] (os.system cmd))\n"}]
       [{"relative-path" "packages/doeff-agents/src/doeff_agents/_sessionhost/impls/cleankind.hy"
         "source" ";; a substrate-clean per-kind impl: it only yields substrate effects\n(defhandler cleankind-handler\n  (ClassifyPane [agent-type output]\n    (resume (classify-frame output)))\n  (DeliverMessage [pane-id text]\n    (<- _ (TmuxSendKeys :pane-id pane-id :text text :literal True :submit True))\n    (resume None)))\n"}
        {"relative-path" "packages/doeff-agents/src/doeff_agents/session_store_sub.py"
         "source" "# the substrate handler side (outside impls/) may hold raw IO\nimport sqlite3\nimport subprocess\n"}])
     ;; R7 revision (2026-07-30, issue #575 M0): prevent references to the retired crate from flowing back.
     ;; Only 2 forms are forbidden: the crate path packages/doeff-agentd and the module name doeff_agentd
     ;; — contract vocabulary (agentd_client / DOEFF_AGENTD_BIN / agentd.sqlite /
     ;; doeff-agentd-<user>.sock, etc.) is outside the scope of issue #575 and allowed. Prose (.md) and
     ;; ADR law documents (defadr_*.hy) are out of scope as historical description. The installed rule's
     ;; paths.exclude holds a migration allowlist that shrinks / is removed in M1 (conformance harness) / M2 (semgrep fixtures) /
     ;; M3 (re-anchoring porting provenance docstrings).
     (defsemgrep retired-crate-reference
       "doeff-agentd-retired-crate-reference-forbidden"
       [{"relative-path" "packages/doeff-agents/src/doeff_agents/agentd_revival.py"
         "source" "# backflow: cargo-building the retired crate\nimport subprocess\nsubprocess.run(['cargo', 'build'], cwd='packages/doeff-agentd')\n"}
        {"relative-path" "packages/doeff-agents/tests/test_retired_module_import.py"
         "source" "from doeff_agentd import serve\n"}]
       [{"relative-path" "packages/doeff-agents/src/doeff_agents/contract_vocab_ok.py"
         "source" "# agentd contract vocabulary is not subject to retirement (issue #575 scope is limited)\nfrom doeff_agents._agentd_client import ensure_agentd\nSOCKET_SUFFIX = 'doeff-agentd-user.sock'\nBIN_ENV = 'DOEFF_AGENTD_BIN'\nDB_NAME = 'agentd.sqlite'\n"}
        {"relative-path" "packages/doeff-agents/src/doeff_agents/provenance_ok.py"
         "source" "# porting provenance: agentd-rust-final:src/main.rs:2775 (rollback only; not the standard of correctness)\n"}
        {"relative-path" "docs/history-note.md"
         "source" "The Rust reference implementation used to live in packages/doeff-agentd (retired; tag agentd-rust-final).\n"}])
     (deftest test-adr-doe-agents-004-metered-billing-needs-kind-and-host-policy
       ;; Machine surface of law metered-billing-is-declared-by-kind-and-allowed-by-host-policy:
       ;; the class is a function of the kind (not of env, not of the flag), and the flag only moves
       ;; the permission side. The in-home declaration is judged by "non-empty" (not by the field's presence).
       (import json)
       (import doeff [run])
       (import doeff_agents._sessionhost.policy [
         BILLING-METERED
         BILLING-SUBSCRIPTION
         binding-billing-class
         claude-home-metered-reading
         codex-home-metered-reading])
       ;; The class is a function of the kind
       (assert (= (binding-billing-class {"kind" "claude-code" "config_dir" "/c"})
                  BILLING-SUBSCRIPTION))
       (assert (= (binding-billing-class {"kind" "codex" "codex_home" "/h"})
                  BILLING-SUBSCRIPTION))
       (assert (= (binding-billing-class {"kind" "claude-code-metered" "config_dir" "/c"})
                  BILLING-METERED))
       (assert (= (binding-billing-class {"kind" "codex-metered"
                                          "auth_file" "/a" "profile_dir" "/p"})
                  BILLING-METERED))
       ;; Things that do not claim a class (no binding, unknown kind) are None
       (assert (is (binding-billing-class None) None))
       (assert (is (binding-billing-class {"kind" "gemini"}) None))
       ;; In-home declaration: only non-empty counts as a declaration (codex's flat-rate login holds the field as null)
       (assert (is (get (run (codex-home-metered-reading
                              (json.dumps {"OPENAI_API_KEY" None "auth_mode" "chatgpt"}))) 1)
                   False))
       (assert (is (get (run (codex-home-metered-reading
                              (json.dumps {"OPENAI_API_KEY" "sk-x"}))) 1)
                   True))
       ;; What claude accepts as a metered kind is only apiKeyHelper or the Vertex pair
       (assert (= (get (run (claude-home-metered-reading
                             (json.dumps {"apiKeyHelper" "cat /k"}))) 2)
                  "apiKeyHelper"))
       (assert (is (get (run (claude-home-metered-reading
                              (json.dumps {"env" {"ANTHROPIC_API_KEY" "sk"}}))) 2)
                   None)))
     (deftest test-adr-doe-agents-004-reads-never-start-a-host
       ;; Machine surface of law reads-never-start-a-host: observation verbs do not touch ensure,
       ;; and an unreachable host ends with "no observation + guidance to the start command" (no
       ;; listener is created on the socket). The way to start is exactly the explicit flag.
       ;; This machine's resident socket is not touched — fire after naming an absent tmp
       ;; path via DOEFF_AGENTD_SOCKET. The way to start a host is exactly one, ensure_agentd on the client
       ;; side (the CLI only passes the start_host flag to the handler),
       ;; so that is what is replaced and watched.
       (import os)
       (import tempfile)
       (import click.testing [CliRunner])
       (import doeff_agents._cli :as cli-module)
       (import doeff_agents._agentd_client :as client-module)
       (import doeff_agents._agentd_client [AGENTD-SOCKET-ENV])
       (setv runner (CliRunner))
       (setv saved-ensure client-module.ensure-agentd)
       (setv saved-env (.get os.environ AGENTD-SOCKET-ENV))
       (defn forbidden [#* args #** kwargs]
         (raise (AssertionError "an observation verb tried to start a host")))
       (setv observation-argvs
             [["ps"]
              ["watch" "s-1"]
              ["output" "s-1"]
              ["agentd" "by-conversation" "--conversation-id" "c-1"]
              ["agentd" "kinds"]])
       (try
         (with [tmp (tempfile.TemporaryDirectory)]
           (setv socket-path (os.path.join tmp "absent.sock"))
           (setv (get os.environ AGENTD-SOCKET-ENV) socket-path)
           (setattr client-module "ensure_agentd" forbidden)
           (for [argv observation-argvs]
             (setv result (.invoke runner cli-module.cli argv))
             (assert (= result.exit-code 1) f"{argv}: exit code {result.exit-code}")
             ;; no observation = ends with guidance to the start command (does not silently return 0)
             (assert (in "doeff_agents._sessionhost.host" result.output) f"{argv}: {result.output}")
             ;; no listener was created (observation does not give birth to a host)
             (assert (not (os.path.exists socket-path))
                     f"{argv} left a socket behind"))
           ;; The way to start is only the explicit flag — ensure is reached only when the flag is given
           (setv calls [])
           (defn fake-ensure [#** kwargs]
             (.append calls "ensure")
             (raise (SystemExit 9)))
           (setattr client-module "ensure_agentd" fake-ensure)
           (setv flagged (.invoke runner cli-module.cli ["ps" "--ensure"]))
           (assert (= calls ["ensure"]) f"--ensure did not reach ensure: {calls}")
           (assert (= flagged.exit-code 9) f"exit {flagged.exit-code}"))
         (finally
           (setattr client-module "ensure_agentd" saved-ensure)
           (if (is saved-env None)
               (.pop os.environ AGENTD-SOCKET-ENV None)
               (setv (get os.environ AGENTD-SOCKET-ENV) saved-env)))))
     (deftest test-adr-doe-agents-004-capacity-denominator-is-launch-owned
       ;; Machine surface of capacity-counts-only-launch-owned-rows: the denominator predicate
       ;; narrows by ownership (adopted), not by lifetime (lifecycle).
       ;; adopt = observation — not counted toward capacity regardless of lifecycle.
       ;; launch-owned — counted, because it consumes the substrate even if interactive.
       (import doeff_agents [SessionRow])
       (import doeff_agents._sessionhost.policy [counts-toward-launch-capacity])
       (defn mk [lifecycle adopted]
         (SessionRow :session-id "adr4cap" :session-name "adr4cap" :pane-id "%0"
                     :agent-type "claude" :lifecycle lifecycle :status "running"
                     :started-at "2026-08-17T00:00:00+00:00" :adopted adopted))
       (assert (is (counts-toward-launch-capacity (mk "interactive" True)) False))
       (assert (is (counts-toward-launch-capacity (mk "run_to_completion" True)) False))
       (assert (is (counts-toward-launch-capacity (mk "interactive" False)) True))
       (assert (is (counts-toward-launch-capacity (mk "run_to_completion" False)) True)))
     ;; R12 / law herdr-session-identity-is-workspace-label: installed rule
     ;; doeff-agents-herdr-session-identity-not-agent-name (.semgrep.yaml)
     ;; hit / clean snapshots are pinned on the ADR side as well. hit = the shape that resolves identity and kill
     ;; via the registry (the old form of PR #569) / agent.get without the waiver marker. clean =
     ;; label resolution + the single existence-check probe directly under the waiver marker.
     (defsemgrep herdr-session-identity-not-agent-name
       "doeff-agents-herdr-session-identity-not-agent-name"
       [{"relative-path" "packages/doeff-agents/src/doeff_agents/_sessionhost/substrate_herdr_name_identity_bad.hy"
         "source" ";; ⚠ The vocabulary choice, deff or defk, is not what this specimen is about — the rule's pattern is a regex on agent.get and does not depend on how the definition is spelled. Some functions in real code remain deff, but the ADR-DOE-HY-004 ledger also counts literal text inside strings, so the specimen is written with defk.\n;; the old form that resolves session identity and kill via the name tag (agent registry) (PR #569)\n(defk herdr-agent-pane-id-io [socket-path session-name]\n  (get (get (herdr-call socket-path \"agent.get\" {\"target\" session-name}) \"agent\") \"pane_id\"))\n(defk herdr-kill-session-io [socket-path session-name]\n  (herdr-call socket-path \"pane.close\" {\"pane_id\" (herdr-agent-pane-id-io socket-path session-name)}))\n"}
        {"relative-path" "packages/doeff-agents/conformance/harness_name_identity_bad.py"
         "source" "def session_exists_out_of_band(session_id: str) -> bool:\n    return 'error' not in _herdr_call(\"agent.get\", {\"target\": session_id})\n"}]
       [{"relative-path" "packages/doeff-agents/src/doeff_agents/_sessionhost/substrate_herdr_label_ok.hy"
         "source" ";; ⚠ The vocabulary choice, deff or defk, is not what this specimen is about — the rule's pattern is a regex on agent.get and does not depend on how the definition is spelled. Some functions in real code remain deff, but the ADR-DOE-HY-004 ledger also counts literal text inside strings, so the specimen is written with defk.\n;; identity is the workspace label (a set) — kill closes every holder\n(defk herdr-kill-session-io [socket-path session-name]\n  (for [ws-id (herdr-label-workspace-ids-io socket-path session-name)]\n    (herdr-call socket-path \"workspace.close\" {\"workspace_id\" ws-id})))\n;; the single existence-check probe (directly under the waiver marker)\n(defk herdr-external-agent-pane-id-io [socket-path name]\n  (try\n    ;; registry-existence-probe: allowed only for checking the existence of externally named seats (identity, attribution, and kill use the label)\n    (setv result (herdr-call socket-path \"agent.get\" {\"target\" name}))\n    (except [e HerdrApiError]\n      (when (= e.code \"agent_not_found\") (return None))\n      (raise)))\n  (herdr-registry-agent-pane-id result name))\n"}])
     (deftest test-adr-doe-agents-004-herdr-identity-pure-rules
       ;; Pure-function surface of law herdr-session-identity-is-workspace-label (no real herdr
       ;; needed): (a) the registry existence check accepts only an exact match on the response's name (it does not mistake a pane_id
       ;; target for "the seat with that name"). (b) The duplicate decision does not depend on id order
       ;; — if the pre-query has a holder, it is duplicate regardless of order;
       ;; if self is absent after creation, vanished. (c) Label holders are a set (in listing order).
       (import doeff_agents._sessionhost.substrate_herdr [
         herdr-registry-agent-pane-id herdr-new-session-verdict herdr-label-holders])
       (setv reply {"agent" {"name" "s-7bfc5d5028" "pane_id" "w4XN:p9"}})
       (assert (= (herdr-registry-agent-pane-id reply "s-7bfc5d5028") "w4XN:p9"))
       (assert (is (herdr-registry-agent-pane-id reply "w4XN:p9") None))
       (assert (is (herdr-registry-agent-pane-id reply "coupling-core-review") None))
       ;; earlier w3NZ, later w3N0 (under shortlex the later one comes first) — duplicate in either order.
       (assert (= (herdr-new-session-verdict "w3N0" ["w3NZ"] ["w3NZ" "w3N0"]) "duplicate"))
       (assert (= (herdr-new-session-verdict "w3N0" ["w3NZ"] ["w3N0" "w3NZ"]) "duplicate"))
       (assert (= (herdr-new-session-verdict "w3N0" [] []) "vanished"))
       (assert (= (herdr-new-session-verdict "w3N0" [] ["w3N0"]) "ok"))
       (assert (= (herdr-label-holders
                    {"workspaces" [{"workspace_id" "w1" "label" "x"}
                                   {"workspace_id" "w2" "label" "y"}
                                   {"workspace_id" "w3" "label" "x"}]}
                    "x")
                  ["w1" "w3"])))]
  :plans ["master plan on the ../agent-control-plane side: docs/acp-2026-07-05-agentd-hy-session-host-plan.md"
          "doeff issue #575 — agentd retirement milestones (M0 law revision + backflow prevention / M1 conformance default flip / M2 test rebinding / M3 crate deletion + rollback tag / M4 full verification)"])
