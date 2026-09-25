(require doeff-hy.macros [deftest])

(import dataclasses [replace])
(import doeff_cluster.worker_model [JobSpec])
(import doeff_cluster.cluster_model [ClusterJob WorkerInfo Placement ClusterTiming ClusterState])
(import doeff_cluster.cluster_policy [place-jobs jobs-for])

(setv T (ClusterTiming :lease-ms 10000 :fence-ms 10000 :reassign-after-ms 30000))

(defn job [name #** kw] (ClusterJob (JobSpec name "m" #() "rev") #** kw))
(defn worker [name seen [capacity 10] #* labels] (WorkerInfo name (tuple labels) capacity seen))

(deftest test-spreads-and-respects-labels-and-pins
  (setv state (ClusterState
    #((job "a") (job "b") (job "k" :requires #(#("kind" "k3s"))) (job "p" :pin "mac"))
    {"mac" (worker "mac" 0) "new" (worker "new" 0) "pod" (worker "pod" 0 10 #("kind" "k3s"))}))
  (setv result (place-jobs 1000 state T))
  (assert (= (. (get result "k") worker) "pod"))
  (assert (= (. (get result "p") worker) "mac"))
  ;; a と b は空きの多い順に散る(p が mac・k が pod に載った後なので new が先)
  (assert (= (sorted (lfor n ["a" "b"] (. (get result n) worker))) ["mac" "new"])))

(deftest test-placement-is-stable-while-worker-is-alive
  (setv state (ClusterState #((job "a")) {"mac" (worker "mac" 0) "new" (worker "new" 0)}
                            {"a" (Placement "a" "new" 3 0)}))
  (assert (= (get (place-jobs 5000 state T) "a") (Placement "a" "new" 3 0))))

(deftest test-silent-worker-keeps-job-until-reassign-deadline
  ;; new の最後の heartbeat は 0。fence(T の 10 秒)で new は自分で止めている。移すのは 30 秒後から。
  (setv state (ClusterState #((job "a")) {"mac" (worker "mac" 40000) "new" (worker "new" 0)}
                            {"a" (Placement "a" "new" 1 0)}))
  (assert (= (. (get (place-jobs 30000 state T) "a") worker) "new"))
  (setv moved (get (place-jobs 30001 state T) "a"))
  (assert (= #(moved.worker moved.generation) #("mac" 2))))

(deftest test-no_candidate-leaves-job-unassigned-and-removed-job-is-dropped
  (setv state (ClusterState #((job "k" :requires #(#("kind" "k3s")))) {"mac" (worker "mac" 0)}
                            {"gone" (Placement "gone" "mac" 1 0)}))
  (assert (= (place-jobs 1000 state T) {})))

(deftest test-capacity-and-jobs-for
  (setv state (ClusterState #((job "a") (job "b") (job "c")) {"mac" (worker "mac" 0 2)}))
  (setv result (place-jobs 0 state T))
  (assert (= (sorted result) ["a" "b"]))
  (assert (= (lfor s (jobs-for (replace state :placements result) "mac") s.name) ["a" "b"])))

(deftest test-timing-rejects-reassign-before-fence
  (import pytest)
  (with [(pytest.raises ValueError)] (ClusterTiming :fence-ms 30000 :reassign-after-ms 30000)))



;; --- label と専用の印(dedicated = k8s の taint に当たる) -------------------------------------------

(import doeff_cluster.cluster_model [TaskRecord Requirement ComponentVersion])
(import doeff_cluster.cluster_policy [place-tasks unplaced-jobs])

(setv AGENT #("role" "agent") MARK #("dedicated" "role=agent"))
(defn mac [name [seen 0]] (worker name seen 10 #("kind" "mac") AGENT MARK))
(defn pod [name [seen 0]] (worker name seen 10 #("kind" "k3s") #("role" "controller")))

(deftest test-general-job-is-not-placed-on-a-dedicated-worker
  ;; Mac の方が空いていても、専用の印を求めない job は k3s へ
  (setv state (ClusterState #((job "a") (job "b") (job "c")) {"mac" (mac "mac") "atlas" (pod "atlas")}))
  (assert (= (sfor n ["a" "b" "c"] (. (get (place-jobs 1000 state T) n) worker)) #{"atlas"})))

(deftest test-agent-job-goes-to-the-dedicated-worker
  (setv state (ClusterState #((job "runner" :requires #(AGENT))) {"mac" (mac "mac") "atlas" (pod "atlas")}))
  (assert (= (. (get (place-jobs 1000 state T) "runner") worker) "mac")))

(deftest test-pin-does-not-override-the-dedicated-mark
  (setv state (ClusterState #((job "p" :pin "mac")) {"mac" (mac "mac")}))
  (assert (= (place-jobs 1000 state T) {}))
  (assert (in "置ける worker が無い" (get (unplaced-jobs 1000 state T) "p"))))

(deftest test-job-without-a-place-is-reported-unplaced
  ;; agent の job で Mac が居ない・一般の job で k3s が居ない
  (setv state (ClusterState #((job "runner" :requires #(AGENT)) (job "placer")) {"atlas" (pod "atlas")}))
  (setv s2 (ClusterState #((job "placer")) {"mac" (mac "mac")}))
  (assert (not-in "runner" (place-jobs 1000 state T)))
  (assert (in "置ける worker が無い" (get (unplaced-jobs 1000 state T) "runner")))
  (assert (in "置ける worker が無い" (get (unplaced-jobs 1000 s2 T) "placer"))))

(deftest test-job-leaving-a-worker-that-became-dedicated-waits-until-it-stopped-there
  ;; mac が専用の印を付けて戻った。mac に載っていた一般の job は外れ、mac がまだ動かしていると報告している間は置かず、
  ;; 止め終えた報告の後で k3s へ置く(同じ job を 2 つ動かさない)。
  (setv state (ClusterState #((job "placer")) {"mac" (mac "mac") "atlas" (pod "atlas")}
                            {"placer" (Placement "placer" "mac" 2 0)}
                            :statuses {"mac" {"at" 0 "jobs" [{"name" "placer" "phase" "running"}]}}))
  (assert (= (place-jobs 1000 state T) {}))
  (assert (= (get (unplaced-jobs 1000 (replace state :placements {}) T) "placer") "前の担い手が止め終えるのを待っている"))
  (setv stopped (replace state :placements {} :statuses {"mac" {"at" 1500 "jobs" []}}))
  (assert (= (. (get (place-jobs 2000 stopped T) "placer") worker) "atlas")))

(defn task [id requires]
  (TaskRecord id "digest" "m:e" "blob" "rev" #((ComponentVersion "python" "3")) requires 15000 20000 0))

(deftest test-task-follows-the-same-dedicated-rule
  (setv workers {"mac" (replace (mac "mac") :versions #((ComponentVersion "python" "3")))
                 "atlas" (replace (pod "atlas") :versions #((ComponentVersion "python" "3")))})
  (setv state (ClusterState #() workers {} {"t1" (task "t1" #()) "t2" (task "t2" #((Requirement #* AGENT)))}))
  (setv placed (place-tasks 1000 state {} T))
  (assert (= #((. (get placed "t1") worker) (. (get placed "t2") worker)) #("atlas" "mac")))
  ;; agent の task で Mac が居なければ、送らずに失敗(理由に求める label)
  (setv only-pod (ClusterState #() {"atlas" (get workers "atlas")} {} {"t3" (task "t3" #((Requirement #* AGENT)))}))
  (setv failed (get (place-tasks 1000 only-pod {} T) "t3"))
  (assert (= failed.phase "failed"))
  (assert (in "role" failed.detail)))
