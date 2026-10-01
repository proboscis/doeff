;;; Rollout と heartbeat の穴(2026-09-25)。test_rollout の小さな世界(Sim)の上で:
;;;   - coordinator が止まっていた時間を段の時間に数えない(起動の時に止まっていた長さだけ時計をずらす)
;;;   - 観察(Observing)の間の Unknown(担い手の heartbeat の途絶)は完了にも失敗にも数えない
;;;   - 失敗が続く action は間を空けて出す(毎秒の送り直しをしない)
;;;   - 戻し(RollingBack)が終わらない時は stuck の印を出し、新は止めない
(require doeff-hy.macros [deftest val var])
(import dataclasses [replace])
(import doeff_cluster.shared.intent.protocol [ClusterTiming Request])
(import doeff_cluster.coordinator.intent.cluster_model [ClusterState RolloutRow RolloutStatus TaskRecord])
(import doeff_cluster.coordinator.core.durable_kv [full-kv state-from-kv])
(import doeff_cluster.coordinator.core.api_policy [plan-rollouts resume-after-downtime mark-alive ALIVE-MARK-MS])
(import doeff_cluster.coordinator.protocol.request_bodies [responded])
(import doeff_cluster.coordinator.core.rollout_policy [validate-rollout-spec rollout-step action-due retry-delay-ms shift-clocks RETRY-MAX-MS])
(import doeff_cluster.coordinator.core.metrics_policy [metrics-text])
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
  (setv since (. (get sim.state.rollouts "to-worker") status phase-since-ms))
  (setv gap (restart-after sim 600))
  (assert (>= gap 600000))
  (assert (= (. (get sim.state.rollouts "to-worker") status phase-since-ms) (+ since gap)))
  (sim.step)
  (assert (= (sim.phase "to-worker") "Observing"))
  (assert (= (sim.run-until "to-worker" #("Complete" "RolledBack")) "Complete"))
  ;; 完了は起き直した後に 90 秒ほど見てから(止まっていた 10 分は数えていない)
  (setv done (. (get sim.state.rollouts "to-worker") status completed-ms))
  (assert (>= (- done (+ since gap)) (* 1000 (get FORWARD "observeSeconds")))))


(deftest test-task-leases-survive-coordinator-downtime
  (setv task (TaskRecord "t1" "n" "b" "r" #() #() 15000 (+ 1000 15000) 1000))
  (setv state (ClusterState :tasks {"t1" task} :alive-ms 5000))
  (val reply-1 (resume-after-downtime state 65000))
  (var after (get reply-1 0))
  (var gap (get reply-1 1))
  (assert (= gap 60000))
  (assert (= (. (get after.tasks "t1") lease-until-ms) (+ 16000 60000)))
  ;; 生きていた時刻を知らない置き場(2026-09-25 より前)はずらさない
  (val reply-2 (resume-after-downtime (replace state :alive-ms 0) 65000))
  (:= after (get reply-2 0))
  (:= gap (get reply-2 1))
  (assert (= gap 0))
  (assert (= after.alive-ms 65000)))


(deftest test-mark-alive-is-written-every-few-seconds-not-every-step
  (setv s (mark-alive (ClusterState) 10000))
  (assert (= s.alive-ms 10000))
  (assert (is (mark-alive s (+ 10000 (- ALIVE-MARK-MS 1))) s))
  (assert (= (. (mark-alive s (+ 10000 ALIVE-MARK-MS)) alive-ms) (+ 10000 ALIVE-MARK-MS))))


(setv SPEC (validate-rollout-spec {"from" {"kind" "Deployment" "namespace" "ns" "name" "old" "replicas" None "dryRun" False}
                                   "to" {"kind" "Service" "name" "new"}
                                   "readyTimeoutSeconds" 300 "stopTimeoutSeconds" 180 "observeSeconds" 60 "failAfterSeconds" 30
                                   "rollbackTimeoutSeconds" 600 "markDeployment" False "abort" False}))
(setv STOPPED {"ready" "NotReady" "stopped" True "specReplicas" 0 "reason" "止まっている"})
(setv READY {"ready" "Ready" "stopped" False "specReplicas" 1 "reason" ""})
(setv UNKNOWN {"ready" "Unknown" "stopped" None "specReplicas" None "reason" "担い手の報告が古い"})
(setv NOT-READY {"ready" "NotReady" "stopped" False "specReplicas" 1 "reason" "拍が落ちた"})


(defn #^ RolloutStatus observing [#^ int since] (RolloutStatus :phase "Observing" :phase-since-ms since))


(deftest test-unknown-while-observing-neither-completes-nor-fails
  ;; 観察 60 秒のうち 50 秒を Unknown で過ごしても、完了も失敗もしない。Unknown の長さだけ観察を延ばす。
  (val reply-3 (rollout-step SPEC (observing 0) STOPPED READY 10000))
  (var status (get reply-3 0))
  (val reply-4 (rollout-step SPEC status STOPPED UNKNOWN 11000))
  (:= status (get reply-4 0))
  (assert (= status.unknown-since-ms 11000))
  (setv #(again _) (rollout-step SPEC status STOPPED UNKNOWN 70000))
  (assert (= again.phase "Observing"))
  (assert (is again status))                                ; Unknown の間は status を変えない(版が拍ごとに進まない)
  (val reply-5 (rollout-step SPEC status STOPPED READY 61000))
  (:= status (get reply-5 0))
  (assert (= status.phase "Observing"))
  (assert (= status.phase-since-ms 50000))            ; Unknown の 50 秒だけずらした
  (val reply-6 (rollout-step SPEC status STOPPED READY 111000))
  (:= status (get reply-6 0))
  (assert (= status.phase "Complete")))


(deftest test-complete-needs-a-ready-observation
  (setv #(status _) (rollout-step SPEC (observing 0) STOPPED NOT-READY 61000))
  (assert (= status.phase "Observing")))


(deftest test-unknown-new-while-stopping-old-holds-the-stop
  (setv status (RolloutStatus :phase "StoppingOld" :phase-since-ms 0))
  (setv running-old {"ready" "Ready" "stopped" False "specReplicas" 1 "reason" ""})
  (setv #(after actions) (rollout-step SPEC status running-old UNKNOWN 1000))
  (assert (= after.phase "StoppingOld"))
  (assert (= actions [])))


(deftest test-a-failing-action-is-retried-with-growing-gaps
  (setv action {"op" "scale" "target" SPEC.from-target "replicas" 0})
  (setv failed (RolloutStatus :last-action {"op" "scale" "target" "Deployment:ns/old" "replicas" 0 "ok" False "error" "403" "at" 1000 "count" 3}))
  (assert (= (retry-delay-ms 3) 4000))
  (assert (not (action-due failed action 4999)))
  (assert (action-due failed action 5000))
  (assert (= (retry-delay-ms 30) RETRY-MAX-MS))
  ;; 違う action・成功した直後はすぐ出す
  (assert (action-due failed (| action {"replicas" 1}) 1001))
  (assert (action-due (RolloutStatus :last-action (| failed.last-action {"ok" True})) action 1001)))


(deftest test-a-rollback-that-does-not-finish-is-marked-stuck-and-keeps-the-new
  (setv status (RolloutStatus :phase "RollingBack" :phase-since-ms 0 :rollback-step "restoreOld" :from-replicas 1))
  (setv old-down {"ready" "NotReady" "stopped" False "specReplicas" 1 "reason" "Pod が起きない"})
  (val reply-7 (rollout-step SPEC status old-down READY 1000))
  (var s (get reply-7 0))
  (var actions (get reply-7 1))
  (assert (is s.stuck None))
  (val reply-8 (rollout-step SPEC s old-down READY 601000))
  (:= s (get reply-8 0))
  (:= actions (get reply-8 1))
  (assert (= s.stuck.step "restoreOld"))
  (assert (= actions []))                                   ; 旧の台数は既に 1(命令は出さない)・新は止めない
  (setv #(again _) (rollout-step SPEC s old-down READY 700000))
  (assert (= again.stuck s.stuck))         ; 印は付けた拍だけ変わる
  ;; 計器に出る
  (setv state (ClusterState :rollouts {"r" (RolloutRow :spec SPEC :status s)}))
  (assert (in "doeff_worker_rollout_stuck{rollout=\"r\"} 1" (metrics-text state 700000 T))))
