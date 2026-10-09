;; 次に起きる刻の期限(#3064・#3865): 入れ替えの見張り(api_policy.placement-due)と Rollout の歩(wake_policy.rollout-due)は、判断が比べに
;; 使う期限の値(同じ関数)から次の刻を返す。
;; - 返す刻が、本番の判断が比べに使う期限の値と一致する: 入れ替えの諦め(handoff_policy.handoff-deadline・watch-handoffs)と、
;;   Rollout の段の時間切れ(rollout_policy.ready-timeout-from・plan-rollouts)。判断はその刻の 1 ms 前は答えを変えず、その刻に変える。
;;   期限の関数を判断と別に持つ(片方だけ直す)と、刻か判断の切り替わりのどちらかが食い違って赤。
;; - Rollout の歩は毎歩回る(前の Rollout の歩からの間隔の下限は無い — #3868)ので、期限の前の歩の後は期限ちょうどを返し、期限の刻の
;;   歩の後はその期限を判じ済みとして返さない。
(require doeff-hy.macros [deftest defk <- val])
(import doeff [run])
(import doeff_cluster.shared.intent.protocol [ClusterTiming])
(import doeff_cluster.shared.protocol.inbox [http-request])
(import doeff_cluster.coordinator.intent.cluster_model [ClusterNaming ClusterState HandoffPhase])
(import doeff_cluster.coordinator.protocol.request_bodies [responded])
(import doeff_cluster.coordinator.core.api_policy [placement-due plan-rollouts tick ROLLOUT-ACTOR])
(import doeff_cluster.coordinator.core.resource_policy [stamp])
(import tests.program_rows [SAMPLE-RUN])
(import doeff_cluster.coordinator.core.handoff_policy [handoff-deadline watch-handoffs])
(import doeff_cluster.coordinator.core.rollout_policy [ready-timeout-from])
(import doeff_cluster.coordinator.core.wake_policy [rollout-due])
(import doeff_cluster.shared.intent.due_model [DueAt DueNow DueNever])
(import tests.test_handoff_deadline [Sim HANDOFF steps])

;; 担い手の報告が古くならない・readiness の window が諦めの期限より長い時計と宣言(効く期限を諦めの 1 つにする)。
;; 移し替え・約束の在る job の移し替え・忘れる期限は生死の窓より長い(ClusterTiming の順の検め — #3865)ので、生死の窓と一緒に延ばす。
(val LONG-LEASE (ClusterTiming :lease-ms 1000000000 :reassign-after-ms 2000000000 :kept-reassign-after-ms 2500000000
                                :worker-forget-ms 3000000000))
(val WINDOW-OVER-DEADLINE (| HANDOFF {"readiness" {"windowSeconds" 100 "handoffTimeoutSeconds" 30}}))
;; worker の居ない世界の Service 2 つの間の Rollout(新は置けず Ready にならないので、WaitingNewReady の時間切れだけが効く — k8s を読まない)。
(val SERVICE {"revision" "r1" "needs" ["net"] "run" SAMPLE-RUN "replicas" 1})
(val SERVICE-ROLLOUT {"from" {"kind" "Service" "name" "writer-a"} "to" {"kind" "Service" "name" "writer-b"}
                      "readyTimeoutSeconds" 60 "stopTimeoutSeconds" 90 "observeSeconds" 120 "failAfterSeconds" 15})
(val ROLLOUT-START 1000000)


(deftest test-the-placement-due-is-the-handoff-deadline-the-watch-compares
  ;; 入れ替え: 新の世代(r2)が Ready にならず見張りが Ready を待っている状態で、placement-due は諦めの期限 handoff-deadline を返し、
  ;; 本番の判断 watch-handoffs はその刻の 1 ms 前は待ったまま、その刻に諦める。
  (val sim (Sim WINDOW-OVER-DEADLINE))
  (<- (steps sim 12))
  (sim.mark-broken "r2")
  (sim.redeclare (| WINDOW-OVER-DEADLINE {"revision" "r2"}))
  ;; r2 の process が起きて準備の報告(偽)を送り始めるまで進める(報告の無い間の window の終わりを効かせない)。
  (<- (steps sim 10))
  (val state sim.state)
  (val watch (get state.handoffs "writer-a"))
  (assert (= watch.phase HandoffPhase.WAITING) watch)
  (val job (next (gfor j state.jobs :if (= j.spec.name "writer-a") j)))
  (<- due (| int None) (placement-due state sim.now LONG-LEASE))
  (assert (= due (handoff-deadline watch job)) #(due watch))
  (assert (> due sim.now) #(due sim.now))
  (val before (watch-handoffs (- due 1) state LONG-LEASE))
  (val at (watch-handoffs due state LONG-LEASE))
  (assert (= (. (get before.handoffs "writer-a") phase) HandoffPhase.WAITING) before.handoffs)
  (assert (= (. (get at.handoffs "writer-a") phase) HandoffPhase.ABANDONED) at.handoffs))


(defk waiting-rollout []
  {:pre [] :post [(: % ClusterState)] :tags {:context "doeff-cluster-test" :role "program"}}
  "worker の居ない世界に Service 2 つとその間の Rollout を作り、要求の無い拍と Rollout の拍を 1 つずつ回して WaitingNewReady に入れた
   状態を返すため(Rollout の期限の検の出発点 — 歩の判断の不動点)。"
  (val made-a (responded (ClusterState :started-ms (- ROLLOUT-START 60000))
                         (! (http-request "POST" "/resources/Service" {} {"name" "writer-a" "spec" SERVICE} :actor "c-test"))
                         ROLLOUT-START (ClusterTiming)))
  (val made-b (responded (get made-a 0)
                         (! (http-request "POST" "/resources/Service" {} {"name" "writer-b" "spec" SERVICE} :actor "c-test"))
                         ROLLOUT-START (ClusterTiming)))
  (val made (responded (get made-b 0)
                       (! (http-request "POST" "/resources/Rollout" {} {"name" "to-b" "spec" SERVICE-ROLLOUT} :actor "c-test"))
                       ROLLOUT-START (ClusterTiming)))
  (assert (all (gfor m [made-a made-b made] (< (get m 1) 300))) #((get made-a 2) (get made-b 2) (get made 2)))
  (<- ticked ClusterState (tick (get made 0) ROLLOUT-START (ClusterTiming)))
  (<- planned tuple (plan-rollouts ticked ROLLOUT-START (ClusterTiming) (ClusterNaming)))
  (assert (= (get planned 1) []) planned)
  (val state (stamp ticked (get planned 0) ROLLOUT-ACTOR ROLLOUT-START (ClusterTiming)))
  (assert (= (. (get state.rollouts "to-b") status phase) "WaitingNewReady") (get state.rollouts "to-b"))
  state)


(deftest test-the-rollout-due-is-the-stage-deadline-the-step-compares
  ;; Rollout: WaitingNewReady の Rollout で、rollout-due は段の時間切れ ready-timeout-from を返し、本番の判断 plan-rollouts は
  ;; その刻の 1 ms 前は待ったまま、その刻に戻し(RollingBack)へ入る。
  (<- state ClusterState (waiting-rollout))
  (val row (get state.rollouts "to-b"))
  (<- answer (| DueAt DueNow DueNever) (rollout-due state ROLLOUT-START (ClusterTiming) (ClusterNaming)))
  (assert (= answer (DueAt :at (ready-timeout-from row.spec row.status.phase-since-ms))) #(answer row.status))
  (val due answer.at)
  (<- before tuple (plan-rollouts state (- due 1) (ClusterTiming) (ClusterNaming)))
  (<- at tuple (plan-rollouts state due (ClusterTiming) (ClusterNaming)))
  (assert (= (. (get (. (get before 0) rollouts) "to-b") status phase) "WaitingNewReady") (get before 0))
  (assert (= (. (get (. (get at 0) rollouts) "to-b") status phase) "RollingBack") (get at 0)))


(deftest test-the-rollout-due-is-the-deadline-itself-without-a-rollout-interval
  ;; Rollout の歩は毎歩回る(#3868)ので、期限の 500 ms 前の歩の後の rollout-due は期限ちょうどを返す(1 秒の格子へ遅らせない)。
  ;; 期限の刻の歩はその期限を判じたので、その歩の後は期限の刻より後だけを返す(期限の刻の答えに戻らない)。
  (<- state ClusterState (waiting-rollout))
  (val row (get state.rollouts "to-b"))
  (val due (ready-timeout-from row.spec row.status.phase-since-ms))
  (<- early (| DueAt DueNow DueNever) (rollout-due state (- due 500) (ClusterTiming) (ClusterNaming)))
  (assert (= early (DueAt :at due)) #(early due))
  (<- judged (| DueAt DueNow DueNever) (rollout-due state due (ClusterTiming) (ClusterNaming)))
  (assert (not (and (isinstance judged DueAt) (<= judged.at due))) judged))

