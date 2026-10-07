;;; Rollout と heartbeat の穴(2026-09-25)。test_rollout の小さな世界(Sim)の上で:
;;;   - coordinator が止まっていた時間を段の時間に数えない(起動の時に止まっていた長さだけ時計をずらす)
;;;   - 観察(Observing)の間の Unknown(担い手の heartbeat の途絶)は完了にも失敗にも数えない
;;;   - 失敗が続く action は間を空けて出す(毎秒の送り直しをしない)
;;;   - 戻し(RollingBack)が終わらない時は stuck の印を出し、新は止めない
(require doeff-hy.macros [deftest defk <- val var])
(import doeff [run])
(import dataclasses [replace])
(import doeff_cluster.shared.intent.protocol [ClusterTiming Request])
(import doeff_cluster.coordinator.intent.cluster_model [ClusterState RolloutRow RolloutStatus TaskRecord TargetView DeploymentUnreadable])
(import doeff_cluster.shared.protocol.inbox [http-request])
(import doeff_cluster.coordinator.protocol.durable_kv [full-kv state-from-kv])
(import doeff_cluster.coordinator.core.api_policy [plan-rollouts record-action resume-after-downtime mark-alive ALIVE-MARK-MS])
(import doeff_cluster.coordinator.core.wake_policy [rollout-due])
(import doeff_cluster.coordinator.intent.cluster_model [ClusterNaming])
(import doeff_cluster.shared.intent.due_model [DueAt DueNow DueNever])
(import doeff_cluster.coordinator.protocol.request_bodies [responded])
(import doeff_cluster.coordinator.core.rollout_policy [validate-rollout-spec rollout-step action-due retry-delay-ms shift-clocks RETRY-MAX-MS])
(import doeff_cluster.coordinator.core.metrics_policy [metrics-text])
(import tests.test_rollout [Sim FORWARD DEP phases-of])

(setv T (ClusterTiming))


(defk restart-after [sim seconds]
  {:pre [(: sim Sim) (: seconds int)] :post [(: % int)] :tags {:context "doeff-cluster-test" :role "entry"}}
  "coordinator が seconds 秒止まってから、耐久の置き場(key の表)から起き直すため。止まっている間も世界(Pod・worker の process)は進む。
   答え = ずらした止まっていた長さ(ms)。"
  (<- kv dict (full-kv (! (mark-alive sim.state sim.now))))
  (for [_ (range seconds)]
    (+= sim.now 1000)
    (sim.advance-pods))
  (<- stored ClusterState (state-from-kv kv sim.now))
  (val resumed (resume-after-downtime stored sim.now))
  (setv sim.state (get resumed 0))
  (get resumed 1))


(deftest test-downtime-in-the-middle-of-observing-is-not-counted
  ;; 観察 120 秒のうち 30 秒が過ぎた所で coordinator が 10 分止まった。起き直した直後に「観察の期間を終えた」と言わない。
  (setv sim (Sim))
  (sim.rollout "to-worker" FORWARD)
  (sim.run-until "to-worker" #("Observing"))
  (for [_ (range 30)] (sim.step))
  (setv since (. (get sim.state.rollouts "to-worker") status phase-since-ms))
  (<- gap int (restart-after sim 600))
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
  (setv s (! (mark-alive (ClusterState) 10000)))
  (assert (= s.alive-ms 10000))
  (assert (is (! (mark-alive s (+ 10000 (- ALIVE-MARK-MS 1)))) s))
  (assert (= (. (! (mark-alive s (+ 10000 ALIVE-MARK-MS))) alive-ms) (+ 10000 ALIVE-MARK-MS))))


(deftest test-a-worker-row-moves-only-at-the-mark-and-reloads-the-mark
  ;; #2903(seen-marks を WorkerInfo の欄へ)の前後で同じ答え: heartbeat だけでは worker/<名> の行は変わらず(書きは印の拍ごと)、
  ;; 印の拍で連絡のあった worker の行だけが新しい lastSeenMs を持ち、沈黙した worker の行は変わらない。読み直すと、最後の連絡の
  ;; 時刻は保存した印の時刻になり、読み直した状態の保存の鍵は元と同じ。heartbeat は本物の受け口(POST /heartbeat)を通す — 受け口は
  ;; heartbeat ごとに WorkerInfo を作り直すので、印を運び忘れると heartbeat のたびに行から lastSeenMs が消える(#2903 で一度そうなった)。
  (val beat (fn [state name now]
              (get (responded state (run (http-request "POST" "/heartbeat" {} {"name" name "provides" ["net"] "capacity" 1 "taskReserve" 0 "statuses" []}
                                                  :actor "c-test"))
                              now T)
                   0)))
  (val workers-of (fn [kv] (dfor #(k v) (.items kv) :if (.startswith k "worker/") k v)))
  (val marked (! (mark-alive (beat (beat (ClusterState) "w1" 10000) "w2" 10000) 10000)))
  (<- at-mark dict (full-kv marked))
  (assert (= #((get at-mark "worker/w1" "lastSeenMs") (get at-mark "worker/w2" "lastSeenMs")) #(10000 10000)) at-mark)
  (val beaten (beat marked "w1" 12000))
  (<- after-beat dict (full-kv beaten))
  (assert (= (workers-of after-beat) (workers-of at-mark)) "heartbeat だけで worker の保存の行が変わった")
  (val next-mark (! (mark-alive beaten (+ 10000 ALIVE-MARK-MS))))
  (<- at-next dict (full-kv next-mark))
  (assert (= (get at-next "worker/w1" "lastSeenMs") 12000) at-next)
  (assert (= (get at-next "worker/w2") (get at-mark "worker/w2")) "沈黙した worker の行が変わった")
  (<- reloaded ClusterState (state-from-kv at-next 20000))
  (assert (= (. (get reloaded.workers "w1") last-seen-ms) 12000))
  (<- again dict (full-kv reloaded))
  (assert (= (workers-of again) (workers-of at-next))))


(setv SPEC (validate-rollout-spec {"from" {"kind" "Deployment" "namespace" "ns" "name" "old" "replicas" None "dryRun" False}
                                   "to" {"kind" "Service" "name" "new"}
                                   "readyTimeoutSeconds" 300 "stopTimeoutSeconds" 180 "observeSeconds" 60 "failAfterSeconds" 30
                                   "rollbackTimeoutSeconds" 600 "markDeployment" False "abort" False}))
(setv STOPPED (TargetView :ready "NotReady" :stopped True :spec-replicas 0 :reason "止まっている"))
;; Service から Deployment ns/app-a へ移し終えて台数を持ち、Deployment に印の annotation を置く Rollout の spec。
(val OWNER-SPEC (validate-rollout-spec {"from" {"kind" "Service" "name" "old"}
                                         "to" {"kind" "Deployment" "namespace" "ns" "name" "app-a" "replicas" 1 "dryRun" False}
                                         "readyTimeoutSeconds" 300 "stopTimeoutSeconds" 180 "observeSeconds" 60 "failAfterSeconds" 30
                                         "rollbackTimeoutSeconds" 600 "markDeployment" True "abort" False}))
;; action-due が書きの後の観測かを読む相手の Deployment の観測 — まだ観測していない(None)。
(val NO-OBSERVATION None)


(defk observed-at [at]
  {:pre [(: at int)] :post [(: % DeploymentUnreadable)] :tags {:context "doeff-cluster-test" :role "judgment"}}
  "Deployment ns/old を刻 at に観測した(見張りが伝えた)観測を作るため。"
  (DeploymentUnreadable :error "検の観測" :at at))
(setv READY (TargetView :ready "Ready" :stopped False :spec-replicas 1 :reason ""))
(setv UNKNOWN (TargetView :ready "Unknown" :stopped None :spec-replicas None :reason "担い手の報告が古い"))
(setv NOT-READY (TargetView :ready "NotReady" :stopped False :spec-replicas 1 :reason "拍が落ちた"))


(defk observing [since]
  {:pre [(: since int)] :post [(: % RolloutStatus)] :tags {:context "doeff-cluster-test" :role "entry"}}
  "時刻 since から観察(Observing)に入った rollout の状態を、検の出発点として組むため。"
  (RolloutStatus :phase "Observing" :phase-since-ms since))


(deftest test-unknown-while-observing-neither-completes-nor-fails
  ;; 観察 60 秒のうち 50 秒を Unknown で過ごしても、完了も失敗もしない。Unknown の長さだけ観察を延ばす。
  (val reply-3 (rollout-step SPEC (! (observing 0)) STOPPED READY 10000))
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
  (setv #(status _) (rollout-step SPEC (! (observing 0)) STOPPED NOT-READY 61000))
  (assert (= status.phase "Observing")))


(deftest test-unknown-new-while-stopping-old-holds-the-stop
  (setv status (RolloutStatus :phase "StoppingOld" :phase-since-ms 0))
  (setv running-old (TargetView :ready "Ready" :stopped False :spec-replicas 1 :reason ""))
  (setv #(after actions) (rollout-step SPEC status running-old UNKNOWN 1000))
  (assert (= after.phase "StoppingOld"))
  (assert (= actions [])))


(deftest test-a-failing-action-is-retried-with-growing-gaps
  (setv action {"op" "scale" "target" SPEC.from-target "replicas" 0})
  (setv failed (RolloutStatus :last-action {"op" "scale" "target" "Deployment:ns/old" "replicas" 0 "ok" False "error" "403" "at" 1000 "count" 3}))
  (assert (= (retry-delay-ms 3) 4000))
  (assert (not (action-due failed action 4999 NO-OBSERVATION)))
  (assert (action-due failed action 5000 NO-OBSERVATION))
  (assert (= (retry-delay-ms 30) RETRY-MAX-MS))
  ;; 違う action はすぐ出す
  (assert (action-due failed (| action {"replicas" 1}) 1001 NO-OBSERVATION)))


(deftest test-a-written-deployment-action-waits-for-an-observation-after-the-write
  ;; 成功した同じ書き(相手が Deployment)は、その書きの後の観測が届くまで出し直さない(見張りが書きの結果を伝える前に同じ書きを
  ;; 重ねない — #3868)。書いた刻 1000 の観測(書きより前に届いた物)と観測が無い間は出さず、1001 の観測が届けば出す。
  ;; 相手が Service の書きは coordinator の状態の中の書き(次の判断がすぐ読む)なので、成功の直後もすぐ出す。
  (val action {"op" "scale" "target" SPEC.from-target "replicas" 0})
  (val written (RolloutStatus :last-action {"op" "scale" "target" "Deployment:ns/old" "replicas" 0 "ok" True "at" 1000 "count" 1}))
  (assert (not (action-due written action 1000 (! (observed-at 1000)))))
  (assert (not (action-due written action 9000 NO-OBSERVATION)))
  (assert (action-due written action 1001 (! (observed-at 1001))))
  (val service-action {"op" "scale" "target" SPEC.to-target "replicas" 1})
  (val service-written (RolloutStatus :last-action {"op" "scale" "target" "Service:new" "replicas" 1 "ok" True "at" 1000 "count" 1}))
  (assert (action-due service-written service-action 1000 NO-OBSERVATION)))


(deftest test-a-repeated-action-is-dated-at-its-last-attempt
  ;; 同じ action を続けて実行した時、lastAction の at は最後に実行した刻(数と一緒に進む — #3868 のレビュー)。出し直しの間は最後の失敗
  ;; から倍々に数え、成功した書きは最後の書きの後の観測を待つ。at が最初の刻のままだと、間の上限(60 秒)を越えた後と、2 度目の成功の
  ;; 後は毎歩出し直しになり、調停ループが落ち着かない(Rollout の歩の 1 秒の間隔がこれを隠していた)。
  (val action {"rollout" "r" "op" "scale" "target" SPEC.from-target "replicas" 0})
  (val state (ClusterState :rollouts {"r" (RolloutRow :spec SPEC :status (RolloutStatus :phase "StoppingOld" :phase-since-ms 0))}))
  (val twice (record-action (record-action state action False "403" 1000) action False "403" 3000))
  (val failed (. (get twice.rollouts "r") status))
  (assert (= #((get failed.last-action "at") (get failed.last-action "count")) #(3000 2)) failed.last-action)
  (assert (not (action-due failed action (+ 3000 (retry-delay-ms 2) -1) NO-OBSERVATION)))
  (assert (action-due failed action (+ 3000 (retry-delay-ms 2)) NO-OBSERVATION))
  (val written (. (get (. (record-action (record-action state action True None 1000) action True None 5000) rollouts) "r") status))
  (assert (= (get written.last-action "at") 5000) written.last-action)
  ;; 2 度目の書き(5000)より前の観測では、3 度目を出さない。
  (assert (not (action-due written action 6000 (! (observed-at 4000))))))


(deftest test-a-failing-annotation-waits-for-the-retry-gap
  ;; markDeployment の Rollout が台数を持つ Deployment に印の annotation を置けない(403)時、plan-rollouts は失敗の間(1 秒から倍々)を
  ;; 空けて出し直し、調停ループはその刻に起きる(終わった Rollout でも)。間を空けないと失敗の記録で状態が毎歩変わり、調停ループが
  ;; 落ち着かない(#3868 のレビュー — 以前は Rollout の歩の 1 秒の間隔が隠していた)。
  (val state (ClusterState :rollouts {"to-a" (RolloutRow :spec OWNER-SPEC
                                                          :status (RolloutStatus :phase "Complete" :stopped-old-ms 500 :completed-ms 500))}))
  (<- first tuple (plan-rollouts state 1000 T))
  (val marks (lfor a (get first 1) :if (= (get a "op") "annotate") a))
  (assert (= (len marks) 1) first)
  (val failed (record-action state (get marks 0) False "403" 1000))
  (<- early tuple (plan-rollouts failed 1500 T))
  (<- late tuple (plan-rollouts failed 2000 T))
  (assert (= (lfor a (get early 1) :if (= (get a "op") "annotate") a) []) early)
  (assert (= (len (lfor a (get late 1) :if (= (get a "op") "annotate") a)) 1) late)
  (<- due (| DueAt DueNow DueNever) (rollout-due failed 1000 T (ClusterNaming)))
  (assert (= due (DueAt :at 2000)) due))


(deftest test-a-rollback-that-does-not-finish-is-marked-stuck-and-keeps-the-new
  (setv status (RolloutStatus :phase "RollingBack" :phase-since-ms 0 :rollback-step "restoreOld" :from-replicas 1))
  (setv old-down (TargetView :ready "NotReady" :stopped False :spec-replicas 1 :reason "Pod が起きない"))
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
