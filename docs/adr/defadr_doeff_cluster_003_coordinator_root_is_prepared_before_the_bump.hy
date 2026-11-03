;;; Executable ADR: preparation before a doeff-cluster coordinator version bump (building the self-boot root of the target version) is done by a Job outside
;;; the coordinator's Pod, and Deployment coordinator is replaced only after the Job finishes building. This replaces the DaemonSet coordinator-root-prepare that lived in the
;;; repo of the system built on top.
;;;
;;; Reason: on every version bump, all workers could not reach the coordinator for 105 to 141 seconds (investigation of 10-12). Job coordinator-prepare-<sha> is
;;; placed by a Flux Kustomization. The rule against standing up a dedicated Pod for a business task applies to the business tasks of the system on top; this Job is a mechanism of
;;; doeff-cluster itself, so the rule does not apply.
;;;
;;; Rollback: revert this ADR and the commit that added deploy/k8s/coordinator-prepare, and remove the Kustomization
;;; doeff-cluster-coordinator-prepare and the dependsOn line from the Flux declarations of the system on top (Flux declarations first — if the dir is removed first, the Kustomization being waited on
;;; never becomes Ready and the coordinator is not applied). After rollback, a version bump again stops the old Pod first and then builds inside the new Pod.

(require doeff-adr.macros [defadr rule law])
(import doeff-adr.macros [fact interpretation counterexample])
(require doeff-hy.macros [deftest defk <- val])
(import doeff [run])
(import pathlib [Path])
(import shutil)
(import subprocess)
(import yaml)


(val K8S "packages/doeff-cluster/deploy/k8s")
(val JOB-NAME-PREFIX "coordinator-prepare-")


(defk commit-of [manifest]
  {:pre [(: manifest dict)]
   :post [(: % str)]
   :tags {:context "doeff-cluster-adr" :role "judgment"}}
  "The value of env WORKER_DOEFF_COMMIT of the first container of a Deployment or Job (the yardstick for judging whether versions match)."
  (val container (get manifest "spec" "template" "spec" "containers" 0))
  (val found (lfor entry (get container "env") :if (= (get entry "name") "WORKER_DOEFF_COMMIT") (get entry "value")))
  (assert (= (len found) 1) (+ "there is not exactly one env WORKER_DOEFF_COMMIT: " (str found)))
  (get found 0))


(defk version-mismatches [coordinator-commit jobs]
  {:pre [(: coordinator-commit str) (: jobs list)]
   :post [(: % list)]
   :tags {:context "doeff-cluster-adr" :role "judgment"}}
  "List of messages describing version mismatches of the build Job (empty = match) — there must be exactly one Job, and the version in its env and the suffix of its name must equal the coordinator's version (R3)."
  (when (!= (len jobs) 1)
    (return [(.format "there is not exactly one build Job ({} found)" (len jobs))]))
  (val job (get jobs 0))
  (<- job-commit (commit-of job))
  (val name (get job "metadata" "name"))
  (val want-name (+ JOB-NAME-PREFIX coordinator-commit))
  (+ (if (= job-commit coordinator-commit) []
         [(.format "the Job's WORKER_DOEFF_COMMIT {} differs from the coordinator's {}" job-commit coordinator-commit)])
     (if (= name want-name) []
         [(.format "the Job's name {} is not {}" name want-name)])))


(defadr ADR-DOE-CLUSTER-003
  :title "Before a coordinator version bump, a Job outside the coordinator's Pod (coordinator-prepare-<commit>) builds the self-boot root of the target version on the same node, the same hostPath, and the same /work, and Flux does not apply Deployment coordinator until the Job finishes. The Job's version is copied from the coordinator's version line at build time (the version is defined by one line of coordinator.yaml). While the Job cannot be placed it waits in Pending (the deadline is held by the Pod spec), and the Job and the coordinator hold their place on atlas with a PriorityClass higher than the default 0"
  :status "accepted"
  :scope ["packages/doeff-cluster/deploy/k8s/coordinator-prepare/coordinator-prepare.yaml"
          "packages/doeff-cluster/deploy/k8s/coordinator-prepare/priority-class.yaml"
          "packages/doeff-cluster/deploy/k8s/coordinator-prepare/kustomization.yaml"
          "packages/doeff-cluster/deploy/k8s/coordinator/coordinator.yaml"
          "packages/doeff-cluster/deploy/k8s/kustomization.yaml"
          "packages/doeff-cluster/tests/test_k8s_declarations.py"
          "docs/adr/defadr_doeff_cluster_003_coordinator_root_is_prepared_before_the_bump.hy"]
  :problem
    [(fact
       "On every coordinator version bump (a change to the Deployment's WORKER_DOEFF_COMMIT, strategy Recreate), the workers on every machine stop reaching the coordinator in the same second. At 12:39 JST on 10-12 (fb32909f3 → 74ebb50b6), bytecode baking took 111 of the 141.1 seconds of downtime (stored=83 rebuilt=155 reused=0); at 14:43 JST (74ebb50b6 → 475ee1e5d), 79 of 105.2 seconds (stored=16 rebuilt=223 reused=0)."
       :evidence "two lines of the coordinator Pod's log (boot: prepared the root of … / coordinator: was stopped for N seconds)")
     (fact
       "The number of files rebaked is 4 to 8 times the number of changed files because of doeff-hy marker bumps (at 14:43, CODE_TAG 2→3 so not a single stored entry hit; at 12:39, RECORD_TAG 5→6 so all 133 unchanged Hy files missed) and changes to the closure digest of macros used by almost every module (defk, deff) (even 475ee1e5d → 6533804cf, which bumps no marker, missed 126 unchanged Hy files). The misses are correct (if a macro changes, the expansion may change)."
       :evidence "investigation of 10-12 (ran boot.sh's prepare role locally in the same order as production and reproduced stored/rebuilt with production's numbers)")
     (fact
       "The DaemonSet coordinator-root-prepare in the repo of the system on top (2026-10-09) built the target version in advance, but it was dropped without a replacement on 2026-10-10 when the coordinator's declaration moved to this repo's Kustomization."
       :evidence "commit bab798f50 of the system on top")
     (fact
       "On 2026-10-09, when the target root was built inside the coordinator's Pod, it competed with the running coordinator for the Pod's CPU limit of 2, and the throttling watchdog stopped the build."
       :evidence "header comment of bab798f50^:deploy/agent-worker/coordinator-root-prepare.yaml in the system on top")
     (fact
       "The first shape (749c11015) wrote the Job's version and name by hand in coordinator-prepare.yaml, and an ADR check watched the promise that a version-bump commit aligns all 3 places. a53f417cc at 18:37 JST on 10-12 bumped only coordinator.yaml's version to 3fcba1939 and left the Job at the previous version a1d17b0dd — the prepare Kustomization became Ready with the previous Job, and the coordinator built the target root inside the Pod and was replaced. The landing queue does not run doeff's checks (the gate in .agents/land-queue.toml looks only at conflict markers), so the ADR check first went red in the next daily full verification (6800d9f5e realigned it)."
       :evidence "body of doeff 6800d9f5e")
     (fact
       "At 17:44 JST on 2026-11-02, Flux applied bd09a669b, which bumps the coordinator's version 6e1996649 → fffb250ee, and created Job coordinator-prepare-fffb250ee…. The sum of memory requests on atlas was 19904Mi / allocatable 20263Mi (98%, 359Mi free), and the Pod with a 512Mi request could not be placed (FailedScheduling \"Insufficient memory … No preemption victims found\" — its priority was the default 0, the same as the other Pods on atlas, so there was nothing it could evict). The Job spec's activeDeadlineSeconds 1200 counts even while Pending, and the Pod never ran and became Failed with DeadlineExceeded at 18:04 JST. The prepare Kustomization was HealthCheckFailed, and the coordinator Kustomization was DependencyNotReady. The Job's name is determined by the version, and Flux reads a Failed Job as unchanged and does not recreate it, so it stayed stuck until the next version bump (9f10594e — created its Job at 18:13 JST, was placed, and finished building at 18:28 JST). atlas permanently carries Pods declared by other repos (6 object store Pods, a DB, the metrics system), and every time the fixed Pods grew, the room for the build Job disappeared."
       :evidence "kubectl Job and Event output and kustomize-controller logs (2026-11-02)")]
  :context
    [(interpretation
       "Baking seconds do not shrink by adding CPU (no stored entries, parallelism 8: 93 s real = scan 10, closure walk 21, baking 29, tool startup about 32). With an already-built root (marker .doeff-boot-ready), boot.sh starts without baking, so the only way to remove the build seconds from the downtime is to finish building before stopping the old Pod.")
     (interpretation
       "The shape of making the Deployment RollingUpdate and building inside the new Pod is not adopted — either the state files get two writers, or the new Pod does not become Ready and the replacement gets stuck.")
     (interpretation
       "Cannot be placed and cannot be built are different failures and are not made the same Failed. Cannot be built (boot.sh exits non-zero, baking hangs) is not fixed until the version is fixed, so the Job fails and the coordinator stays on the old version. Cannot be placed (not enough free room on the node) is fixed once room frees up, so waiting in Pending finishes the build without human intervention. The Pod spec's activeDeadlineSeconds is counted by kubelet from the instant it accepted the Pod (status.startTime), so a hang is still cut at the same 20 minutes as now, and time in Pending is not counted. While waiting, the prepare Kustomization does not become Ready, so the patrol watching Flux's Ready still notices.")
     (interpretation
       "Two shapes for protecting the placement were compared, and PriorityClass was adopted. (a) The shape of writing the build Job's 512Mi into atlas's memory budget and guarding it with a check is not adopted — the other workloads on atlas are declared by other repos, and this repo's check cannot read their requests, so nobody would keep the written budget (on 11-02, too, fixed Pods from other repos grew and free room fell to 359Mi). (b) A PriorityClass is upheld by the scheduler: only when free room is insufficient, it evicts just enough Pods at the default 0 (keeping those with higher values and those that started earlier). The harm is that each version bump while atlas is full evicts a few 0 Pods — evicted Pods are recreated by their own controllers, and room comes back once the build Job finishes (seconds to minutes). A long-running Job (a 12-hour copy, etc.) loses its progress if evicted, but that is one of the harms of atlas being full, and it was judged smaller than the harm of version bumps stopping until a human intervenes.")
     (interpretation
       "Narrowing the eviction targets to doeff's own things is not adopted for now. preemptionPolicy Never only puts it at the head of the queue and does not move Pods already placed, so with atlas full of long-lived Pods as on 11-02 it stops the same way. Giving atlas's workers a negative-value class does not narrow it either, because the scheduler restores in order of importance, so new 0 Pods are chosen to be evicted first. The only shape that can narrow it is a 512Mi placeholder owned by doeff (always keeping a pause Pod with a class lower than the Job on atlas), but it always uses 512Mi of atlas's requests, and if another Pod gets in while the Job runs, the next version bump evicts 0 Pods (the hole is partial). Add it when not moving other repos' Pods is given more weight. While waiting, and during a second try, the prepare Kustomization becomes HealthCheckFailed every 25m timeout and re-waits 1 minute later — the Job is not Failed; only the Ready display flaps.")
     (interpretation
       "The coordinator also has the same class. Recreate stops the old Pod and then creates the new one, so if another Pending Pod on atlas takes the 1536Mi freed at that moment first, the new coordinator cannot be placed and all workers remain unable to reach the coordinator (heavier than the build Job not being placed). The value is 10 million — above the default 0 and well below the storage and node system classes (900 million to 2 billion) — so only ordinary Pods are evicted.")]
  :decision
    [(rule R1 "The root of the coordinator's target version is built by Job coordinator-prepare-<commit> (deploy/k8s/coordinator-prepare) using boot.sh's prepare role. The Job mounts the same hostPath as Deployment coordinator on the same node at the same absolute path /work (the root holds the absolute path it was built at), does not mount the records at /work/coord, and holds no credentials (ServiceAccount token, the ConfigMap of the system on top). It builds in its own cgroup and does not eat the running coordinator's CPU.")
     (rule R2 "Flux applies deploy/k8s/coordinator-prepare with a separate Kustomization (wait: true), and the deploy/k8s/coordinator Kustomization, via dependsOn, is not applied until that Kustomization is Ready at the same revision (the Job has finished). If the Job fails, the coordinator keeps running on the old version. The Job has no ttlSecondsAfterFinished — if a TTL deleted it, Flux would recreate it on the next reconcile, so an old-version Job is deleted by Flux's prune when the version in its name changes and it drops out of the declarations (the built root and the bytecode store stay on the hostPath). The Job carries the Flux marker kustomize.toolkit.fluxcd.io/force: Enabled — a Job's template is immutable, so a commit that changes the template without changing the version would have its apply refused and the coordinator would not be applied. With the marker, Flux deletes and recreates the refused Job (with an already-built root it finishes in seconds).")
     (rule R3 "The version is defined by one line: the WORKER_DOEFF_COMMIT line of coordinator/coordinator.yaml. The Job's env WORKER_DOEFF_COMMIT and the suffix of its name are not written in coordinator-prepare.yaml; when coordinator-prepare/ is built, it reads ../coordinator and copies them via replacements (the coordinator objects it reads are dropped from the output with the local-config marker, and this Kustomization applies only R5's PriorityClass and the Job). A version-bump commit changes only that one line of coordinator.yaml — the alignment promise does not rely on a check (the landing queue does not run checks).")
     (rule R4 "The build Job's deadline is held by the Pod template's spec.activeDeadlineSeconds (1200 seconds — cuts a hang of one try), and the Job spec has no activeDeadlineSeconds. While it cannot be placed (Pending for lack of free room on the node), the Job does not fail but waits; once placed it finishes building and the prepare Kustomization returns to Ready without human intervention. The Job fails only when it cannot be built (exits non-zero, a try exceeds the deadline). When a try is removed (kubelet eviction on memory shortage, eviction by a higher class, drain — Pod condition DisruptionTarget), that is not a failure to build either, so the Job's podFailurePolicy (Ignore on DisruptionTarget) recreates it without counting toward backoffLimit.")
     (rule R5 "The build Job and Deployment coordinator have PriorityClass doeff-cluster-coordinator (value 10 million, PreemptLowerPriority, globalDefault false), and when the node lacks free memory they are placed by evicting Pods at the default 0. The class is owned by coordinator-prepare/ (Flux applies it before coordinator/ — if coordinator/ owned it, on the first apply the Job's Pod would name a nonexistent class and not be created, and the coordinator/ Kustomization waits for that Job, so they would wait on each other and stop).")]
  :laws
    [(law the-prepare-job-builds-the-coordinators-version
       :statement "for_all value v of the version line of coordinator.yaml: the build output of coordinator-prepare/ has exactly one Job with role prepare ∧ its WORKER_DOEFF_COMMIT = v ∧ its name = coordinator-prepare- + v"
       :counterexamples
         [(counterexample "only the coordinator's version is bumped and the Job is left at the previous version — Flux sees the unchanged Job as Ready and applies the coordinator, and the new Pod builds inside again (the downtime returns; a53f417cc at 18:37 on 10-12)")
          (counterexample "only the Job's env is bumped without changing its name — a Job's template is immutable, so Flux's apply is refused and the coordinator is not applied")]
       :enforced-by ["docs/adr/defadr_doeff_cluster_003_coordinator_root_is_prepared_before_the_bump.hy::test-adr-doe-cluster-003-the-prepare-job-builds-the-coordinators-version"
                     "docs/adr/defadr_doeff_cluster_003_coordinator_root_is_prepared_before_the_bump.hy::test-adr-doe-cluster-003-version-mismatches-are-named"
                     "packages/doeff-cluster/tests/test_k8s_declarations.py::test_prepare_job_follows_a_bump_of_the_coordinator_line_alone"]
       :wiring "wired (2026-10-12). Switched to the copy-the-version shape on 2026-10-16 — the build output is what guards it; the check is the notice when the shape breaks")
     (law the-prepare-job-shares-only-the-root-with-the-coordinator
       :statement "for_all Job J with role prepare and Deployment coordinator C: node(J) = node(C) ∧ hostPath(J's work) = hostPath(C's work) ∧ J's mounts = {/work} ∧ J has no ServiceAccount token and no envFrom ∧ J has no ttlSecondsAfterFinished"
       :counterexamples
         [(counterexample "it builds at a different path (e.g. /prewarm/coordinator) — the python of the venv of the root carrying the completion marker is not found in the coordinator's Pod and it cannot start (header comment of the DaemonSet in the system on top)")
          (counterexample "finished Jobs are deleted with ttlSecondsAfterFinished — Flux recreates it on the next reconcile and keeps starting rebuild Pods")]
       :enforced-by ["packages/doeff-cluster/tests/test_k8s_declarations.py::test_coordinator_root_is_prepared_by_one_job_beside_it"
                     "packages/doeff-cluster/tests/test_k8s_declarations.py::test_coordinator_prepare_job_is_not_removed_by_ttl"
                     "packages/doeff-cluster/tests/test_k8s_declarations.py::test_coordinator_prepare_builds_alone_with_only_the_job_and_its_priority_class"]
       :wiring "wired (2026-10-12). The Flux ordering (R2's wait and dependsOn) is held by the Flux declarations in the repo of the system on top, outside this repo's checks")
     (law the-prepare-job-waits-while-it-cannot-be-placed
       :statement "for_all Job J with role prepare: J's spec has no activeDeadlineSeconds ∧ spec.activeDeadlineSeconds of J's Pod template = 1200 ∧ J's podFailurePolicy = a single rule, Ignore on DisruptionTarget"
       :counterexamples
         [(counterexample "the deadline is placed on the Job spec — it counts even while Pending and unplaced, and the Job becomes Failed without its Pod ever running. The Job's name is determined by the version and Flux does not recreate a Failed Job, so the coordinator Kustomization is not applied until the next version bump or human intervention (2026-11-02)")
          (counterexample "the deadline is removed — when the build hangs, the Job never finishes, the coordinator stays on the old version, and the prepare Kustomization keeps waiting without any failure shape")
          (counterexample "no podFailurePolicy — if kubelet evictions from memory shortage on atlas (atlas's workers: 5 times from 10-29 to 11-01) remove a try twice, the Job becomes Failed and gets stuck just as when it could not be placed")]
       :enforced-by ["packages/doeff-cluster/tests/test_k8s_declarations.py::test_coordinator_prepare_job_waits_while_unplaced_and_cuts_a_hang_of_its_run"
                     "packages/doeff-cluster/tests/test_k8s_declarations.py::test_a_removed_try_of_the_prepare_job_is_not_counted_as_a_failure"]
       :wiring "wired (2026-11-02)")
     (law the-coordinator-and-its-prepare-job-outrank-the-default-pods
       :statement "for_all Job J with role prepare and Deployment coordinator C: priorityClassName(J) = priorityClassName(C) = doeff-cluster-coordinator ∧ 0 < value(that class) < 1 billion ∧ preemptionPolicy = PreemptLowerPriority ∧ globalDefault = false ∧ only coordinator-prepare/ owns that class"
       :counterexamples
         [(counterexample "no class (default 0) — the same value as the other Pods on atlas, so the scheduler has nothing to evict, and when free room drops below 512Mi the build Job cannot be placed (359Mi free on 2026-11-02)")
          (counterexample "the class is placed in coordinator/ — on the first apply the build Job's Pod names a nonexistent class and is not created, and the coordinator/ Kustomization waits for that Job to finish, so the two wait on each other and stop")
          (counterexample "globalDefault is set to true — ordinary Pods get the same value too, and there is nothing left to evict")]
       :enforced-by ["packages/doeff-cluster/tests/test_k8s_declarations.py::test_coordinator_and_its_prepare_job_outrank_the_default_pods_on_their_node"
                     "packages/doeff-cluster/tests/test_k8s_declarations.py::test_the_priority_class_is_applied_before_the_pods_that_name_it"
                     "packages/doeff-cluster/tests/test_k8s_declarations.py::test_coordinator_prepare_builds_alone_with_only_the_job_and_its_priority_class"]
       :wiring "wired (2026-11-02)")
     (law a-prepare-job-changed-at-the-same-version-is-recreated
       :statement "for_all Job J with role prepare: kustomize.toolkit.fluxcd.io/force in J's annotations = Enabled"
       :counterexamples
         [(counterexample "without the marker, the Job's template is changed without changing the version (e.g. the very commit that moves the deadline into the Pod spec) — Flux's apply is refused as immutable, the prepare Kustomization does not become Ready, and the coordinator is not applied until the next version bump")]
       :enforced-by ["packages/doeff-cluster/tests/test_k8s_declarations.py::test_a_prepare_job_changed_at_the_same_version_is_recreated_not_refused"]
       :wiring "wired (2026-11-02)")]
  :enforcement
    [(deftest test-adr-doe-cluster-003-the-prepare-job-builds-the-coordinators-version
       ;; Build the current declarations with kubectl kustomize, as Flux does, and check that the build Job's version and name match the coordinator's version (R3).
       (val k8s (/ (. (Path __file__) parent parent parent) K8S))
       (val kubectl (shutil.which "kubectl"))
       (assert (is-not kubectl None) "kubectl is missing — cannot build and check the declarations (this check is not skipped)")
       (setv [coordinators jobs]
         (lfor #(part kind) [#("coordinator" "Deployment") #("coordinator-prepare" "Job")]
               :setv done (subprocess.run [kubectl "kustomize" (str (/ k8s part))] :capture-output True :text True :timeout 25 :check False)
               (do (assert (= done.returncode 0) (+ "kubectl kustomize " part " failed: " done.stderr))
                   (lfor doc (yaml.safe-load-all done.stdout) :if (and (is-not doc None) (= (get doc "kind") kind)) doc))))
       (assert (= (len coordinators) 1) coordinators)
       (val problems (run (version-mismatches (run (commit-of (get coordinators 0))) jobs)))
       (assert (= problems [])
               (+ "the build Job's version does not match the coordinator's version (ADR-DOE-CLUSTER-003 R3 — the Job's version is copied by the replacements in coordinator-prepare/ "
                  "from the version line of coordinator.yaml): " (str problems))))
     (deftest test-adr-doe-cluster-003-version-mismatches-are-named
       ;; The mismatch judgment names and refuses the 2 counterexamples (a declaration that bumped only the coordinator, a declaration that bumped only the Job's env).
       (val old "475ee1e5d7d5894f6ba4c73907be57b057ea70e9")
       (val new "a1d17b0dd3047d1111f442e5a1ba4348ea3eab05")
       (setv [matching coordinator-only env-only]
         (lfor #(name commit) [#((+ "coordinator-prepare-" new) new) #((+ "coordinator-prepare-" old) old)
                               #((+ "coordinator-prepare-" old) new)]
               {"metadata" {"name" name}
                "spec" {"template" {"spec" {"containers" [{"env" [{"name" "WORKER_DOEFF_COMMIT" "value" commit}]}]}}}}))
       (assert (= (run (version-mismatches new [matching])) []))
       (assert (= (len (run (version-mismatches new [coordinator-only]))) 2))
       (assert (= (run (version-mismatches new [env-only]))
                  [(.format "the Job's name coordinator-prepare-{} is not coordinator-prepare-{}" old new)]))
       (assert (= (run (version-mismatches new [])) ["there is not exactly one build Job (0 found)"])))]
  :plans [])
