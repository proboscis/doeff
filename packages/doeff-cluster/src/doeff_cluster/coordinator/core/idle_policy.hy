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
(import collections.abc [Callable])
(import dataclasses [replace])
(import doeff_cluster.shared.intent.protocol [ClusterTiming])
(import doeff_cluster.coordinator.intent.cluster_model [ClusterState ClusterNaming IdleProbe QuietStep QuietStretch Watcher WatchStep
                                                       HeartbeatReply])
(import doeff_cluster.coordinator.core.cluster_policy [nodes-to-read with-derived-capabilities])
(import doeff_cluster.coordinator.core.resource_policy [stamp])
(import doeff_cluster.coordinator.core.api_policy [tick respond plan-rollouts deployments-to-observe mark-alive ROLLOUT-ACTOR ROLLOUT-TICK-MS TICK-MS])
(import doeff_cluster.coordinator.core.watch_policy [settle-watch all-waiting-unchanged])

(val MAX-QUIET-MS 3600000)    ; 一度に眠る区間の上限(仮想の 1 時間 — その刻の歩は静かでも本物の歩として回す)


(defk rollout-quiet [state now timing naming]
  {:pre [(: state ClusterState) (: now int) (: timing ClusterTiming) (: naming ClusterNaming)] :post [(: % (| ClusterState None))]
   :tags {:context "coordinator" :role "judgment"}}
  "now の Rollout の拍(coordinator.rollout-tick)が k8s を読まず action も出さない時の、その拍の後の状態を知るため。読む・出すなら
   None。rollout-tick の純粋な部分と同じ順(観測 → 能力の導出 → plan-rollouts → stamp)。"
  (if (or (deployments-to-observe state now) (nodes-to-read state now))
      None
      (do (val before (with-derived-capabilities state naming.node-capabilities))
          (<- planned tuple (plan-rollouts before now timing naming))
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
   並べる(返事の順)。「変わった」と答える待ちか、吸えない期限の待ちが在れば None(その歩は静かでない)。
   どの待ちにも settle-watch が答えない歩(版が動かず期限も来ていない — watch_policy.all-waiting-unchanged)は、1 件ずつ判じずに
   そのまま持ち越す(答えは 1 件ずつ判じた時と同じ — #2670 の根 B)。"
  (<- unchanged bool (all-waiting-unchanged watchers state at))
  (when unchanged
    (return watchers))
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


(defk heard-beats [state beats at timing same-reply]
  {:pre [(: state ClusterState) (: beats tuple) (: at int) (: timing ClusterTiming) (: same-reply (| Callable None))]
   :post [(: % (| ClusterState None))] :tags {:context "coordinator" :role "judgment"}}
  "at の刻に届く仮の拍(worker の宿が預けた heartbeat)を、1 拍ずつの走りの歩と同じ受けの判断(api_policy.respond — 状態が拍の頭の tick の
   答えのままなら静かな早道)で順に受けた後の状態を知るため。どれかが 200 でない・返事が worker の最後に受けた返事と違う(same-reply —
   模擬の列が返事の JSON で比べる。無ければ違うと数える)なら None(その歩は静かでない — worker が本物の heartbeat を送る)。"
  (var current state)
  (var quiet True)
  (for [beat beats]
    (when quiet
      (<- answered tuple (respond current beat.request at timing beat.body :settled (is current state)))
      (val reply (get answered 2))
      (if (or (!= (get answered 1) 200) (is same-reply None) (not (isinstance reply HeartbeatReply)))
          (:= quiet False)
          (do (<- same bool (same-reply beat reply))
              (if same
                  (:= current (get answered 0))
                  (:= quiet False))))))
  (if quiet current None))


(defk only-times-moved [after before]
  {:pre [(: after ClusterState) (: before ClusterState)] :post [(: % bool)] :tags {:context "coordinator" :role "judgment"}}
  "仮の拍を受けた歩の後の状態 after が、歩の前の before から時刻の欄(生存の印 — coordinator の alive-ms と worker ごとの seen-mark・
   Rollout の拍の刻・worker の最後の連絡の時刻・状態の報告の at)のほか何も変えていないかを知るため — 静かな歩の条件。task の lease の
   延長は静かでないに数える(#2781 の表 — 延長は保存が要る)。"
  (and (= (set after.workers) (set before.workers))
       (= (set after.statuses) (set before.statuses))
       (all (gfor #(name info) (.items after.workers)
                  (= (replace info :last-seen-ms (. (get before.workers name) last-seen-ms)
                                   :seen-mark (. (get before.workers name) seen-mark))
                     (get before.workers name))))
       (all (gfor #(name report) (.items after.statuses)
                  (= (replace report :at (. (get before.statuses name) at)) (get before.statuses name))))
       (= (replace after :alive-ms before.alive-ms :rollout-tick-ms before.rollout-tick-ms
                   :workers before.workers :statuses before.statuses)
          before)))


(defk quiet-step [before at beats timing naming [same-reply None]]
  {:pre [(: before QuietStep) (: at int) (: beats tuple) (: timing ClusterTiming) (: naming ClusterNaming) (: same-reply (| Callable None))]
   :post [(: % (| QuietStep None))] :tags {:context "coordinator" :role "judgment"}}
  "before の歩の後、at の刻の歩(1 拍ずつの走りの coordinator-step と同じ順: tick → 届いた仮の拍 beats の受け → Rollout の拍 → mark-alive →
   待ちの判じ)が静かなら、その歩の後の状態と待ちを知るため。静かでなければ None。静か = tick が何も変えず、仮の拍の受けが時刻の欄の
   ほか何も変えず返事も worker の最後の返事と同じで、Rollout の拍(1 秒ごと)が k8s を読まず action も出さず何も変えず、待ちが
   「変わった」と答えず期限の待ちを吸える歩(変わってよいのは生存の印・Rollout の拍の刻・worker の最後の連絡と報告の at だけ)。"
  (val state before.state)
  (var current None)
  (<- ticked ClusterState (tick state at timing))
  (when (= ticked state)
    (<- heard (| ClusterState None) (heard-beats state beats at timing same-reply))
    (:= current heard))
  (var rolled None)
  (when (is-not current None)
    (if (>= (- at current.rollout-tick-ms) ROLLOUT-TICK-MS)
        (do (<- after (| ClusterState None) (rollout-quiet current at timing naming))
            (when (= after current)
              (:= rolled (replace current :rollout-tick-ms at))))
        (:= rolled current)))
  (var stepped None)
  (when (is-not rolled None)
    (val marked (mark-alive rolled at))
    (var moved True)
    (when beats
      (<- only bool (only-times-moved marked state))
      (:= moved only))
    (when moved
      (<- held (| tuple None) (renewed-watchers before.watchers marked at timing))
      (when (is-not held None)
        (:= stepped (QuietStep :at at :state marked :watchers held :marked (!= marked.alive-ms rolled.alive-ms) :beats beats)))))
  stepped)


(defk next-step-at [last pending]
  {:pre [(: last QuietStep) (: pending tuple)] :post [(: % int)] :tags {:context "coordinator" :role "judgment"}}
  "歩 last の後の、1 拍ずつの走りの次の歩の刻(直前の歩 + 1 拍と、まだ受けていない仮の拍 pending の届く刻の早い方)を知るため。"
  (min (+ last.at TICK-MS) (if pending (. (get pending 0) at) (+ last.at TICK-MS))))


(defk quiet-stretch [probe start horizon [same-reply None]]
  {:pre [(: probe IdleProbe) (: start QuietStep) (: horizon int) (: same-reply (| Callable None))] :post [(: % QuietStretch)]
   :tags {:context "coordinator" :role "judgment"}}
  "start の歩の後から、1 拍ずつの走りと同じ刻の歩(直前の歩 + 1 拍と、仮の拍の届く刻の早い方 — 同じ刻なら 1 つの歩で受ける)を、
   horizon の刻まで本番の判断で 1 歩ずつ試すため。答え = 静かだった歩の列と、最初の静かでない歩の刻(horizon までに無ければ None)。
   静かな歩は次の歩の起点になる(生存の印と引き直した待ちを持ち越す)。probe.beats = まだ受けていない仮の拍(刻の順 — start の刻と同じ
   刻の拍は start の歩の後の歩で受ける)。same-reply = 仮の拍の返事を worker の最後の返事と比べる Program の関数(heard-beats)。"
  (var last start)
  (var pending probe.beats)
  (var steps #())
  (var end None)
  (var going True)
  (while going
    (val grid (+ last.at TICK-MS))
    (val at (if (and pending (<= (. (get pending 0) at) grid)) (. (get pending 0) at) grid))
    (if (> at horizon)
        (:= going False)
        (do ;; 同じ刻に届く仮の拍は送り手の名の順に受ける(模擬の列が同じ刻の要求を名の順に取るのと同じ — request_queue.take-requests)。
            (val arriving (tuple (sorted (gfor beat pending :if (= beat.at at) beat) :key (fn [beat] beat.name))))
            (<- stepped (| QuietStep None) (quiet-step last at arriving probe.timing probe.naming same-reply))
            (if (is stepped None)
                (do (:= end at)
                    (:= going False))
                (do (:= steps (+ steps #(stepped)))
                    (:= pending (tuple (gfor beat pending :if (> beat.at at) beat)))
                    (:= last stepped))))))
  (QuietStretch :steps steps :end-at end))
