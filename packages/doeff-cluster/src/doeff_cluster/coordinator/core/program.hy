;;; coordinator の調停ループの Program(層 core — #2022 で doeff_cluster/coordinator.hy から移した)。
;;; 起動の入口(state の読み・handler の組・main)は doeff_cluster/coordinator/entry/main.hy。
;;;
;;; doeff worker の coordinator(実験)。資源(Service・Worker・Task・Rollout)と盤を持ち、生きている worker へ割り当てる。
;;;
;;; HTTP(資源の口の詳細は api_policy.hy の冒頭):
;;;   GET/POST/PUT/DELETE /resources/<Kind>[/<名>]   資源ごとの compare-and-set(書きは X-Actor が要る)
;;;   POST   /resources/Service/<名>/readiness       service の ReportReady(送り手の process の世代つき)
;;;   POST   /resources/Service/<名>/metrics         service の ReportMetrics(同じ)
;;;   GET    /metrics                                今動いている process の計器(Prometheus の text・label service・worker)
;;;   GET    /events                                 出来事の記録(誰が・いつ・何を・前後の版)
;;;   PUT    /jobs          旧い口。資源ごとの compare-and-set に写す(一覧に無い Service は消さない)
;;;   POST   /heartbeat     worker の生存と状態 {name, provides, exclusive, capacity, versions, statuses} → {"jobs": […], "tasks": […], "timing": …}
;;;   GET    /state         宣言・worker・割り当て・task・各 worker の最新の状態・直近の出来事
;;;   GET    /watch?after=<版>&timeoutSeconds=<秒>[&worker=<名>&boot=<世代>]
;;;                           版(state の revision)が after と違うようになるか期限(10 秒まで)まで待って {"revision" "changed"} を返す
;;;                           (watch_policy — worker を名指せば、その worker の heartbeat の返事が変わる時だけ起きる・#1933)
;;;   GET    /board?prefix=[&withVersions=1]   盤の行(鍵が prefix で始まる物)
;;;   PUT    /board/<鍵>     {"value": …, "expect"?: …, "expectVersion"?: …} compare-and-set。合わなければ 409
;;;   POST   /tasks · GET /tasks/<id> · DELETE /tasks/<id>   task を出す・問い合わせる(lease を延ばす)・落とす
;;;   POST   /tasks/<id>/result   task の子 process が終わる前に直に届ける結果(届かなければ worker の heartbeat が運ぶ — #1387)
;;;   PUT /detached/<key> · GET /detached/<key> · POST /detached/<key>/cancel · DELETE /detached/<key>
;;;                           切り離した task を送る(job id で冪等)・読む(lease に触らない)・取り消す・保持を解く(detached_policy)
;;;   GET    /livez · /readyz   k8s の probe。調停ループを通さず、HTTP の受付(handler)が受付の箱の待ちの様子だけで答える
;;;                             (probe-verdict — 待つと定めた刻までの待ちは止まりと数えない・#3865)。fsync・k8s の API の読みで
;;;                             ループが数秒遅れても落ちない(2026-09-25)。
;;;
;;; 形: 調停ループは doeff の Program(run-coordinator)。要求が無い間は、次の期限(wake_policy.next-wake)か要求か停止の合図まで
;;; 受付を 1 本で待つ(1 秒ごとに起きない・#3865)。並んでいる要求をまとめて受け(NextRequests)、純粋な判断
;;; (api_policy.respond / tick / plan-rollouts)で 1 件ずつ次の状態と返事を導き、まとまりの変化を 1 回で永続化してから
;;; (SaveState — 答え手の protocol が KV の差分に綴り、追記の log に 1 行・fsync 1 回)全員に返事をする(Reply)— group commit。返事を済ませた書き(版の番号を含む)は
;;; coordinator が落ちても消えない。永続化に失敗したら返事をせずに落ちる(送り手には失敗として見える)。
;;; k8s の Deployment と Node の見張り(FollowDeployments・FollowNodes — 時間で読みに行かず、変化の出来事で受付の箱が起きる・#3868・
;;; #4070)と台数の変更(ScaleDeployment)も effect。
;;; I/O は handler の中だけ。
(require doeff-hy.macros [defk <- val var])
(require doeff-hy.record [defrecord])
(val MODULE-TAGS {:context "coordinator" :role "program"})
(import dataclasses [dataclass])  ; defrecord の展開が名指す
(import dataclasses [replace])
(import datetime [datetime])
(import doeff_time [GetTime epoch-ms-of])
(import doeff_cluster.shared.core.clock [now-epoch-ms datetime-of-epoch-ms])
(import doeff_cluster.shared.intent.protocol [ClusterTiming NextRequests Reply CoordinatorStopRequested Request])
(import doeff_cluster.coordinator.intent.cluster_model [ClusterState ErrorReply ClusterNaming SaveState Fault CoordinatorFault Watcher WatchRefusal WatchAnswer WatchStep
                                                       DeploymentUnreadable NodeLabelsUnreadable])
(import doeff_cluster.coordinator.core.watch_policy [watch-of settle-watch])
(import doeff_cluster.coordinator.core.cluster_policy [nodes-to-follow with-derived-capabilities])
(import doeff_cluster.coordinator.core.api_policy [respond tick plan-rollouts deployments-to-follow scale-service record-action mark-alive stamp-alive ROLLOUT-ACTOR])
(import doeff_cluster.coordinator.core.resource_policy [stamp])
(import doeff_cluster.coordinator.intent.request_bodies [ReadBody BodyUnreadable HeartbeatBody])
(import doeff_cluster.coordinator.intent.kube_model [ScaleDeployment AnnotateDeployment KubeUnavailable FollowDeployments FollowNodes])
(import doeff_core_effects.effects [slog])
(import doeff_core_effects.scheduler [Spawn])
(import doeff_events [Publish NoticeSent NoticeGapMarked NoticeDropped])
(import doeff_cluster.coordinator.core.cluster_policy [liveness-moves liveness-now note-liveness])
(import doeff_cluster.shared.intent.due_model [DueAt DueNow DueNever])
(import doeff_cluster.coordinator.core.wake_policy [next-wake after-step wait-seconds count-unsettled])



;; --- 調停ループ(Program) -------------------------------------------------------------

;; heartbeat の返事の遅れの行の名と閾(2026-10-07 — 名 + 欄 at = 返事を置き終えた時刻(ISO)・worker = 送り手の worker の名・
;; elapsed-ms = 受付の箱に並んでから返事を置き終えるまでの ms・slowest = いちばん長かった区間の名・slowest-ms = その区間の ms)。
;; 閾は worker の拍の遅れの線(worker/core/program の TICK-LAG-MS)と同じ 5 秒で、生死の lease(10 秒)の手前で名指す。
;; 実例: 2026-10-07 13:16〜13:18 JST に worker の heartbeat の往復が 5.8 秒かかり、coordinator の生死の表で live が瞬いたが、
;; coordinator の log には遅れの行が無く、遅れが coordinator の側か網かを分けられなかった。
(val HEARTBEAT-LAG-LOG "coordinator: heartbeat の返事の遅れ")
(val HEARTBEAT-LAG-MS 5000)
;; 区間の名: Inbox = 受付の箱に並んでから取られるまで(前の歩でループが止まっていた待ち — 受付の箱が測る)・Respond = 歩の頭の調停と、
;; まとまりの要求全部の判断・Rollout = Rollout の歩(k8s の台数の書きを含む — 走らなかった歩は 0)・SaveState = 生存の印と保存(fsync)・
;; Reply = 返事を置くまで。時刻は heartbeat の在る歩でだけ、要求の判断の後・Rollout の後(走った時)・保存の後・返事の後に読む
;; (歩の頭の読みは判断の now を兼ねる)。計りだけの読みは GetTime を直に出す(worker の拍の遅れの計りと同じ)。


(defrecord StepMarks
  "heartbeat の在る歩 1 つで読んだ時刻(epoch ms): taken = 要求を取った刻(歩の判断の now)・judged = 要求の判断の後・rolled = Rollout の
   歩の後(走らなければ judged)・saved = 保存の後・replied = 返事を置き終えた後。"
  (#^ int taken)
  (#^ int judged)
  (#^ int rolled)
  (#^ int saved)
  (#^ int replied))


(defrecord LagSpan
  "返事までの区間 1 つ: name = 区間の名・ms = 長さ。"
  (#^ str name)
  (#^ int ms))


(defrecord HeardBeat
  "歩で返事を置く heartbeat 1 つ: worker = 送り手の worker の名・queued-ms = 受付の箱に並んでから取られるまでの ms。"
  (#^ str worker)
  (#^ int queued-ms))


(defrecord HeartbeatLag
  "heartbeat の返事の遅れの行の欄: elapsed-ms = 受付の箱に並んでから返事を置き終えるまで・slowest = いちばん長い区間の名・
   slowest-ms = その長さ。"
  (#^ int elapsed-ms)
  (#^ str slowest)
  (#^ int slowest-ms))


(defk heartbeat-lag [marks queued-ms]
  {:pre [(: marks StepMarks) (: queued-ms int)] :post [(: % HeartbeatLag)] :tags {:context "coordinator" :role "judgment"}}
  "歩で読んだ時刻と、その heartbeat が受付の箱に並んだ ms から、遅れの行の欄(経過と、いちばん長い区間の名と長さ)を導くため。
   長さが並んだら先の区間を名指す。"
  (val spans #((LagSpan :name "Inbox" :ms queued-ms)
               (LagSpan :name "Respond" :ms (- marks.judged marks.taken))
               (LagSpan :name "Rollout" :ms (- marks.rolled marks.judged))
               (LagSpan :name "SaveState" :ms (- marks.saved marks.rolled))
               (LagSpan :name "Reply" :ms (- marks.replied marks.saved))))
  (val longest (max spans :key (fn [span] span.ms)))
  (HeartbeatLag :elapsed-ms (+ queued-ms (- marks.replied marks.taken)) :slowest longest.name :slowest-ms longest.ms))


(defk lap-ms [measuring]
  {:pre [(: measuring bool)] :post [(: % int)] :tags {:context "coordinator" :role "program"}}
  "heartbeat の在る歩でだけ、区間の切れ目の時刻(epoch ms)を読むため。measuring が偽なら時刻を読まずに 0(heartbeat の無い歩に
   時刻の effect を足さない)。"
  (if measuring
      (do (<- at datetime (GetTime))
          (epoch-ms-of at))
      0))


(defk note-heartbeat-lags [heard marks]
  {:pre [(: heard tuple) (: marks StepMarks)] :post [(: % int)] :tags {:context "coordinator" :role "program"}}
  "歩で返事を置いた heartbeat のうち、受付の箱に並んでから返事を置き終えるまでが HEARTBEAT-LAG-MS を越えた物ごとに、時刻(ISO)・
   worker の名・かかった ms・いちばん長い区間の名を 1 行で log に出すため(遅れが coordinator の側か網かを分ける材料)。
   heard = HeardBeat の tuple。答え = 出した行の数。"
  (var noted 0)
  (for [beat heard]
    (when (> (+ beat.queued-ms (- marks.replied marks.taken)) HEARTBEAT-LAG-MS)
      (<- lag HeartbeatLag (heartbeat-lag marks beat.queued-ms))
      (<- (slog HEARTBEAT-LAG-LOG :level "info" :at (.isoformat (datetime-of-epoch-ms marks.replied)) :worker beat.worker
                :elapsed-ms lag.elapsed-ms :slowest lag.slowest :slowest-ms lag.slowest-ms))
      (:= noted (+ noted 1))))
  noted)


(defk deployment-observations [state now]
  {:pre [(: state ClusterState) (: now int)] :post [(: % tuple)] :tags {:context "coordinator" :role "program"}}
  "Rollout の判断が観測を要る Deployment(api_policy.deployments-to-follow)を見張り、前の歩の後に見張りが伝えた変化を待たずに受け取る
   ため(#3868 — 時間で読みに行かない。変化の刻に見張りが受付の箱を起こす)。答え = 観測の表への書き(変わった Deployment だけ)。
   見張りが届かない・断られた Deployment は読めなかった観測になり(Rollout は Unknown)、その理由を 1 行出す(同じ理由は続けて伝わらない
   ので、変わった時に 1 度)。見張る相手が無い歩でも見張りを揃える(相手から外れた Deployment の見張りを止める)。"
  (<- deployments tuple (deployments-to-follow state))
  (<- writes tuple (FollowDeployments :keys deployments :now-ms now))
  (for [write writes]
    (when (isinstance write.value DeploymentUnreadable)
      (<- (slog (.format "coordinator: k8s の Deployment {} を見張れない — {}(Rollout はこの相手を Unknown と扱う)"
                         write.key write.value.error)))))
  writes)


(defk node-observations [state now]
  {:pre [(: state ClusterState) (: now int)] :post [(: % tuple)] :tags {:context "coordinator" :role "program"}}
  "worker の置かれた node(cluster_policy.nodes-to-follow)を見張り、前の歩の後に見張りが伝えた label の変化を待たずに受け取るため
   (#4070 — 時間で読みに行かない。変化の刻に見張りが受付の箱を起こす)。答え = 観測の表への書き(変わった node だけ)。見張りが
   届かない・断られた・消された node は読めなかった観測になり(その node の worker は前に導いた能力を保つ)、その理由を 1 行出す(同じ
   理由は続けて伝わらないので、変わった時に 1 度)。見張る node が無い歩でも見張りを揃える(worker の居なくなった node の見張りを止める)。"
  (<- nodes tuple (nodes-to-follow state))
  (<- writes tuple (FollowNodes :names nodes :now-ms now))
  (for [write writes]
    (when (isinstance write.value NodeLabelsUnreadable)
      (<- (slog (.format "coordinator: k8s の Node {} を見張れない — {}(その node の worker は前に導いた能力を保つ)"
                         write.key write.value.error)))))
  writes)


(defk rollout-tick [state timing naming now]
  {:pre [(: state ClusterState) (: timing ClusterTiming) (: naming ClusterNaming) (: now int)] :post [(: % ClusterState)]}
  ;; 1. k8s の観測(Rollout の相手の Deployment の見張り・能力の導出の node の label — 改訂 1 の I)を、調停ループを止めずに受け取る
  ;;    (#2807・#3868)。
  (<- deployment-writes tuple (deployment-observations state now))
  (<- node-writes tuple (node-observations state now))
  ;; 観測は ClusterState.observations の表へ書く(版の比べと保存の差分の外 — #2728)。
  (val seen state.observations)
  (val observed (replace seen :deployments (.with-writes seen.deployments deployment-writes)
                              :nodes (.with-writes seen.nodes node-writes)))
  (val before (with-derived-capabilities (replace state :observations observed) naming.node-capabilities))
  ;; 2. 純粋な判断で段を進め、action を出す。
  (setv #(planned actions) (! (plan-rollouts before now timing naming)))
  (var current (stamp before planned ROLLOUT-ACTOR now timing))
  ;; 3. action を実行する。Service の台数は状態の書き換え(送り手 = rollout/<名>)、Deployment は k8s の API。
  (for [action actions]
    (setv target (.get action "target") who (+ "rollout/" (get action "rollout")))
    (cond
      (and target (= target.kind "Service"))
        (do (setv scaled (record-action (scale-service current target.name (get action "replicas")) action True None now))
            (:= current (stamp current scaled who now timing)))
      (= (get action "op") "scale")
        (try
          (<- written int (ScaleDeployment target.namespace target.name (get action "replicas")
                                           :dry-run target.dry-run))
          (:= current (stamp current (record-action current action True None now written) who now timing))
          (except [error KubeUnavailable]
            (:= current (stamp current (record-action current action False (str error) now) who now timing))))
      (= (get action "op") "annotate")
        (try
          (<- (AnnotateDeployment (get action "namespace") (get action "name") (get action "annotations")))
          (:= current (stamp current (record-action current action True None now) who now timing))
          (except [error KubeUnavailable]
            (:= current (stamp current (record-action current action False (str error) now) who now timing))))))
  current)


(defk fault-reply [fault]
  {:pre [(: fault Fault)] :post [(: % ErrorReply)] :tags {:context "coordinator" :role "judgment"}}
  ;; coordinator の中の欠陥(api_policy.respond が 500 の Fault で返した例外)を log に 1 行出し、送り手に見せる本文を返す — 本文は
  ;; 送り手の誤りでないことを名乗る(#1024 — #1005 では中の TypeError が 400 に畳まれ、log にも出なかった)。
  (<- (CoordinatorFault fault))
  (ErrorReply :message (.format "coordinator の中の欠陥: {}: {}({})" fault.error-type fault.message fault.where) :fault True))


(defk readable-body [request]
  {:pre [(: request Request)] :post [(: % "道の本文の型の値か BodyUnreadable")] :tags {:context "coordinator" :role "program"}}
  "要求 1 件の本文を道の型に読むため(ReadBody)。読みの中で上がった例外は値 BodyUnreadable にして返し、判断 respond の欠陥の囲みで
   400 か 500 かを決めさせる — 本文の読みは判断の囲みの外(#2445)なので、ここで値にしないと 1 件の要求の欠陥が調停ループの外まで抜け、
   coordinator の process ごと落ちる(#2796)。"
  (try
    (<- body (ReadBody request))
    body
    (except [error Exception]
      (BodyUnreadable :error error))))


(defk request-reply [state request now timing [settled False]]
  {:pre [(: state ClusterState) (: request Request) (: now int) (: timing ClusterTiming) (: settled bool)] :post [(: % tuple)]
   :tags {:context "coordinator" :role "program"}}
  ;; 版の変化を待つ読み(GET /watch)でない要求 1 件に答えるため: 判断(api_policy.respond)で次の状態と返事を導き、中の欠陥は log に
  ;; 1 行出して送り手に見せる本文にする。答え = #(次の状態 status 本文 heartbeat の送り手の名か None)— 名は返事の遅れの計り
  ;; (note-heartbeat-lags)が名指す worker(本文を heartbeat の型に読めた要求だけ)。
  ;; 本文は道の型に解いてから判断に渡す(答え手 = coordinator/protocol/request_bodies — #2445)。
  (<- read-body (readable-body request))
  (val sender (if (isinstance read-body HeartbeatBody) read-body.name None))
  ;; settled = state が同じ now で調停済み(coordinator-step が拍の頭の tick の答えのままの時に渡す)— 静かな heartbeat の早道の前提(#2655)。
  (<- result tuple (respond state request now timing read-body :settled settled))
  (val body (get result 2))
  (if (isinstance body Fault)
      (do (<- fault-body ErrorReply (fault-reply body))
          #((get result 0) (get result 1) fault-body sender))
      #(#* result sender)))


(defk watch-answer-json [answer]
  {:pre [(: answer WatchAnswer)] :post [(: % dict)] :tags {:context "coordinator" :role "judgment"}}
  "GET /watch の答えを返事の本文(JSON の object)にするため。"
  {"revision" answer.revision "changed" answer.changed})


(defk announce-liveness [events]
  {:pre [(: events tuple)] :post [(: % int)] :tags {:context "coordinator" :role "program"}}
  "worker の生死の出来事(WorkerGone・WorkerBack — #3864)を process の外へ出すため。届かなかった物は doeff-events が channel の欠けの
   印として持ち、受け手は戻った時に coordinator の状態から追いつく(ADR-DOE-EVENTS-002 R5)ので、ここは出し直さず 1 行だけ報告する。
   答え = 出した数。"
  (for [event events]
    (<- answer (| NoticeSent NoticeGapMarked NoticeDropped) (Publish event))
    (match answer
      (NoticeSent) None
      (NoticeGapMarked) (<- (slog (.format "coordinator: {} が知らせの broker に届かなかった — channel {} に欠けの印({})"
                                           (. (type event) __name__) answer.channel answer.detail)))
      (NoticeDropped) (<- (slog (.format "coordinator: {} が知らせの broker に届かず捨てた({})"
                                         (. (type event) __name__) answer.detail)))))
  (len events))


(defk announced-aside [events]
  {:pre [(: events tuple)] :post [(: % None)] :tags {:context "coordinator" :role "program"}}
  "worker の生死の出来事を、調停の歩の外の task で出すため(#3864 — 知らせの broker が答えない間も、coordinator の歩と要求への返事を
   止めない)。出る順は doeff-events の包みの 1 本の出口が守る(後から出した task は前の task の後に並ぶ)。出来事が無い歩は task を作らない。"
  (when events
    (<- (Spawn (announce-liveness events))))
  None)


(defk coordinator-step [state timing naming watchers wait]
  {:pre [(: state ClusterState) (: timing ClusterTiming) (: naming ClusterNaming) (: watchers tuple) (: wait (| float None))]
   :post [(: % tuple)]}
  ;; 1 まとまり = 並んでいる要求を全部受ける(無ければ wait 秒まで待つ — None は期限なし)→ 1 件ずつ判断 → Rollout → 永続化 → 全員に
  ;; 返事。wait は調停ループが次に起きる刻から求める(wake_policy・#3865)。Rollout は毎歩回す: 相手の Deployment の変化は見張りが
  ;; 受付の箱を起こすので、起きた歩でその変化を判じる(#3868)。
  ;; watchers = 返事を待たせている版の変化の待ち(Watcher の tuple — watch_policy)。永続化の後に、前からの待ちとこのまとまりで
  ;; 来た待ちを今の状態で判じ(settle-watch)、起きた物に返事をし、残りを次の歩へ持ち越す。
  ;; 返り値 = #(次の状態 まとまりの要求の数 待ち続ける待ちの tuple)。
  (<- batch list (NextRequests wait))
  (<- now int (now-epoch-ms))
  ;; 期限の経過(worker の沈黙・task の lease・readiness の window)は、まとまりの有無と無関係に毎歩調停する(2026-09-25)。
  ;; 以前は要求の無い歩だけだったので、読みの要求(GET)が続く間は調停が走らず、担い手の死んだ切り離した task が
  ;; lost にならなかった(読みは状態を変えないので調停しない)。書きの要求は今までどおり要求ごとに調停する(api_policy.settle)。
  (<- tick-answer ClusterState (tick state now timing))
  (var next tick-answer)
  (var replies #())
  (var waiting watchers)
  ;; 返事を置く heartbeat(HeardBeat の tuple)— 返事の遅れの計り(note-heartbeat-lags)の相手。
  (var heard #())
  (for [request batch]
    (<- watch (| Watcher WatchRefusal None) (watch-of request now timing))
    (match watch
      (Watcher) (:= waiting (+ waiting #(watch)))
      (WatchRefusal) (:= replies (+ replies #(#(request 400 (ErrorReply :message watch.reason)))))
      ;; 状態が歩の頭の tick の答えのままなら、同じ now で調停済み(前の要求が調停を通らずに状態を変えていない)。
      _ (do (<- answered tuple (request-reply next request now timing (is next tick-answer)))
            (:= next (get answered 0))
            (:= replies (+ replies #(#(request (get answered 1) (get answered 2)))))
            (val sender (get answered 3))
            (when (is-not sender None)
              (:= heard (+ heard #((HeardBeat :worker sender :queued-ms request.queued-ms))))))))
  ;; 返事の遅れの計り: heartbeat の在る歩でだけ区間の切れ目の時刻を読む(区間の名は HEARTBEAT-LAG-MS の註)。
  (val measuring (bool heard))
  (<- judged-ms int (lap-ms measuring))
  (<- ticked ClusterState (rollout-tick next timing naming now))
  (:= next ticked)
  (<- rolled-ms int (lap-ms measuring))
  (<- marked ClusterState (mark-alive next now))
  (:= next marked)
  (<- (SaveState state next))
  (<- saved-ms int (lap-ms measuring))
  ;; worker の生死の出来事は保存の後に出す(#3864)。
  (<- (announced-aside (! (liveness-moves state next timing))))
  (for [#(request status body) replies]
    (<- (Reply request status body)))
  (when measuring
    (<- replied-ms int (lap-ms measuring))
    (<- (note-heartbeat-lags heard (StepMarks :taken now :judged judged-ms :rolled rolled-ms :saved saved-ms :replied replied-ms))))
  ;; 待ちへの返事は永続化の後(返した版の変化は coordinator が落ちても消えない — group commit と同じ)。
  (var kept #())
  (for [watcher waiting]
    (<- judged WatchStep (settle-watch watcher next now timing))
    (if (is judged.answer None)
        (:= kept (+ kept #(judged.watcher)))
        (do (<- body dict (watch-answer-json judged.answer))
            (<- (Reply watcher.request 200 body)))))
  #(next (len batch) kept now))


(defk release-watchers [state watchers]
  {:pre [(: state ClusterState) (: watchers tuple)] :post [(: % int)] :tags {:context "coordinator" :role "program"}}
  ;; 止まる調停ループが、待たせている版の変化の待ちに「変わっていない」と今の版で返すため(送り手を受付の打ち切りまで待たせない)。
  ;; 答え = 返した数。
  (<- body dict (watch-answer-json (WatchAnswer state.revision False)))
  (for [watcher watchers]
    (<- (Reply watcher.request 200 body)))
  (len watchers))


(defk run-coordinator [state timing naming]
  {:pre [(: state ClusterState) (: timing ClusterTiming) (: naming ClusterNaming)] :post [(: % ClusterState)]}
  ;; naming = 外の系と取り交わす名(Rollout の annotation・node の label から導く能力)。composition root(main・模擬環境)が渡す。
  ;; node の label から導く能力の名は、worker の自己申告として受けない(register-heartbeat が provides から外す — 改訂 1 の I)。
  (var current (replace state :derivable (frozenset (gfor row naming.node-capabilities (get row 2)))))
  (var watchers #())
  ;; 次に起きる刻(最初の歩は今すぐ)と、今すぐが要求なしに続いた歩の数(wake_policy.count-unsettled)。
  (var due (DueNow))
  (var streak 0)
  ;; 起動の時に、名簿の全部の今の生死を 1 度出す(#3864 — 沈黙の集合は保存の形に無いので、ここで今の刻から求める。保存する欄は
  ;; 変わらない。受け手の追いつきにも成る)。以後の歩は、変わった所だけを出す。
  (<- started int (now-epoch-ms))
  (:= current (note-liveness current started timing))
  (<- (announced-aside (! (liveness-now current timing))))
  (while True
    (<- stopping bool (CoordinatorStopRequested))
    (when stopping
      ;; 止まる刻の生存の印を保存してから止まる(#3865 — 要求の無い間に眠る形では、眠りの間に印を書かない。起き直しの
      ;; resume-after-downtime が止まっていた長さを、最後の印から数えて多く見積もらないように)。
      (<- now int (now-epoch-ms))
      (<- marked ClusterState (stamp-alive current now))
      (<- (SaveState current marked))
      (<- (release-watchers marked watchers))
      (return marked))
    ;; 受付は、前の歩の後に決めた次に起きる刻まで待つ(期限が無ければ期限なし — 要求か停止の合図か外の出来事でだけ起きる・#3865)。
    (<- waiting-from int (now-epoch-ms))
    (<- wait (| float None) (wait-seconds due waiting-from))
    (<- stepped tuple (coordinator-step current timing naming watchers wait))
    (val after (get stepped 0))
    (:= watchers (get stepped 2))
    ;; 次に起きる刻: 要求を受けずに状態を変えた歩の後は今すぐ、それ以外は次に起きる刻(after-step — 要求で変わった状態が落ち着いて
    ;; いなければ期限の関数が今すぐを返す)。今すぐが続けば、変わり続けた欄を名指して落ちる(count-unsettled)。
    (val woke (get stepped 3))
    (<- planned (| DueAt DueNow DueNever) (next-wake after woke timing naming watchers))
    (val took (> (get stepped 1) 0))
    (<- following (| DueAt DueNow DueNever) (after-step planned (and (not took) (!= after current))))
    (<- counted int (count-unsettled streak following current after))
    (:= streak counted)
    (:= due following)
    (:= current after)))
