;;; 要求の無い拍を何拍飛ばしても調停が何も変えないか(純粋な判断・I/O はしない)。
;;;
;;; 調停ループ(coordinator.coordinator-step)は要求が無くても 1 秒ごとに拍を回す(本番の拍の間隔と判断の刻)。模擬の時計の下では、
;;; 仮想の何時間を 1 秒ごとの拍で回すと、何も変えない拍の数が検の所要の大半になる。模擬の要求の列(coordinator_handler_sets の
;;; queued-requests)は、要求が無い間、この判断が「何も変えない」と言う拍の数だけ一度に眠る(2026-09-30 の決定 — 模擬の時計の下だけ・
;;; 本番の拍の間隔 1 秒と判断の刻は変えない)。
;;;
;;; 次の期限を別に見積もらない: k 拍先の拍を、本番の拍と同じ判断の関数(api_policy.tick・deployments-to-observe・plan-rollouts・
;;; mark-alive、cluster_policy.nodes-to-read・with-derived-capabilities、resource_policy.stamp)で 1 拍ずつ試し、状態が変わる・
;;; k8s を読む・action を出す最初の拍で起きる。期限の求め忘れは起こり得ない(判断そのものを試すので)。飛ばした拍は本番でも状態を
;;; 変えないので、起きる刻と、そこでの判断は 1 秒ごとの拍と同じになる(同値の検 = tests/test_idle_skip.hy)。
(require doeff-hy.macros [defk <- val var])
(import dataclasses [replace])
(import doeff_cluster.shared.intent.protocol [ClusterTiming])
(import doeff_cluster.coordinator.intent.cluster_model [ClusterState ClusterNaming IdleProbe])
(import doeff_cluster.coordinator.core.cluster_policy [nodes-to-read with-derived-capabilities])
(import doeff_cluster.coordinator.core.resource_policy [stamp])
(import doeff_cluster.coordinator.core.api_policy [tick plan-rollouts deployments-to-observe mark-alive ROLLOUT-ACTOR ROLLOUT-TICK-MS TICK-MS])

(setv MAX-QUIET-TICKS 60)    ; 一度に飛ばす拍の上限(生きていた時刻の印 ALIVE-MARK-MS が先に来るので、ふだんは 5 拍で止まる)


(defk rollout-quiet [state now timing naming]
  {:pre [(: state ClusterState) (: now int) (: timing ClusterTiming) (: naming ClusterNaming)] :post [(: % (| ClusterState None))]
   :tags {:context "doeff-cluster" :role "judgment"}}
  "now の Rollout の拍(coordinator.rollout-tick)が k8s を読まず action も出さない時の、その拍の後の状態を知るため。読む・出すなら
   None。rollout-tick の純粋な部分と同じ順(観測 → 能力の導出 → plan-rollouts → stamp)。"
  (if (or (deployments-to-observe state now) (nodes-to-read state now))
      None
      (do (val before (with-derived-capabilities state naming.node-capabilities))
          (val planned (plan-rollouts before now timing naming))
          (if (get planned 1) None (stamp before (get planned 0) ROLLOUT-ACTOR now timing)))))


(defk quiet-ticks [probe now]
  {:pre [(: probe IdleProbe) (: now int)] :post [(: % int)] :tags {:context "doeff-cluster" :role "judgment"}}
  "要求が無い間に飛ばしてよい拍の数(1 以上 — 1 拍 = TICK-MS)を知るため。1 拍先から 1 拍ずつ、本番の要求の無い拍(tick → 1 秒ごとの Rollout の
   拍 → mark-alive)を試し、状態が変わる・k8s を読む・action を出す・版の変化の待ちの期限を過ぎる最初の拍までの秒。何も変えない拍は状態を変えないので、次の拍も
   同じ状態から試す(Rollout の拍の刻 rollout-tick-ms だけは、本番と同じく拍ごとに進める — 調停の判断は読まない欄)。"
  (val state probe.state)
  (var last-roll state.rollout-tick-ms)
  (var ticks 1)
  (var found None)
  (while (and (is found None) (<= ticks MAX-QUIET-TICKS))
    (val at (+ now (* ticks TICK-MS)))
    ;; 版の変化を待つ要求(GET /watch)の期限の後の最初の拍は、待ちに「変わっていない」と返す拍 — 飛ばさない(本番の 1 秒の拍が
    ;; 返すのと同じ刻・#1933)。
    (val answers-watch (and (is-not probe.wake-ms None) (>= at probe.wake-ms)))
    (val ticked (tick state at probe.timing))
    (val due (>= (- at last-roll) ROLLOUT-TICK-MS))
    (var rolled ticked)
    (when due
      (<- after (| ClusterState None) (rollout-quiet ticked at probe.timing probe.naming))
      (:= rolled after))
    (if (and (not answers-watch) (is-not rolled None) (= (mark-alive rolled at) state))
        (do (when due (:= last-roll at))
            (:= ticks (+ ticks 1)))
        (:= found ticks)))
  (if (is found None) MAX-QUIET-TICKS found))
