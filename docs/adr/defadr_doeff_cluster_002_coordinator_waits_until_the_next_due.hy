;;; Executable ADR: while there are no requests, the doeff-cluster coordinator waits in one wait until the instant the answer of a judgment next changes, a request, a stop,
;;; or an outside event — it does not wake every second to look. Judgments come out at exactly the due instant (not rounded to a 1-second grid).
;;;
;;; Origin = the 2026-10-06 rule (what was decided is the :problem fact), which the Mac coordinator on 2026-10-07 read as the answer to having production also sleep until the due
;;; (the :problem records that this is an interpretation).
;;;
;;; Stages: unit 1 (the due answer type, rewriting now + 1, the pure judgment of the next wake instant and the promise of settling) is judgment only and does not change how waiting works.
;;; Unit 2 wired in the reconcile loop (run-coordinator in core/program.hy), and in the same change removed the 1-second period setting (TICK-MS) and the code that wakes on it (the quiet
;;; interval on the simulated grid — idle_policy) (unit 2b). Heartbeats while a worker stand-in sleeps are handed over as requests at that instant by the simulated intake queue.
;;;
;;; Rollback: revert this ADR and the unit 2 commits (production goes back to a 1-second beat, the simulation back to quiet intervals on the grid). The unit 1
;;; judgment (the due answer type) does not change how waiting works, so it may stay.
;;;
;;; Addendum R8 (2026-10-16): detect the coordinator's own stall by comparing against the scheduled wake instant (R1), and do not count the stalled length
;;; as silence (the 3-minute stall of node-a on 2026-10-13 — option 3). Rollback is reverting the R8 commit.

(require doeff-adr.macros [defadr rule law])
(import doeff-adr.macros [fact interpretation counterexample])
(require doeff-hy.macros [deftest val])
(import doeff [run])
(import pathlib [Path])
(import doeff_hy._table [TableWrite table-of])
(import doeff_cluster [ClusterTiming])
(import doeff_cluster [ClusterState ClusterNaming BoardRow])
(import doeff_cluster [DueNow DueNever])
(import doeff_cluster [liveness-due task-due sweep-due])
(import doeff_cluster [tick-due])
(import doeff_cluster [next-wake])


;; ratchet ledger — the number of places in the due-function files that write the numeric (+ now 1) as a due (2026-10-07, measured after unit 1a).
(val CORE "packages/doeff-cluster/src/doeff_cluster/_coordinator/core")
(val NOW-PLUS-ONE-ROSTER
  {"cluster_policy.hy" 0
   "api_policy.hy" 0
   "wake_policy.hy" 0})   ; the lower bound on the Rollout step instant in rollout-due was removed (R5)


(defadr ADR-DOE-CLUSTER-002
  :title "While there are no requests, the doeff-cluster coordinator waits in one wait until whichever comes first: the instant the answer of a judgment next changes (exactly the due), a request, a stop signal, or an outside event. It has no beat that wakes every second to look. Due functions return their answer as the closed type \"instant / now / never\", and never return now in a settled state"
  :status "accepted"
  :scope ["packages/doeff-cluster/src/doeff_cluster/_shared/intent/due_model.hy"
          "packages/doeff-cluster/src/doeff_cluster/_shared/core/due_policy.hy"
          "packages/doeff-cluster/src/doeff_cluster/_coordinator/intent/due_model.hy"
          "packages/doeff-cluster/src/doeff_cluster/_coordinator/core/wake_policy.hy"
          "packages/doeff-cluster/src/doeff_cluster/_coordinator/core/cluster_policy.hy"
          "packages/doeff-cluster/src/doeff_cluster/_coordinator/core/api_policy.hy"
          "packages/doeff-cluster/src/doeff_cluster/_coordinator/core/program.hy"
          "packages/doeff-cluster/src/doeff_cluster/_coordinator/core/rollout_policy.hy"
          "packages/doeff-cluster/src/doeff_cluster/_coordinator/protocol/request_queue.hy"
          "packages/doeff-cluster/src/doeff_cluster/_coordinator/protocol/kube.hy"
          "packages/doeff-cluster/src/doeff_cluster/_foundation/kube_client.hy"
          "docs/adr/defadr_doeff_cluster_002_coordinator_waits_until_the_next_due.hy"]
  :problem
    [(fact
       "On 2026-10-06 it was decided to fix every place that needs polling, and to make polling the last resort.")
     (fact
       "The 2026-09-30 decision (a reversible decision) put the sleep-until-the-next-due shape only under the simulated clock, left production's 1-second beat interval and judgment instants unchanged, and left undecided whether production should also sleep until the due (option 1). Production's 1 second was bound by test-the-production-inbox-keeps-the-one-second-tick in tests/test_idle_skip.hy (commit 86c8b2f94).")
     (fact
       "On 2026-10-07 the Mac coordinator read the 2026-10-06 rule as the answer to the open judgment (option 1) and decided to proceed with production also sleeping until the next due — this is the Mac coordinator's interpretation, not a record of option 1 being chosen by name.")
     (fact
       "From 02:22 to 02:24 JST on 2026-10-13 (17:21:19Z to 17:24:28Z) node-a stalled for 3 minutes (containerd stopped answering and kubelet's PLEG stalled; a gap in the host's PSI records), and the production coordinator running on node-a stalled without crashing. On the first step after it woke, every worker's last contact was past lease-ms, all were counted as silent, and 33 disappearances and returns reached the dispatching owner (turn-dispatcher). On 2026-10-16 the dispatcher decided to keep the placement (node-a) and to have the coordinator detect its own stall and not count lease expiry right after a stall (option 3).")
     (fact
       "Even with no requests, the coordinator woke every second to run one step (coordinator-step in core/program.hy passed TICK-MS 1000 to IdleNextRequests, and http-requests in the production intake shared/protocol/inbox.hy cut off the inbox after 1 second). Due functions returned 'not yet settled' and 'the next beat if a row exists' as the numeric now + 1 (liveness-due, task-due, sweep-due in cluster_policy.hy; rollout-due in idle_policy.hy)."
       :evidence "lines at doeff origin/main e604d71de3")]
  :context
    [(interpretation
       "The instant a due function returns is computed from the same function as the due value the judgment uses for comparison. In the wake-at-exactly-the-due shape, if the instant is 1 ms early the judgment does not change on waking, and since the function only looks at instants after now it skips the real change instant. If it is late, the judgment is delayed. The 1-second grid hid this misalignment.")
     (interpretation
       "The numeric now + 1 cannot distinguish 'a due 1 ms ahead' from 'one more step right away'. Splitting the answer into a closed type lets the side that decides how to wait (the reconcile loop) tell an immediate step from a wait until the due by name.")]
  :decision
    [(rule R1 "After a step that changes nothing, the coordinator waits on the intake in one wait until the next wake instant (next-wake in core/wake_policy.hy — the earliest of the due of a no-request step, the Rollout due, and the due of waits holding back a reply). A request, a stop signal, or an outside event wakes the wait. It has no 1-second period setting (TICK-MS) and no code that wakes on it (removed in unit 2b).")
     (rule R2 "Judgments come out at exactly the due instant. They are not rounded to a 1-second grid (the shape that keeps the grid and waits until the next grid point is not adopted, because the period constant would remain).")
     (rule R3 "The answer of a due function is the closed type DueAt(instant), DueNow (right now — judging at the current instant changes the state), or DueNever (if the state stays as is, time alone will not change it). It does not return the numeric now + 1. In a settled state (running one more no-request step changes nothing) it does not return DueNow.")
     (rule R4 "After a step that changed state without receiving a request, run one more step without waiting (after-step). After a step that received a request, wait until the next wake instant — if the state changed by the request has not settled, the due function returns DueNow (R3), so no empty step is run per request (fix A of 2026-10-07, approved by cisco-c8; previously one more step was always run even after a step that received a request). If DueNow continues for more than UNSETTLED-STEP-LIMIT steps, fail with CoordinatorUnsettled naming the fields that differ between the before and after states (do not keep spinning silently).")
     (rule R5 "The Kubernetes Deployments that a Rollout targets are not read on a timer (the former per-second read at ROLLOUT-TICK-MS, the lower bound on the interval between Rollout steps, and the every-10-seconds reread of targets that hold a replica count, were removed). The coordinator watches the Deployments that a Rollout needs to observe with a watch after a list (FollowDeployments, follow in foundation/kube_client), and wakes the intake inbox when the watch hands over a change (an outside event — R1). The labels of the node a worker is placed on are also not read on a timer (the former every-60-seconds reread NODE-LABELS-TTL-MS and its due node-reread-due were removed). The coordinator watches the nodes declared in heartbeats the same way (FollowNodes, follow-node in foundation/kube_client), and wakes the intake inbox when it hands over a label change. Rollout steps run every step, so rollout-due returns only dues after now. A write to a Deployment that succeeded is not reissued until an observation after that write arrives (rollout_policy.action-due — do not repeat the same write based on a stale observation from before the watch reports; the 1-second interval hid this). A failed action (including the marker annotation) is reissued with a doubling gap since the last failure, and that instant is a due even for a finished Rollout. The watch stream is received over a connection with TCP keepalive, so a peer that goes silent while still connected becomes an unreadable observation in about 30 seconds.")
     (rule R6 "The due of a placed, non-detached task is the instant its holder falls outside the reassign-after-ms window (the same comparison as place-tasks). The due of a waiting task that has a live worker it can be placed on is the first instant that worker falls outside the lease-ms window (vacancies and the end of drain are covered by the request and sweep dues).")
     (rule R7 "Simulation-only things (worker stand-ins sleeping through quiet beats, and the heartbeats they deposit before sleeping) live in the receiving layer (the simulated intake queue coordinator/protocol/request_queue.hy). The queue hands a deposited heartbeat to the reconcile loop at its instant as an ordinary heartbeat request, and the reconcile loop receives only the same requests as production (the reconcile loop has no effect that hands it simulation material — the former IdleNextRequests and IdleTaken were removed).")
     (rule R8 "The coordinator detects its own in-process stall when the off-schedule length of the previous step (from the previous step's judgment instant until it started waiting on the intake + oversleep past the R1 next wake instant — api_policy.stall-of) exceeds lease-ms, and, like resume-after-downtime, does not count the time from the previous step's judgment instant until now (the stalled length) as elapsed time or silence (api_policy.clocks-shifted shifts the workers' last contact, task leases, the start of Rollout stages, the replacement dues, the board's lease and TTL rows, the dues of drain and warm-table rows, and the last report of retired generations — so on the step right after a stall, workers alive before the stall are not counted as lease-expired, jobs are not moved, and the due at which a requester re-requests does not expire). Every ClusterState field must be assigned to exactly one of shifted (api_policy.STALL-SHIFTED-FIELDS) or unshifted (STALL-UNSHIFTED-FIELDS) — forcing whoever adds a field with a due to decide whether that due may expire after a stall (blind test A of the 2026-10-16 design validation). The stall judgment is only stall-of, and the reconcile loop does not create a stall with a different threshold or a different meaning (blind test B). A scheduled sleep (waiting until the due instant) is not counted as a stall. A wait without a due does not measure oversleep and detects only via the previous step's length. A detected stall is written as a single log line with its breakdown (previous step's length, oversleep). Costs accepted: stalls whose off-schedule length is at most lease-ms are not detected (same as before the fix; since sleep until the scheduled wake instant is not counted, an undetected stall is at most about twice the lease); and while stalls continue step after step, elapsed time is not counted, so judging a dead worker is also delayed during that period (if it continues, a stall line appears every step). The placement (production node-a) is not changed (2026-10-16).")]
  :laws
    [(law due-answers-are-closed
       :statement "for_all due function f in {liveness-due, task-due, sweep-due, tick-due, rollout-due, next-wake}, state s, instant now: f(s, now) ∈ DueAt ∪ DueNow ∪ DueNever"
       :counterexamples
         [(counterexample "liveness-due returns a difference in the recomputation of names counted as not alive as (+ now 1) — in the wake-at-exactly-the-due shape it cannot be told apart from a due 1 ms ahead (the mainline shape of 2026-10-06)")]
       :enforced-by ["packages/doeff-cluster/tests/test_due_answer.hy"
                     "packages/doeff-cluster/tests/test_next_wake.hy"]
       :wiring "wired (2026-10-07, unit 1a f459781cc) — the due functions' :post binds the closed type, and test_due_answer asserts the now and never answers")
     (law a-settled-state-is-never-due-now
       :statement "for_all state s, instant now: tick(tick(s, now), now) = tick(s, now) ⇒ tick-due(tick(s, now), now) ≠ DueNow"
       :counterexamples
         [(counterexample "a waiting task with no live worker it can be placed on is made DueNow — while all workers are busy or silent, the wait-until-the-due shape keeps running no-wait steps")]
       :enforced-by ["packages/doeff-cluster/tests/test_due_answer.hy::test-a-settled-state-is-never-due-now"]
       :wiring "wired (2026-10-07, unit 1a) — 4 settled scenarios; red with the wrong shape was confirmed locally")
     (law due-is-the-instant-the-judgment-changes
       :statement "for_all due function f and the judgment j it answers, state s, instant now: f(s, now) = DueAt(D) ⇒ j(s, D − 1) = j(s, now) ∧ j(s, D) ≠ j(s, now) (for a function declared to return a lower bound of the instants that may change the answer, only the first half)"
       :counterexamples
         [(counterexample "a due function returns an instant (>=) 1 ms off from the judgment's comparison (>) — on waking at exactly the due the judgment does not change, and since the function looks only after now it skips the real change")]
       :enforced-by ["packages/doeff-cluster/tests/test_idle_skip.hy (liveness, sweep, task)"
                     "packages/doeff-cluster/tests/test_quiet_due_placement_rollout.hy (replacement, Rollout stages)"
                     "packages/doeff-cluster/tests/test_due_answer.hy (placed tasks, waiting tasks)"
                     "packages/doeff-cluster/tests/test_next_wake.hy (wait dues)"
                     "packages/doeff-cluster/tests/test_due_boundaries.hy (readiness, stall, node reread)"]
       :wiring "wired (2026-10-07, unit 1b)")
     (law an-unsettled-loop-fails-loudly
       :statement "for_all sequence of reconcile-loop steps: number of consecutive DueNow steps > UNSETTLED-STEP-LIMIT ⇒ CoordinatorUnsettled (the message includes the names of fields that differ between the before and after states)"
       :counterexamples
         [(counterexample "while now keeps continuing, steps keep running without waiting — CPU keeps being used and it is unclear which judgment does not settle")]
       :enforced-by ["packages/doeff-cluster/tests/test_next_wake.hy::test-a-coordinator-that-never-settles-fails-naming-the-moving-field"
                     "packages/doeff-cluster/tests/test_coordinator_idle_wait.hy::test-a-loop-that-never-settles-fails-loudly"]
       :wiring "wired (2026-10-07, unit 2b) — the reconcile loop run-coordinator goes through count-unsettled every step (changes from steps that received a request are not counted)")
     (law the-coordinator-wakes-only-for-a-due-a-request-a-stop-or-an-event
       :statement "for_all production and simulated coordinators, interval I with no requests: the instants at which the coordinator wakes within I ⊆ {next-wake instants} ∪ {instants a request arrived} ∪ {instants of a stop signal} ∪ {instants of an outside event}"
       :counterexamples
         [(counterexample "the intake inbox is cut off after 1 second, and 3 steps run in 3 seconds with no requests (the 2026-10-07 mainline — measured on the wall-clock simulation)")]
       :enforced-by ["packages/doeff-cluster/tests/test_coordinator_idle_wait.hy"
                     "packages/doeff-cluster/tests/test_idle_skip.hy::test-a-quiet-system-steps-only-for-requests-and-deadlines"]
       :wiring "wired (2026-10-07, unit 2b)")
     (law a-rollout-follows-its-deployments-by-events
       :statement "for_all interval I in which a Rollout waits for a Deployment change: number of times the coordinator reads a Kubernetes Deployment within I = 0 (only the list at the start of the watch and the change events) ∧ if the Rollout's processing stage advances at the instant t a Deployment changed, that instant is t (not rounded to a 1-second grid)"
       :counterexamples
         [(counterexample "while a Rollout is in progress, Deployments are read every second (ROLLOUT-TICK-MS) — 19 reads in a quiet 20 seconds, and it advances to StoppingOld not at the instant the Pods became ready …635370 but at the next grid point …636000 (failure case 44bff9355)")
          (counterexample "right after a successful replica-count write, the same write is reissued every step based on a stale observation from before the watch reports the result — removing the 1-second interval makes the reconcile loop not settle (CoordinatorUnsettled at 101 steps)")
          (counterexample "on repeated failures of the same action, lastAction's at is left at the first failure's instant, and a failing marker annotation is reissued without a gap — after the gap cap (60 seconds) and on annotation failures it becomes a reissue every step")
          (counterexample "a watch stream that goes silent while still connected (neither FIN nor RST arrives) is waited on until the read cutoff (5.5 minutes), judging on stale observations meanwhile — TCP keepalive detects it in about 30 seconds")]
       :enforced-by ["packages/doeff-cluster/tests/test_rollout_kube_events.hy"
                     "packages/doeff-cluster/tests/test_kube_client_watch.hy"
                     "packages/doeff-cluster/tests/test_rollout_holes.hy::test-a-written-deployment-action-waits-for-an-observation-after-the-write"
                     "packages/doeff-cluster/tests/test_rollout_holes.hy::test-a-repeated-action-is-dated-at-its-last-attempt"
                     "packages/doeff-cluster/tests/test_rollout_holes.hy::test-a-failing-annotation-waits-for-the-retry-gap"
                     "packages/doeff-cluster/tests/test_kube_reads_off_loop.hy::test-the-emulated-k8s-wakes-the-loop-when-a-stalled-node-watch-answers"]
       :wiring "wired (2026-10-08)")
     (law a-node-label-is-followed-by-events
       :statement "for_all interval I in which the labels of the node a worker is placed on do not change: number of times the coordinator reads a Kubernetes Node within I = 0 (only the list at the start of the watch and the change events) ∧ at the instant t a label changed, the intake inbox wakes and on that step the worker's derived capabilities are re-derived from the new labels (it does not keep stale capabilities until the next period instant)"
       :counterexamples
         [(counterexample "node labels are reread every 60 seconds (NODE-LABELS-TTL-MS) — 2 reads in a quiet 150 seconds, and 1 second after the company-machine label is removed the worker still has company-machine (failure case b2f856817)")]
       :enforced-by ["packages/doeff-cluster/tests/test_node_label_events.hy"
                     "packages/doeff-cluster/tests/test_kube_client_watch.hy::test-a-node-is-listed-by-name-and-a-label-change-reaches-on-body"
                     "packages/doeff-cluster/tests/test_kube_reads_off_loop.hy::test-the-emulated-k8s-wakes-the-loop-when-a-stalled-node-watch-answers"]
       :wiring "wired (2026-10-08)")
     (law a-sleeping-stand-in-changes-no-decision
       :statement "for_all scenarios: between a run that wakes only on dues and a run that wakes extra (waking the coordinator every second), the sequence of durable state transitions with liveness markers removed (judgments and their instants), the final state of the store, and the scenario's answer are equal (worker stand-ins wait between rounds with the same wait as production; in the earlier shape, stand-ins slept through quiet beats and the queue handed over deposited heartbeats at their instants)"
       :counterexamples
         [(counterexample "deposited heartbeats are handed to the reconcile loop all at once on waking — judgment instants drift from the heartbeat instants (the earlier quiet-interval shape hid this by testing it against production judgments)")]
       :enforced-by ["packages/doeff-cluster/tests/test_idle_skip.hy"]
       :wiring "wired (2026-10-07, unit 2b; the sleeping stand-in and deposit path were removed in unit 5)")
     (law a-coordinator-stall-is-not-silence
       :statement "for_all pace p of the previous step (judgment instant j, instant waiting started w, scheduled wake instant d — for a wait without a due, d = none and max(0, now − d) = 0) and current step instant now: (w − j) + max(0, now − d) > lease-ms ⇒ stalled length = now − j ∧ at the time of that step's reconcile (tick), workers alive at the previous step are alive (no WorkerGone for workers alive before the stall) ∧ thereafter a worker with no heartbeat gets WorkerGone after the remainder of its silence as of the previous step. (w − j) + max(0, now − d) ≤ lease-ms (including a step that woke exactly at due d) ⇒ stalled length = 0"
       :counterexamples
         [(counterexample "on the first step after waking, the 3 stalled minutes are counted as worker silence — all workers lease-expire at once and 33 disappearances and returns come out (node-a on 2026-10-13; in the pre-fix sim scenario, WorkerGone for w1 and w2 comes out at the instant the stall ends)")
          (counterexample "a stall is distinguished only by elapsed time since the previous step — a step that woke at a worker's due instant (previous step + lease + 1) is counted as a stall, dues keep being shifted, and a worker that really went silent is never judged silent")
          (counterexample "the list of shifted fields lacks drain and the board's TTL rows — on the step right after a stall, only the drain and board rows of live workers expire (measured by blind test A of the 2026-10-16 design validation)")
          (counterexample "the reconcile loop does not go through stall-of and instead creates its own Stall and shifts on a step whose oversleep exceeds 5 seconds — it passes the judgment test and the simulation test, yet there are two definitions of a stall (likewise blind test B)")]
       :enforced-by ["packages/doeff-cluster/tests/test_coordinator_stall.hy"
                     "packages/doeff-cluster/tests/test_coordinator_stall_loop.hy"
                     "packages/doeff-cluster/tests/test_coordinator_stall_on_sim.hy"]
       :wiring "wired (2026-10-16) — the reconcile loop run-coordinator passes the pace StepPace every step, and coordinator-step goes through stall-resumed before reconciling. The scenario that stalls the coordinator for 3 minutes with the sim's StallCoordinator was confirmed red locally with the pre-fix shape (the sim stalls inside the intake wait = the oversleep branch; the previous-step-length branch — a storage clog — is held by the judgment check in test_coordinator_stall)")]
  :enforcement
    [(deftest test-adr-doe-cluster-002-due-answers-are-closed
       ;; Due functions return DueNever for a state with no due and DueNow for an unsettled state (not a numeric None or now + 1 — R3).
       (val timing (ClusterTiming))
       (val empty (ClusterState))
       (val expired (ClusterState :board (table-of #((TableWrite "k" (BoardRow :value 1 :version 1 :expires-ms 100 :size 1))))))
       (val answers [(run (liveness-due empty 0 timing)) (run (task-due empty 0 timing)) (run (sweep-due empty 0 timing))
                     (run (tick-due empty 0 timing)) (run (next-wake empty 0 timing (ClusterNaming) #()))])
       (assert (= answers [(DueNever) (DueNever) (DueNever) (DueNever) (DueNever)]) answers)
       (assert (= (run (sweep-due expired 200 timing)) (DueNow)) "sweeping an expired row is due now"))
     (deftest test-adr-doe-cluster-002-no-number-now-plus-one
       ;; Needle: places in the due-function files that write the numeric (+ now 1) as a due do not exceed the ledger (a new one is red, a forgotten removal is also red — R3).
       ;; The lower bound on the Rollout step instant in wake_policy ((max (+ now 1) …)) was removed (R5).
       (val repo-root (. (Path __file__) parent parent parent))
       (val counts (dfor name NOW-PLUS-ONE-ROSTER
                         name (.count (.read-text (/ repo-root CORE name) :encoding "utf-8") "(+ now 1)")))
       (assert (= counts NOW-PLUS-ONE-ROSTER)
               (+ "the count of (+ now 1) in the due-function files differs from the ledger (ADR-DOE-CLUSTER-002 R3 — return now as DueNow; a change that reduces it "
                  "trims NOW-PLUS-ONE-ROSTER in the same change): " (str counts))))]
  :plans [])
