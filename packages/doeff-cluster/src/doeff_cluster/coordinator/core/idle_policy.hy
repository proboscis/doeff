;;; 要求の無い間の静かな区間で、1 拍ずつの走りの調停の歩が何を変えるか(純粋な判断・I/O はしない)。
;;;
;;; 調停ループ(coordinator.coordinator-step)は要求が無くても 1 秒ごとに歩を回す(本番の拍の間隔と判断の刻)。模擬の時計の下では、
;;; 仮想の何時間を 1 秒ごとの歩で回すと、何も変えない歩の数が検の所要の大半になる。模擬の要求の列(coordinator_handler_sets の
;;; queued-requests)は、要求が無い間、この判断が「静か」と言う歩を一度に眠り、起きた時に眠った間の歩を調停ループへ渡す(2026-09-30 の
;;; 決定・#2790 — 模擬の時計の下だけ・本番の拍の間隔 1 秒と判断の刻は変えない)。
;;;
;;; 次の期限を別に見積もらない: 1 拍ずつの走りと同じ刻(直前の歩 + 1 拍)の歩を、本番の歩と同じ判断の関数(api_policy.tick・
;;; rollout-quiet・mark-alive、watch_policy.settle-watch)で 1 歩ずつ試し、状態が変わる・k8s を読む・action を出す・待ちに「変わった」と
;;; 答える最初の歩で区間を切る。期限の求め忘れは起こり得ない(判断そのものを試すので)。静かな歩に許す変化は 2 つだけ(#2790):
;;; - 生存の印(mark-alive の alive-ms と WorkerInfo.seen-mark)と Rollout の拍の刻 — 歩の後の状態を QuietStep に持ち、調停ループが起きた時に
;;;   同じ順・同じ値で保存する(置き場の書きの列は 1 拍ずつの走りと同じ)。
;;; - 期限の来た名指しの待ちへの「変わっていない」の返事と、送り手(worker の宿の待ち)の送り直し — 同じ問いを同じ刻に送り直すので、
;;;   待ちの期限と見え方をその刻で引き直して吸う(同値の検 = tests/test_idle_skip.hy)。
(require doeff-hy.macros [defk <- val var])
(import dataclasses [replace])
(import math [ceil])
(import doeff_cluster.shared.intent.protocol [ClusterTiming])
(import doeff_cluster.coordinator.intent.cluster_model [ClusterState ClusterNaming IdleProbe QuietStep QuietStretch Watcher WatchStep])
(import doeff_cluster.coordinator.core.cluster_policy [nodes-to-read with-derived-capabilities])
(import doeff_cluster.coordinator.core.resource_policy [stamp])
(import doeff_cluster.coordinator.core.api_policy [tick plan-rollouts deployments-to-observe mark-alive ROLLOUT-ACTOR ROLLOUT-TICK-MS TICK-MS])
(import doeff_cluster.coordinator.core.watch_policy [settle-watch])

(val MAX-QUIET-MS 3600000)    ; 一度に眠る区間の上限(仮想の 1 時間 — その刻の歩は静かでも本物の歩として回す)


(defk rollout-quiet [state now timing naming]
  {:pre [(: state ClusterState) (: now int) (: timing ClusterTiming) (: naming ClusterNaming)] :post [(: % (| ClusterState None))]
   :tags {:context "coordinator" :role "judgment"}}
  "now の Rollout の拍(coordinator.rollout-tick)が k8s を読まず action も出さない時の、その拍の後の状態を知るため。読む・出すなら
   None。rollout-tick の純粋な部分と同じ順(観測 → 能力の導出 → plan-rollouts → stamp)。"
  (if (or (deployments-to-observe state now) (nodes-to-read state now))
      None
      (do (val before (with-derived-capabilities state naming.node-capabilities))
          (val planned (plan-rollouts before now timing naming))
          (if (get planned 1) None (stamp before (get planned 0) ROLLOUT-ACTOR now timing)))))


(defk absorbable [watcher state]
  {:pre [(: watcher Watcher) (: state ClusterState)] :post [(: % bool)] :tags {:context "coordinator" :role "judgment"}}
  "期限の来た待ちが、1 拍ずつの走りで送り手が同じ問いを同じ刻に送り直す物か(区間の中で吸ってよいか)を知るため: worker を名指し、待つ
   秒が 0 でなく、問いの after が今の版と同じ待ち。宿の名指しの待ちは「変わっていない」の返事を受けた刻に、自分の知る版(宿の真実の
   watch-after)を after にして送り直す。問いの after が今の版なら、版はその後動いておらず(版は増えるだけ)、宿の知る版も同じなので、
   送り直しは同じ問いになる。版が動いた後の待ちは、宿の知る版が古いままなら送り直しを「変わった」と即座に返す(1 拍ずつの走りの実測 —
   coordinator には宿の知る版が見えない)ので吸わない。確かめる前の 0 秒の待ちと、worker を名指さない読み手の待ちも吸わない。"
  (and (is-not watcher.worker None) (> watcher.seconds 0.0) (= watcher.asked state.revision)))


(defk renewed-watchers [watchers state at timing]
  {:pre [(: watchers tuple) (: state ClusterState) (: at int) (: timing ClusterTiming)] :post [(: % (| tuple None))]
   :tags {:context "coordinator" :role "judgment"}}
  "歩の後の待ちを、1 拍ずつの走りの歩と同じ判断(settle-watch)で判じるため。期限の来ていない待ちはそのまま並べ、期限の来た吸える待ちは
   送り直した待ち(版 = 今の版・期限 = at から引き直し・見え方は at で覚え直す — 1 拍ずつの走りで送り直しを受けた歩と同じ値)を後ろへ
   並べる(返事の順)。「変わった」と答える待ちか、吸えない期限の待ちが在れば None(その歩は静かでない)。"
  (var kept #())
  (var again #())
  (var quiet True)
  (for [watcher watchers]
    (when quiet
      (<- judged WatchStep (settle-watch watcher state at timing))
      (cond
        (is judged.answer None) (:= kept (+ kept #(judged.watcher)))
        judged.answer.changed (:= quiet False)
        True (do (<- can bool (absorbable watcher state))
                 (if can
                     (do (<- held WatchStep (settle-watch (replace watcher :after state.revision :mark None
                                                                           :deadline-ms (+ at (int (* 1000 watcher.seconds))))
                                                          state at timing))
                         (:= again (+ again #(held.watcher))))
                     (:= quiet False))))))
  (if quiet (+ kept again) None))


(defk quiet-step [before at timing naming]
  {:pre [(: before QuietStep) (: at int) (: timing ClusterTiming) (: naming ClusterNaming)] :post [(: % (| QuietStep None))]
   :tags {:context "coordinator" :role "judgment"}}
  "before の歩の後、at の刻の要求の無い歩(1 拍ずつの走りの coordinator-step と同じ順: tick → Rollout の拍 → mark-alive → 待ちの判じ)が
   静かなら、その歩の後の状態と待ちを知るため。静かでなければ None。静か = tick が何も変えず、Rollout の拍(1 秒ごと)が k8s を読まず
   action も出さず何も変えず、待ちが「変わった」と答えず期限の待ちを吸える歩(変わってよいのは生存の印と Rollout の拍の刻だけ)。"
  (val state before.state)
  (var rolled None)
  (when (= (tick state at timing) state)
    (if (>= (- at state.rollout-tick-ms) ROLLOUT-TICK-MS)
        (do (<- after (| ClusterState None) (rollout-quiet state at timing naming))
            (when (= after state)
              (:= rolled (replace state :rollout-tick-ms at))))
        (:= rolled state)))
  (if (is rolled None)
      None
      (do (val marked (mark-alive rolled at))
          (<- held (| tuple None) (renewed-watchers before.watchers marked at timing))
          (if (is held None)
              None
              (QuietStep :at at :state marked :watchers held :marked (!= marked.alive-ms rolled.alive-ms))))))


(defk quiet-stretch [probe start horizon]
  {:pre [(: probe IdleProbe) (: start QuietStep) (: horizon int)] :post [(: % QuietStretch)]
   :tags {:context "coordinator" :role "judgment"}}
  "start の歩の後から、1 拍ずつの走りと同じ刻(直前の歩 + 1 拍)の要求の無い歩を、horizon の刻まで本番の判断で 1 歩ずつ試すため。
   答え = 静かだった歩の列と、最初の静かでない歩の刻(horizon までに無ければ None)。静かな歩は次の歩の起点になる(生存の印と
   引き直した待ちを持ち越す)。"
  (var last start)
  (var steps #())
  (var end None)
  (while (and (is end None) (<= (+ last.at TICK-MS) horizon))
    (val at (+ last.at TICK-MS))
    (<- stepped (| QuietStep None) (quiet-step last at probe.timing probe.naming))
    (if (is stepped None)
        (:= end at)
        (do (:= steps (+ steps #(stepped)))
            (:= last stepped))))
  (QuietStretch :steps steps :end-at end))


(defk rest-to-tick [elapsed-ms quiet-ms]
  {:pre [(: elapsed-ms int) (: quiet-ms int)] :post [(: % int)] :tags {:context "coordinator" :role "judgment"}}
  "要求ではない出来事で起こされた取り手が、本番の 1 秒の拍がその出来事に気づく刻(眠り始めから整数秒・1 秒以上・飛ばしてよい長さ
   まで)まで、あと何 ms 眠るかを知るため。"
  (- (min quiet-ms (max TICK-MS (* TICK-MS (ceil (/ elapsed-ms TICK-MS))))) elapsed-ms))
