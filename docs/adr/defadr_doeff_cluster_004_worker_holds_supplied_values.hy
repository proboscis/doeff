;;; Executable ADR: so that a job re-placed while the API server is down can still get the values of its Secret and ConfigMap
;;; references and start, a worker holds a copy of the referenced values of the jobs that could be placed on it, and a job's read
;;; answers from that copy only when the API server cannot be reached.
;;;
;;; At 2026-10-29 23:30 JST the machine eos, which hosts the API server, went down; the jobs that
;;; ran on eos were re-placed onto atlas in the pool, but at startup they asked the API server for their referenced values, could not
;;; reach it, and failed 17 times in 16 minutes. Until then no ADR decided how supplied-values behaves when the API server is down
;;; (checked by reading docs/adr for "supplied" — 2026-10-30).
;;;
;;; Rollback: revert the commit that added this ADR. The copy files (<work root>/supplied-held) remain, but nothing reads them (delete them by hand if wanted).

(require doeff-adr.macros [defadr rule law])
(import doeff-adr.macros [fact interpretation counterexample])
(require doeff-hy.macros [deftest <-])
(import doeff_cluster._shared.core.supplied_spelling :as spelling)
(import doeff_cluster [supplied-spellings])
(import doeff_cluster._shared.protocol.supplied_values :as reader)


(defadr ADR-DOE-CLUSTER-004
  :title "A worker holds, while the API server answers, a copy under its work root (files 0600, dirs 0700) of the values of the ConfigMap and Secret references in the :environ of the declarations of the jobs that could be placed on it (the spellings the coordinator passes in the supplied field of the heartbeat reply). A job's read answers from the copy only when the API server cannot be reached (HttpFailed); when it is reached and refuses, the copy is not read"
  :status "accepted"
  :scope ["packages/doeff-cluster/src/doeff_cluster/_shared/core/supplied_spelling.hy"
          "packages/doeff-cluster/src/doeff_cluster/_shared/protocol/supplied_values.hy"
          "packages/doeff-cluster/src/doeff_cluster/_coordinator/core/cluster_policy.hy"
          "packages/doeff-cluster/src/doeff_cluster/_coordinator/protocol/replies.hy"
          "packages/doeff-cluster/src/doeff_cluster/_worker/protocol/coordinator_link.hy"
          "packages/doeff-cluster/src/doeff_cluster/_worker/protocol/supplied_hold.hy"
          "packages/doeff-cluster/src/doeff_cluster/_worker/entry/main.hy"
          "packages/doeff-cluster/src/doeff_cluster/_sim/k8s_supplied.hy"
          "packages/doeff-cluster/tests/test_supplied_held.hy"
          "docs/adr/defadr_doeff_cluster_004_worker_holds_supplied_values.hy"]
  :problem
    [(fact
       "At startup a job reads the values of the references in its declaration's :environ through supplied-setting and supplied-directory, with one GET to the API server of its own partition. If that is unreachable it ends with SuppliedValueUnavailable; there was no way to get the values other than the API server."
       :evidence "held-entries and entries-of-answer in shared/protocol/supplied_values.hy (the form before this ADR)")
     (fact
       "A partition's API server lives on one machine. When that machine stops, workers on every machine lose access to the values. Even if a job can be placed on two machines, when the reason for moving is that machine stopping, the destination also needs the same API server to start, so it can never start (the destination lies in the same failure domain as the cause of the move)."
       :evidence "Measured (10-29 23:31-23:47 JST: the job on atlas failed 17 times with ConnectTimeout)")]
  :context
    [(interpretation
       "Running the control plane on several machines, or changing the restart interval, is outside this ADR. This ADR makes the way values are obtained itself survive an API server outage.")
     (interpretation
       "The form that writes the copy only when a job reads (remembering values that were read) is not taken — a job moving off a stopped machine has never read on its destination yet. To hold in advance the references of jobs that could be placed, the coordinator, which owns placement decisions, lists the spellings per worker.")
     (interpretation
       "The form that loads the values onto the coordinator or the record store and distributes them is not taken — Secret contents are not put on coordinator task rows or in the record store (the rule in the header comment of supplied_values). The coordinator carries only spellings; each worker reads the values from the API server under its own identity.")]
  :decision
    [(rule R1 "The coordinator puts supplied in its heartbeat reply: the de-duplicated list of reference spellings (values of the spelling in shared/core/supplied_spelling) in the :environ of the declarations of the jobs that could be placed on that worker (the declarations to place whose pin and effective needs match — liveness, drain and free capacity are not considered). The field is not written in replies to a worker that has not named itself or to a retired generation (an empty list means 'hold nothing' and deletes every copy, so it is not sent when unknown).")
     (rule R2 "A Pod worker (one that builds a client checking the API server with the partition's CA) runs the value-copy keeper as a background task. When the spellings in the reply change, when 5 minutes have passed since everything was last read in full, or when something was unreachable last round, the keeper groups the spellings by object, asks the API server once per object, and writes the copy by replacement to <work root>/supplied-held/<worker name>/<kind>/<namespace>/<name>.json (file 0600, every dir level below the work root 0700). A single-value reference holds only the key the declaration names; a whole-object reference holds every key. Unreachable, unreadable token, 401 and 5xx keep the previous copy; 403 and 404 delete the copy; copies no longer in the spellings are deleted.")
     (rule R3 "A job's read (held-entries) answers from the copy of the worker running the job (looked up by the machine facts WORK_ROOT and WORKER_NAME) only when the API server cannot be reached (HttpFailed), and prints the reference spelling and the time the copy was read on one warning line. When the server is reached and refuses (403, 404 and others) the copy is not read and the read refuses as today. If there is no copy either, it refuses with SuppliedValueUnavailable, adding to the unreachable reason why there is no copy.")
     (rule R4 "Values never appear in logs, in coordinator replies and task rows, or in the record store. Only reference spellings, counts and times appear. Production and simulation run the same functions (hold-supplied, held-entries); the simulation is tested in the down world of doeff_cluster._sim.k8s_supplied.")]
  :laws
    [(law a-job-placed-while-the-api-server-is-down-starts-from-the-copy
       :statement "for_all worker w and reference r ∈ supplied(w) for which w's keeper held a copy of r while the API server was answering: the read of r by a job placed on w while the API server is unreachable answers with the copied value"
       :counterexamples
         [(counterexample "No copy is held — when the API server's machine stops, jobs on their destination keep failing at startup (10-29 23:31 onward)")
          (counterexample "The copy is written only when a job reads — a job that moved off a stopped machine has never read on its destination")]
       :enforced-by ["packages/doeff-cluster/tests/test_supplied_held.hy::test-a-job-placed-while-the-api-server-is-down-starts-from-the-worker-copy"
                     "packages/doeff-cluster/tests/test_supplied_held.hy::test-a-directory-written-from-the-copy-has-every-held-key"
                     "packages/doeff-cluster/tests/test_supplied_held.hy::test-the-coordinator-names-only-the-spellings-of-jobs-the-worker-could-take"]
       :wiring "Wired (2026-10-30)")
     (law a-refused-value-is-not-answered-from-the-copy
       :statement "for_all reference r: when the API server is reached and refuses r (403, 404), the read of r refuses instead of answering from the copy ∧ the keeper deletes that copy. An r with no copy, another worker's copy, and keys the declaration does not name are not answered from a copy even when unreachable"
       :counterexamples
         [(counterexample "Answering from the copy even on a refusal that was received — values that were deleted or whose permission was revoked stay in use")
          (counterexample "Not separating copies by worker name — two workers sharing one work root delete each other's copies as no longer in the spellings")]
       :enforced-by ["packages/doeff-cluster/tests/test_supplied_held.hy::test-a-refused-or-unheld-reference-is-not-answered-from-a-copy"
                     "packages/doeff-cluster/tests/test_supplied_held.hy::test-the-copy-follows-the-declarations-and-the-api-server-answers"]
       :wiring "Wired (2026-10-30)")
     (law the-copy-is-private-and-its-values-stay-out-of-the-log
       :statement "for_all copy file f and each dir d above it (below the work root): mode(f) = 0600 ∧ mode(d) = 0700 ∧ no value appears, neither verbatim nor base64, in log lines of the keeper and the read or in coordinator replies"
       :counterexamples
         [(counterexample "Relying on the mode of MakeDirectory and creating intermediate levels with the umask mode — an intermediate level becomes a dir others can read")]
       :enforced-by ["packages/doeff-cluster/tests/test_supplied_held.hy::test-the-copy-is-private-and-its-values-stay-out-of-the-log"]
       :wiring "Wired (2026-10-30)")
     (law the-coordinator-and-the-reader-share-one-spelling
       :statement "The spelling the coordinator lists and the spelling the value-reading part accepts are the same regular expression in shared/core/supplied_spelling"
       :counterexamples
         [(counterexample "Writing the spelling in two places and changing only one — a job reads a reference the coordinator does not list, and there is no copy")]
       :enforced-by ["docs/adr/defadr_doeff_cluster_004_worker_holds_supplied_values.hy::test-adr-doe-cluster-004-one-spelling"]
       :wiring "Wired (2026-10-30)")]
  :enforcement
    [(deftest test-adr-doe-cluster-004-one-spelling
       ;; The reading part's spelling is the shared/core spelling itself (R1, R3 — the holder and the reader are decided by the same text).
       (assert (is reader.REF-SPELLING spelling.REF-SPELLING))
       (assert (is reader.OBJECT-SPELLING spelling.OBJECT-SPELLING))
       ;; Values that are not references (URL, path) are not listed; single-value and whole-object references are.
       (<- found tuple (supplied-spellings #(#("A" "secret:ns/name/key") #("B" "configmap:ns/obj") #("C" "https://example.invalid/x")
                                             #("D" "/etc/x") #("E" "secret:ns/name/key"))))
       (assert (= found #("configmap:ns/obj" "secret:ns/name/key")) found))]
  :plans [])
