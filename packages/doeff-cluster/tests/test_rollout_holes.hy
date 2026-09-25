;;; Rollout と heartbeat の穴(2026-09-25)。test_rollout の小さな世界(Sim)の上で:
;;;   - coordinator が止まっていた時間を段の時間に数えない(起動の時に止まっていた長さだけ時計をずらす)
;;;   - 観察(Observing)の間の Unknown(担い手の heartbeat の途絶)は完了にも失敗にも数えない
;;;   - 失敗が続く action は間を空けて出す(毎秒の送り直しをしない)
;;;   - 戻し(RollingBack)が終わらない時は stuck の印を出し、新は止めない
(require doeff-hy.macros [deftest])
(import dataclasses [replace])
(import doeff_cluster.cluster_model [ClusterState ClusterTiming Request TaskRecord])
(import doeff_cluster.durable_kv [full-kv state-from-kv])
(import doeff_cluster.api_policy [respond plan-rollouts resume-after-downtime mark-alive ALIVE-MARK-MS])
(import doeff_cluster.rollout_policy [rollout-step action-due retry-delay-ms shift-clocks RETRY-MAX-MS])
(import doeff_cluster.metrics_policy [metrics-text])
(import tests.test_rollout [Sim FORWARD DEP phases-of])

(setv T (ClusterTiming))


(defn #^ int restart-after [#^ Sim sim #^ int seconds]
  "coordinator が seconds 秒止まってから、耐久の置き場(key の表)から起き直す。止まっている間も世界(Pod・worker の process)は進む。"
  (setv kv (full-kv (mark-alive sim.state sim.now)))
  (for [_ (range seconds)]
    (+= sim.now 1000)
    (sim.advance-pods))
  (setv #(state gap) (resume-after-downtime (state-from-kv kv sim.now) sim.now))
  (setv sim.state state)
  gap)


(deftest test-downtime-in-the-middle-of-observing-is-not-counted
  ;; 観察 120 秒のうち 30 秒が過ぎた所で coordinator が 10 分止まった。起き直した直後に「観察の期間を終えた」と言わない。
  (setv sim (Sim))
  (sim.rollout "to-worker" FORWARD)
  (sim.run-until "to-worker" #("Observing"))
  (for [_ (range 30)] (sim.step))
  (setv since (get sim.state.rollouts "to-worker" "status" "phaseSinceMs"))
  (setv gap (restart-after sim 600))
  (assert (>= gap 600000))
  (assert (= (get sim.state.rollouts "to-worker" "status" "phaseSinceMs") (+ since gap)))
  (sim.step)
  (assert (= (sim.phase "to-worker") "Observing"))
  (assert (= (sim.run-until "to-worker" #("Complete" "RolledBack")) "Complete"))
  ;; 完了は起き直した後に 90 秒ほど見てから(止まっていた 10 分は数えていない)
  (setv done (get sim.state.rollouts "to-worker" "status" "completedMs"))
  (assert (>= (- done (+ since gap)) (* 1000 (get FORWARD "observeSeconds")))))


(deftest test-task-leases-survive-coordinator-downtime
  (setv task (TaskRecord "t1" "n" "e" "b" "r" #() #() 15000 (+ 1000 15000) 1000))
  (setv state (ClusterState :tasks {"t1" task} :alive-ms 5000))
  (setv #(after gap) (resume-after-downtime state 65000))
  (assert (= gap 60000))
  (assert (= (. (get after.tasks "t1") lease-until-ms) (+ 16000 60000)))
  ;; 生きていた時刻を知らない置き場(2026-09-25 より前)はずらさない
  (setv #(after gap) (resume-after-downtime (replace state :alive-ms 0) 65000))
  (assert (= gap 0))
  (assert (= after.alive-ms 65000)))


(deftest test-mark-alive-is-written-every-few-seconds-not-every-step
  (setv s (mark-alive (ClusterState) 10000))
  (assert (= s.alive-ms 10000))
  (assert (is (mark-alive s (+ 10000 (- ALIVE-MARK-MS 1))) s))
  (assert (= (. (mark-alive s (+ 10000 ALIVE-MARK-MS)) alive-ms) (+ 10000 ALIVE-MARK-MS))))


(setv SPEC {"from" {"kind" "Deployment" "namespace" "ns" "name" "old" "replicas" None "dryRun" False}
            "to" {"kind" "Service" "name" "new"}
            "readyTimeoutSeconds" 300 "stopTimeoutSeconds" 180 "observeSeconds" 60 "failAfterSeconds" 30
            "rollbackTimeoutSeconds" 600 "markDeployment" False "abort" False})
(setv STOPPED {"ready" "NotReady" "stopped" True "specReplicas" 0 "reason" "止まっている"})
(setv READY {"ready" "Ready" "stopped" False "specReplicas" 1 "reason" ""})
(setv UNKNOWN {"ready" "Unknown" "stopped" None "specReplicas" None "reason" "担い手の報告が古い"})
(setv NOT-READY {"ready" "NotReady" "stopped" False "specReplicas" 1 "reason" "拍が落ちた"})


(defn #^ dict observing [#^ int since] {"phase" "Observing" "phaseSinceMs" since "history" []})


(deftest test-unknown-while-observing-neither-completes-nor-fails
  ;; 観察 60 秒のうち 50 秒を Unknown で過ごしても、完了も失敗もしない。Unknown の長さだけ観察を延ばす。
  (setv #(status _) (rollout-step SPEC (observing 0) STOPPED READY 10000))
  (setv #(status _) (rollout-step SPEC status STOPPED UNKNOWN 11000))
  (assert (= (get status "unknownSinceMs") 11000))
  (setv #(again _) (rollout-step SPEC status STOPPED UNKNOWN 70000))
  (assert (= (get again "phase") "Observing"))
  (assert (is again status))                                ; Unknown の間は status を変えない(版が拍ごとに進まない)
  (setv #(status _) (rollout-step SPEC status STOPPED READY 61000))
  (assert (= (get status "phase") "Observing"))
  (assert (= (get status "phaseSinceMs") 50000))            ; Unknown の 50 秒だけずらした
  (setv #(status _) (rollout-step SPEC status STOPPED READY 111000))
  (assert (= (get status "phase") "Complete")))


(deftest test-complete-needs-a-ready-observation
  (setv #(status _) (rollout-step SPEC (observing 0) STOPPED NOT-READY 61000))
  (assert (= (get status "phase") "Observing")))


(deftest test-unknown-new-while-stopping-old-holds-the-stop
  (setv status {"phase" "StoppingOld" "phaseSinceMs" 0 "history" []})
  (setv running-old {"ready" "Ready" "stopped" False "specReplicas" 1 "reason" ""})
  (setv #(after actions) (rollout-step SPEC status running-old UNKNOWN 1000))
  (assert (= (get after "phase") "StoppingOld"))
  (assert (= actions [])))


(deftest test-a-failing-action-is-retried-with-growing-gaps
  (setv action {"op" "scale" "target" (get SPEC "from") "replicas" 0})
  (setv failed {"lastAction" {"op" "scale" "target" "Deployment:ns/old" "replicas" 0 "ok" False "error" "403" "at" 1000 "count" 3}})
  (assert (= (retry-delay-ms 3) 4000))
  (assert (not (action-due failed action 4999)))
  (assert (action-due failed action 5000))
  (assert (= (retry-delay-ms 30) RETRY-MAX-MS))
  ;; 違う action・成功した直後はすぐ出す
  (assert (action-due failed (| action {"replicas" 1}) 1001))
  (assert (action-due {"lastAction" (| (get failed "lastAction") {"ok" True})} action 1001)))


(deftest test-a-rollback-that-does-not-finish-is-marked-stuck-and-keeps-the-new
  (setv status {"phase" "RollingBack" "phaseSinceMs" 0 "rollbackStep" "restoreOld" "fromReplicas" 1 "history" []})
  (setv old-down {"ready" "NotReady" "stopped" False "specReplicas" 1 "reason" "Pod が起きない"})
  (setv #(s actions) (rollout-step SPEC status old-down READY 1000))
  (assert (is (.get s "stuck") None))
  (setv #(s actions) (rollout-step SPEC s old-down READY 601000))
  (assert (= (get s "stuck" "step") "restoreOld"))
  (assert (= actions []))                                   ; 旧の台数は既に 1(命令は出さない)・新は止めない
  (setv #(again _) (rollout-step SPEC s old-down READY 700000))
  (assert (= (get again "stuck") (get s "stuck")))         ; 印は付けた拍だけ変わる
  ;; 計器に出る
  (setv state (ClusterState :rollouts {"r" {"spec" SPEC "status" s}}))
  (assert (in "doeff_worker_rollout_stuck{rollout=\"r\"} 1" (metrics-text state 700000 T))))
