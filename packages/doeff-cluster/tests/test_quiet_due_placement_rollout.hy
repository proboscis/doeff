;; 静かな区間の次の期限 5/5(#3064): 入れ替えの見張り(api_policy.placement-due)と Rollout の拍(idle_policy.rollout-due)は、判断が比べに
;; 使う期限の値(同じ関数)から次の刻を返す。
;; - (1) 返す刻が、本番の判断が比べに使う期限の値と一致する: 入れ替えの諦め(handoff_policy.handoff-deadline・watch-handoffs)と、
;;   Rollout の段の時間切れ(rollout_policy.ready-timeout-from・plan-rollouts)。判断はその刻の 1 ms 前は答えを変えず、その刻に変える。
;;   期限の関数を判断と別に持つ(片方だけ直す)と、刻か判断の切り替わりのどちらかが食い違って赤。
;; - (2) 期限より前の書き(5 秒ごとの生存の印)は飛び越さず、その刻に起きる: 次の期限まで試さずに進めた区間が、1 秒ごとに本番の判断で
;;   試した区間と、歩の列(刻・状態・生存の印)も区間の終わりの刻も同じ。期限を遅く返すと区間の終わりが食い違って赤。
(require doeff-hy.macros [deftest defk <- val])
(import dataclasses [replace])
(import doeff [run])
(import doeff_cluster.shared.intent.protocol [ClusterTiming])
(import doeff_cluster.shared.protocol.inbox [http-request])
(import doeff_cluster.coordinator.intent.cluster_model [ClusterNaming ClusterState HandoffPhase IdleProbe QuietStep QuietStretch])
(import doeff_cluster.coordinator.protocol.request_bodies [responded])
(import doeff_cluster.coordinator.core.api_policy [placement-due plan-rollouts tick ROLLOUT-ACTOR])
(import doeff_cluster.coordinator.core.resource_policy [stamp])
(import tests.program_rows [SAMPLE-RUN])
(import doeff_cluster.coordinator.core.handoff_policy [handoff-deadline watch-handoffs])
(import doeff_cluster.coordinator.core.rollout_policy [ready-timeout-from])
(import doeff_cluster.coordinator.core.idle_policy [rollout-due quiet-stretch])
(import tests.test_handoff_deadline [Sim HANDOFF steps])
(import tests.test_idle_skip [tried-steps])

;; 担い手の報告が古くならない・readiness の window が諦めの期限より長い時計と宣言(効く期限を諦めの 1 つにする)。
(val LONG-LEASE (ClusterTiming :lease-ms 1000000000))
(val WINDOW-OVER-DEADLINE (| HANDOFF {"readiness" {"windowSeconds" 100 "handoffTimeoutSeconds" 30}}))
;; worker の居ない世界の Service 2 つの間の Rollout(新は置けず Ready にならないので、WaitingNewReady の時間切れだけが効く — k8s を読まない)。
(val SERVICE {"revision" "r1" "needs" ["net"] "run" SAMPLE-RUN "replicas" 1})
(val SERVICE-ROLLOUT {"from" {"kind" "Service" "name" "writer-a"} "to" {"kind" "Service" "name" "writer-b"}
                      "readyTimeoutSeconds" 60 "stopTimeoutSeconds" 90 "observeSeconds" 120 "failAfterSeconds" 15})
(val ROLLOUT-START 1000000)


(deftest test-the-placement-due-is-the-handoff-deadline-the-watch-compares
  ;; (1) 入れ替え: 新の世代(r2)が Ready にならず見張りが Ready を待っている状態で、placement-due は諦めの期限 handoff-deadline を返し、
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
   状態を返すため((1) と (2) の出発点 — 試した歩の後の状態と同じく、拍の判断の不動点)。"
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
  (val state (replace (stamp ticked (get planned 0) ROLLOUT-ACTOR ROLLOUT-START (ClusterTiming)) :rollout-tick-ms ROLLOUT-START))
  (assert (= (. (get state.rollouts "to-b") status phase) "WaitingNewReady") (get state.rollouts "to-b"))
  state)


(deftest test-the-rollout-due-is-the-stage-deadline-the-step-compares
  ;; (1) Rollout: WaitingNewReady の Rollout で、rollout-due は段の時間切れ ready-timeout-from を返し、本番の判断 plan-rollouts は
  ;; その刻の 1 ms 前は待ったまま、その刻に戻し(RollingBack)へ入る。
  (<- state ClusterState (waiting-rollout))
  (val row (get state.rollouts "to-b"))
  (<- due (| int None) (rollout-due state ROLLOUT-START (ClusterTiming) (ClusterNaming)))
  (assert (= due (ready-timeout-from row.spec row.status.phase-since-ms)) #(due row.status))
  (<- before tuple (plan-rollouts state (- due 1) (ClusterTiming) (ClusterNaming)))
  (<- at tuple (plan-rollouts state due (ClusterTiming) (ClusterNaming)))
  (assert (= (. (get (. (get before 0) rollouts) "to-b") status phase) "WaitingNewReady") (get before 0))
  (assert (= (. (get (. (get at 0) rollouts) "to-b") status phase) "RollingBack") (get at 0)))


(deftest test-writes-before-the-deadline-happen-at-their-ticks
  ;; (2) WaitingNewReady の Rollout の状態から 2 分の静かな区間: 次の期限(段の時間切れ)まで試さずに進めた区間は、1 秒ごとに本番の判断で
  ;; 試した区間と、歩の列(5 秒ごとの生存の印を含む)も、区間の終わり(時間切れの後の最初の拍)も同じ。
  (<- state ClusterState (waiting-rollout))
  (val start (QuietStep :at ROLLOUT-START :state state :watchers #() :marked False))
  (val horizon (+ ROLLOUT-START 120000))
  (<- skipped QuietStretch (quiet-stretch (IdleProbe state (ClusterTiming) (ClusterNaming)) start horizon))
  (<- tried QuietStretch (tried-steps start horizon))
  (assert (= skipped.end-at tried.end-at (+ ROLLOUT-START 61000)) #(skipped.end-at tried.end-at))
  (assert (= (len skipped.steps) (len tried.steps)) #((len skipped.steps) (len tried.steps)))
  (val apart (lfor #(a b) (zip skipped.steps tried.steps) :if (!= a b) #(a.at a.state.alive-ms b.state.alive-ms)))
  (assert (= apart []) apart)
  (assert (any (gfor step skipped.steps step.marked)) "区間の中で生存の印が書かれている"))
