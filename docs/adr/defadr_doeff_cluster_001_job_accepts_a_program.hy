;;; Executable ADR: a doeff-cluster job accepts exactly one Program value — handlers are given by with-handlers inside the
;;; Program, and the executor (job_entry) adds not a single default handler. Services and tasks share the same entry, and
;;; settings are read by Ask inside the Program and a handler that answers it by reading os.environ.
;;;
;;; What was decided is written in the facts of :problem.
;;;
;;; Revision 2026-09-27: R5's recorder was rewritten from 'undecided' to a decision — recording and replay are handlers placed in
;;; the Program's with-handlers, and the runner does not insert them (what was decided is in the :problem facts). The same day R5b
;;; was added — handlers inside the recorder must be deterministic (effects that do not reach the recorder are re-answered by the
;;; same computation on replay). The same day R3b was added — a Program value in a declaration is carried, as for tasks, as a packed
;;; string (encode-program / decode-program). The same day R4b was added — a Program's declaration writes only the names of the
;;; capabilities it needs (:needs), never placement names (kind=k3s, role=agent-exp, machine names).
;;; The same day R7 was added — there is no migration period accepting old declarations; old forms are refused at declaration time with a reason.
;;;
;;; Rollback: revert the commit that added this ADR (the one ADR file and the enforcement ledger count disappear). To roll back only
;;; the R5 revision, revert that revision's commit (R5 returns to the 'undecided' text and law runner-inserts-no-recorder disappears).

(require doeff-adr.macros [defadr rule law])
(import doeff-adr.macros [fact interpretation counterexample])
(require doeff-hy.macros [deftest defk <- val])
(import doeff [run])
(import re)
(import pathlib [Path])


;; ---------------------------------------------------------------------------
;; Ratchet ledger — the number of calls in job_entry.hy as of 2026-09-27 through which the executor adds handlers from
;; outside the Program (measured on origin/main faf81b97). The end state is all 0 (R2). The check enforces 'current count <= ledger
;; count', and a change that reduces a count trims the ledger in the same change (forgetting to trim is also red).
;;   scheduled       — the runner wraps with the scheduler's handler (one each for service and task)
;;   env-handlers    — the runner wraps with the handler set built by the env function (an import path) (one each for service and task)
;;   recording-layer — the runner adds the effect recorder (service only; by the R5 decision moved to a handler inside the Program, bringing it to 0)
;; ---------------------------------------------------------------------------

(val RUNNER-HANDLER-ROSTER
  {"scheduled" 0
   "env-handlers" 0
   "recording-layer" 0})  ; all 0 on 2026-09-27 (job_entry is just (run program))

(val JOB-ENTRY "packages/doeff-cluster/src/doeff_cluster/_worker/entry/job_entry.hy")

(defk count-runner-handler-sites [text]
  {:pre [(: text str)]
   :post [(: % dict)]
   :tags {:context "doeff-cluster-adr" :role "judgment"}}
  "Count, per kind, the calls in the body of job_entry through which the runner adds handlers from outside the Program (the shared measure of the check and the ledger)."
  (dfor name RUNNER-HANDLER-ROSTER
        name (len (re.findall (+ r"\(" (re.escape name) r"\s") text))))


(defadr ADR-DOE-CLUSTER-001
  :title "A doeff-cluster job (services and tasks use the same API) accepts exactly one Program value (the result of calling a defk function). Handlers are given by with-handlers inside the Program, and the executor job_entry adds not a single default handler (the scheduled and env-function wrappers are removed too). The service :env, :config and :env-config are dropped; settings are read by Ask inside the Program and a handler reading os.environ. Environment variables the declaration passes to the process are kept as values of the declaration"
  :status "accepted"
  :scope ["packages/doeff-cluster/src/doeff_cluster/_worker/entry/job_entry.hy"
          "packages/doeff-cluster/src/doeff_cluster/_shared/intent/service_model.hy"
          "packages/doeff-cluster/src/doeff_cluster/_shared/core/service_rules.hy"
          "packages/doeff-cluster/src/doeff_cluster/_shared/entry/service_build.hy"
          "packages/doeff-cluster/src/doeff_cluster/_shared/intent/remote_model.hy"
          "docs/adr/defadr_doeff_cluster_001_job_accepts_a_program.hy"]
  :problem
    [(fact
       "Handlers and env are not passed as a job. What a job accepts is a Program, not a pair of a Program and handlers — because a doeff handler is a dynamically scoped expression. The doeff job executor has no default handlers. There is no reason for services alone to have a separate API; if settings are needed they can be read with the Ask effect and a handler that reads os.environ.")
     (fact
       "Today job_entry has different entries for services and tasks: a service takes --factory (an import path of a function), --config (the body's arguments) and --env (an import path of a function that builds the handler set); a task takes --blob (a Program value) and --env. In both, the runner wraps with (scheduled (with-handlers <env handlers> program)), and for services it also adds the recorder (recording-layer)."
       :evidence "packages/doeff-cluster/src/doeff_cluster/_worker/entry/job_entry.hy (run-service, task-outcome, env-handlers, recording-layer)")
     (fact
       "A service declaration has :env (an import path of the env function), :config (the body's arguments) and :env-config (settings only env reads), and the coordinator receives a flat run.config merging the two."
       :evidence "packages/doeff-cluster/src/doeff_cluster/_shared/intent/service_model.hy (ServiceDef env, config, env-config)")
     (fact
       "Recorder: the question was whether the recorder can be treated as a handler matter rather than VM instrumentation (whether algebraic effect handlers in general support that use); it was decided that if they do, the recorder is built as a handler.")
     (fact
       "Today the recorder is added by job_entry's recording-layer, which looks at the record field of run.config and adds it innermost among the env handlers (recording-handler, record_handlers.hy). The recorder has the shape of an 'in-between handler' that re-raises the effect outward, writes down the answer and resumes the continuation."
       :evidence "packages/doeff-cluster/src/doeff_cluster/_worker/entry/job_entry.hy (recording-layer), packages/doeff-cluster/src/doeff_cluster/_foundation/record_handlers.hy")
     (fact
       "Remaining question: the recorder cannot know about effects answered before they reach it. How is this usually solved in the handler form (answer: R5b)?")
     (fact
       "A task carries its Program value as a string packed by encode-program (the --blob file), and the destination unpacks it with decode-program. A service is carried as a function reference (module:attr) and the JSON of the body's arguments (:config), and the destination calls the function to build the Program — two ways of carrying for the same job."
       :evidence "packages/doeff-cluster/src/doeff_cluster/_shared/intent/remote_model.hy (encode-program, decode-program), job_entry.hy (run-service, task-outcome)")
     (fact
       "Placement: a placement spec such as kind=k3s is not written in a declaration. A Program may declare what it needs, but what it declares is the capabilities it needs, not where the executor is placed.")
     (fact
       "Today's placement spec is :requires (key-value pairs), which the coordinator compares one by one for equality with worker labels (labels-satisfy). The values written are placement names: doeff-cluster's examples and checks use {\"kind\" \"k3s\"}, agora uses {\"role\" \"agent-exp\"} (the mark of the experimental namespace)."
       :evidence "packages/doeff-cluster/src/doeff_cluster/_coordinator/core/cluster_policy.hy (labels-satisfy), the header comment of service_model.hy (:requires {\"kind\" \"k3s\"}), packages/doeff-cluster/tests/test_detached.hy, agora-controllers controllers/runtime_env/declare.hy and controllers/agora_sim/tests/test_emulated_runtime_env_sender.hy (Requirement \"role\" \"agent-exp\")")
     (fact
       "Migration: old declarations are not accepted at all. If old and new coexist, the declarations as a whole become confusing.")
     (fact
       "When a replay disagrees with the record, today's replay handler raises ReplayDiverged and stops (replay divergence)."
       :evidence "packages/doeff-cluster/src/doeff_cluster/_foundation/record_log.hy (ReplayDiverged), record_handlers.hy, replay_main.hy")]
  :context
    [(interpretation
       "A doeff handler is a dynamically scoped expression; if a 'handler set' is held as a separate value outside the Program, the same Program means different things on different executors. If handlers are placed in with-handlers inside the Program, a job's meaning is decided by the Program value alone, and a local run, the simulation and the cluster run the same value with the same meaning.")
     (interpretation
       "The runner's default handlers (the scheduler, the env set) are the executor implicitly adding what the Program should list itself, and which ones are in effect cannot be read from the Program. With zero defaults, a missing handler surfaces as an unhandled effect inside the Program.")
     (interpretation
       "Passing settings through declaration fields (:config, :env-config) adds a dedicated settings channel to the job API. Reading settings can be written with the Ask effect and a handler answering it by reading os.environ, so there is no reason for services alone to have a separate API. The environment variables passed to the process themselves stay as values of the declaration (which process gets what).")
     (interpretation
       "Effects answered inside the recorder (on the Program side) never reach it. Replay still works only when the inner handlers are deterministic — because they can reproduce the same answers from the same inputs (the answers the recorder recorded at the boundary) by the same computation. This is the same idea as Temporal's 'deterministic workflow + recorded activity results' and rr's 'record only nondeterministic input': if all nondeterminism (clock, randomness, I/O) is pushed outside the boundary (to the foundation handlers), recording at the boundary alone is enough to replay the whole.")]
  :decision
    [(rule R1 "A doeff-cluster job accepts exactly one Program value (the result of calling a defk function). It does not accept a Program and a set of handlers separately. Handlers are given by with-handlers inside the Program.")
     (rule R2 "The executor job_entry adds not a single default handler. Today's scheduled wrapper and env-function (--env) wrapper are removed too.")
     (rule R3 "Services and tasks are started through the same API (one Program value). The service-only --factory and --config entries are dropped.")
     (rule R3b "A Program value in a declaration is carried, as for tasks, as a packed string (encode-program / decode-program). Services and tasks do not differ in how they are carried. Readability is supplied by declare's display — it unpacks the packed Program and prints the called function's name and arguments. Handler values are not put in the declaration; they are built inside the defk body (with-handlers). Rollback: go back to holding a function reference (module:attr) + argument JSON in the declaration, with the runner calling it on the spot to build the Program (added 2026-09-27).")
     (rule R4 "The service declaration's :env, :config and :env-config are dropped. Settings are read by Ask inside the Program and a handler answering it by reading os.environ (listed on the Program side). Environment variables the declaration passes to the process are kept as values of the declaration.")
     (rule R4b "A Program's declaration writes only the names of the capabilities it needs (:needs #{\"claude-cli\" \"pg-network\"} and so on), never placement names (kind=k3s, role=agent-exp, machine names). The capabilities a worker provides are declared in the cluster configuration (outside the code), and the coordinator places a job on a worker with needed ⊆ provided. Foundation handlers also declare the capabilities they need with :needs, and if those are not included in the :needs of the Program's declaration it is a doeff-linter violation. A foundation that uses company credentials declares \"company-machine\", and only workers on company machines provide it — the placement mechanism guarantees the company credential boundary. Today's :requires (equality matching against worker labels) is replaced by :needs. Rollback: return :needs to :requires (key-value pairs) and to labels-satisfy matching (added 2026-09-27).")
     (rule R7 "There is no migration period accepting old declarations (service :env, :config, :env-config, :requires, env functions, declare's --config). Only the new API (one Program value, :needs) remains, and old forms are refused at declaration time with a reason (not silently reinterpreted). The user (agora-controllers) is rewritten in the same switch. Reason: if old and new forms both pass, neither readers nor the linter can tell which is the correct way to write it, or which declaration runs with which meaning. Rollback: add back the reading of the old fields and let old and new coexist (revert the commit that added this rule — old declarations are no longer refused, but the new API's implementation was a separate change and stays) (added 2026-09-27).")
     (rule R5 "Recording and replay are done by handlers (decided 2026-09-27 — replaces the old text 'undecided: option A = wrap on the Program side / option B = an observation hook of the executor'). The form is an in-between handler like today's effect-recorder: recording re-raises the effect outward, writes down the answer and resumes the continuation. Replay answers from the record at the same place. It is placed in with-handlers inside the Program, between the translation handlers and the foundation handlers (the boundary with the outside world — only generic effects are recorded). Recording versus replay is chosen by which handler is placed, and which one to place is decided by Ask and a handler reading os.environ. The runner does not insert the recorder (today's recording-layer and the record field of run.config are dropped). WithObserve (only watches, does not see answers) is not used for this; it is limited to tracing and logging.")
     (rule R5b "Handlers inside the recorder (on the Program side — business handlers and translation handlers) must be deterministic. They do not read the clock, randomness or I/O themselves; they make these generic effects answered by the foundation handlers below the recorder. Then effects that do not reach the recorder are re-answered by the same computation on replay, and recording at the boundary alone is enough for replay. The guard is doeff-linter's DOEFF106 (definitions that touch raw side effects directly are placed only in the foundation layer), and a breach shows as replay divergence (ReplayDiverged). The order of the scheduler's concurrency (which task advances first) is not deterministic on replay, so it is handled as live (order matching) (added 2026-09-27).")
     (rule R6 "During migration, the handler calls the runner adds do not exceed the counts in RUNNER-HANDLER-ROSTER (new ones are red). A change that reduces them trims the ledger in the same change.")
     (rule R8 "sim-cluster, a local single-node cluster and the k3s cluster are tested through the same API (same entry, same declarations, same Program). Differences in placement are absorbed by handlers (the foundation handler set), not switched by command arguments or environment-name branches (added 2026-10-02 — so that testing locally and on the cluster does not differ, the difference is absorbed by handlers, not command arguments). The same decision as dotfiles ADR-DOTFILES-027 R-ca36606c — the command-side advice is carried by that rule. A testing-side decision in the same direction as R4b (no placement names in declarations). Addendum (2026-10-03, a reversible decision, an application of this one): (1) tests' 'declare, wait until ready, take down' is held as doeff-cluster contract effects, not as test-only tools. (2) The deployment foundation is chosen by a 'type → foundation' table, not by the --foundation argument. (3) Destructive tests (take down, cut the network) are not answered by the k3s handler; they are tested on sim-cluster and the local single-node cluster. The implementation for sim and the local single node is milestone L (no arguments added, arguments reduced; old arguments are removed once the users' re-wiring is on main), and the k3s handler part is milestone O (not shipped). Rollback = to go back to entries split by arguments, revert the implementation change. The situational advice is carried by the dotfiles rules cluster-kind-is-chosen-by-handler-table-not-cli-arg and breaking-tests-stay-off-k3s. Addendum (2026-11-08, a reversible decision, an application of this one): (4) A worker of the local single-node cluster measures its machine room (CPU ticks, memory, PSI) with the same reading as a deployed worker, but a LocalMachine may hand it a quiet stand-in of the machine (QuietMachine — a directory laid out like /proc whose CPU ticks advance as idle and whose PSI is 0, passed through DOEFF_WORKER_LOAD_DIR). Scenarios that do not test placement use it, so that their outcome does not depend on how busy the machine running the tests is: placement follows the measured room, the coordinator places no task on a worker whose machine CPU PSI some is at or above the stop line, and the version-swap test stayed red while the shared test machine sat at 63-69%. The coordinator's placement judgment gets no test-only branch; only the files the worker reads change. Rollback = drop the LocalMachine quiet field and the DOEFF_WORKER_LOAD_DIR reading.")]
  :laws
    [(law job-entry-adds-no-handler
       :statement "for_all job j (service / task): the number of handlers job_entry adds when running j = 0 — no handler takes effect outside j's Program"
       :counterexamples
         [(counterexample "job_entry wraps with (scheduled (with-handlers (env-handlers …) program)) — the scheduler and env handlers take effect outside the Program (the mainline form as of 2026-09-27)")
          (counterexample "The runner adds a clock or a recorder 'by default' — reading the Program does not tell which handlers are in effect")]
       :enforced-by ["test-adr-doe-cluster-001-runner-handler-ratchet"]
       :wiring "Wired (2026-09-27) — every ledger count is 0, and the check turns red if job_entry writes even one scheduled, env-function or recorder wrapper")
     (law service-and-task-share-one-entry
       :statement "for_all job j: the argument of j's entry is one Program value — the entry has the same form for services and tasks"
       :counterexamples
         [(counterexample "A service is started with --factory and --config and a task with --blob — two entries for the same job (the mainline form as of 2026-09-27)")
          (counterexample "Passing settings by writing :env-config in a service declaration — settings can be read by Ask inside the Program and a handler reading os.environ")]
       :enforced-by ["test-adr-doe-cluster-001-one-entry-takes-a-program"]
       :wiring "Wired (2026-09-27) — the check verifies that job_entry's service, task and probe entries do not accept --factory, --env or --config")
     (law runner-inserts-no-recorder
       :statement "for_all job j: the handlers recording and replaying j's effects are in with-handlers inside j's Program (between the translation handlers and the foundation handlers), and the runner (job_entry) does not insert a recorder — recording-layer in RUNNER-HANDLER-ROSTER is 0"
       :counterexamples
         [(counterexample "job_entry looks at the record field of run.config and adds recording-handler innermost among the env handlers (the mainline form as of 2026-09-27) — whether recording happens cannot be read from the Program")
          (counterexample "Using WithObserve for recording — it only watches and does not see answers, so the answers replay needs are not kept (WithObserve is limited to tracing and logging)")
          (counterexample "Placing the recorder outside the foundation handlers — it records business effects before translation, not only the generic effects at the boundary with the outside world")]
       :enforced-by ["test-adr-doe-cluster-001-runner-handler-ratchet"]
       :wiring "Wired (2026-09-27) — recording-layer in the ledger is 0, and the check turns red if job_entry adds a recorder. Placing the recorder inside the Program (boundary-recorder) is the next step")
     (law programs-declare-capabilities-not-places
       :statement "for_all job j: the :needs of j's declaration is a set of capability names and contains no placement names (kind, role, machine names) ∧ for the worker w where j is placed, needs(j) ⊆ provides(w) ∧ for_all foundation handler f listed by j's Program: needs(f) ⊆ needs(j) ∧ (\"company-machine\" ∈ provides(w) ⇔ w is a company machine)"
       :counterexamples
         [(counterexample "Writing :requires {\"kind\" \"k3s\"} in a service declaration — it names a placement rather than what the Program needs, and cannot move to another placement with the same capabilities (the mainline form as of 2026-09-27)")
          (counterexample "Writing role=agent-exp in an agora declaration to choose the experimental namespace — the way of choosing placement enters the code")
          (counterexample "A Program listing a foundation handler that reads company credentials does not write company-machine in :needs — it gets placed on a worker on a non-company machine and the company credential boundary is breached (a linter violation)")]
       :enforced-by ["the coordinator's placement cluster_policy.placeable (packages/doeff-cluster/tests/test_cluster_policy.hy)"
                     "declare's check of foundation :needs, service_rules.foundation-needs-refusal (packages/doeff-cluster/tests/test_service_declaration.hy)"
                     "doeff-linter (matching the :needs of the handlers a foundation lists — rule number not yet assigned)"]
       :wiring "Partly wired (2026-09-28) — the :needs declarations (defk, defhandler, defsystem), the coordinator's needs ⊆ provides ∪ derived placement, deriving company-machine from the node label rather than the worker's self-report, and declare's foundation :needs ⊆ job :needs have implementations and checks. Matching the :needs of the handlers a foundation lists (linter) is not wired")
     (law old-declarations-are-refused
       :statement "for_all declaration d: d uses an old field (:env, :config, :env-config, :requires), an env function, or declare's --config ⇒ it is refused at declaration time with a reason text (it is neither reinterpreted into the new API nor passed with just a warning)"
       :counterexamples
         [(counterexample "Reinterpreting :requires as :needs and passing it during migration only — old and new declarations coexist in one system, and the declaration does not tell with which meaning it was placed")
          (counterexample "Accepting the old :env and only emitting a warning — warnings are not read and the old form lives on")
          (counterexample "Switching doeff-cluster first and leaving the agora-controllers rewrite to a later change — the mainline user is left with broken declarations (done in the same switch)")]
       :enforced-by ["packages/doeff-cluster/tests/test_old_declarations.hy and others — counterexamples per entry that refuses old forms (declaration constructors, defsystem, the declare CLI, the resource API, PUT /jobs, re-reading the store, heartbeat, worker startup, task body, effect constructors, job_entry, replay_main, probe)"]
       :wiring "Wired (2026-09-28) — every doeff-cluster entry that refuses old forms has a counterexample check. agora-controllers' declaration CLI entry refuses them likewise through its rewrite")
     (law handlers-inside-the-recorder-are-deterministic
       :statement "for_all handler h inside the recorder: h does not read the clock, randomness or I/O directly (nondeterministic input is answered, as generic effects, by the foundation handlers below the recorder) — hence for_all record r: the computation replaying r gives, even for effects that do not reach the recorder, the same answers as at recording time and does not raise ReplayDiverged (the order of scheduler concurrency is outside, matched by the live handling)"
       :counterexamples
         [(counterexample "A translation handler reads time.time() directly to build its answer — it does not reach the recorder, so it is not recorded, and the replay gets a different time and diverges")
          (counterexample "A business handler draws with random — the replay gets a different value and the subsequent effect sequence disagrees with the record (ReplayDiverged)")
          (counterexample "A Program-side handler reads a file directly — if the file's contents at replay time differ from recording time, the answer changes")]
       :enforced-by ["doeff-linter DOEFF106"]
       :wiring "Not wired (2026-09-27) — DOEFF106 has not landed in doeff-linter (wt/hy-lint-visibility and others). A breach shows at runtime as replay divergence (ReplayDiverged), but that is after-the-fact detection, not a check")
     (law every-cluster-is-tested-through-one-api
       :statement "for_all test placement c ∈ {sim-cluster, local single-node cluster, k3s cluster}: the entry, declaration and Program used to test a job on c are the same, and the differences of c are absorbed only by the foundation handler set — code paths are not switched by entry arguments or environment names (R8)"
       :counterexamples
         [(counterexample "Switching job_entry's path with a --cluster k3s argument only when testing on k3s — the placement difference enters an entry branch instead of a handler")
          (counterexample "Making a separate command entry for the local single-node cluster — the same Program is tested through a different API per placement")
          (counterexample "Skipping the coordinator for sim-cluster only and running the Program directly — local tests do not go through the production entry (a form with a real occurrence)")]
       :enforced-by ["the dotfiles advice develop-locally-never-wait-for-deployment (ADR-DOTFILES-027 R-ca36606c)"]
       :wiring "Not wired (2026-10-02) — there is no machine check (a check measuring whether the test entries are split per placement is not designed). For now there is only advice at writing time")]
  :enforcement
    [(deftest test-adr-doe-cluster-001-runner-handler-ratchet
       ;; Check: calls through which job_entry adds handlers from outside the Program do not exceed the ledger (new ones are red); forgetting to trim the ledger is also red.
       (val repo-root (. (Path __file__) parent parent parent))
       (val text (.read-text (/ repo-root JOB-ENTRY) :encoding "utf-8"))
       (val counts (run (count-runner-handler-sites text)))
       (val grown (lfor [name n] (sorted (.items counts))
                         :if (> n (get RUNNER-HANDLER-ROSTER name))
                         f"{name}: {n} > ledger {(get RUNNER-HANDLER-ROSTER name)}"))
       (assert (= grown [])
               (+ "job_entry added more handlers from outside the Program (ADR-DOE-CLUSTER-001 R2, R6 — "
                  "handlers are given by with-handlers inside the Program): " (str grown)))
       (val stale (lfor [name n] (sorted (.items counts))
                         :if (< n (get RUNNER-HANDLER-ROSTER name))
                         f"{name}: actual {n} < ledger {(get RUNNER-HANDLER-ROSTER name)}"))
       (assert (= stale [])
               (+ "Ledger not trimmed (ADR-DOE-CLUSTER-001 R6 — a change that reduces a count trims RUNNER-HANDLER-ROSTER in the same change): "
                  (str stale))))
     (deftest test-adr-doe-cluster-001-one-entry-takes-a-program
       ;; Check: job_entry's entry accepts only one Program value — red if an old entry argument (--factory, --env, --config) is in the body (R1, R3).
       (val repo-root (. (Path __file__) parent parent parent))
       (val text (.read-text (/ repo-root JOB-ENTRY) :encoding "utf-8"))
       (val old (lfor flag ["--factory" "--env" "--config"]
                      :if (re.search (+ r"add-argument\s+\w+\s+\"" (re.escape flag) "\"") text)
                      flag))
       (assert (= old []) (+ "job_entry accepts old entry arguments (ADR-DOE-CLUSTER-001 R1, R3): " (str old))))
     (deftest test-adr-doe-cluster-001-ratchet-measure
       ;; Demonstration of the measure: wrapper calls are counted; a definition with the same name or a different name is not.
       (val sample (+ "(defn env-handlers [env config ctx] None)\n"
                       "(run (scheduled (with-handlers (env-handlers e {} ctx) program)))\n"
                       "(recording-layers x)\n"))
       (assert (= (run (count-runner-handler-sites sample))
                  {"scheduled" 1 "env-handlers" 1 "recording-layer" 0})))]
  :plans [])
