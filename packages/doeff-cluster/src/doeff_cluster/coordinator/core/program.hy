;;; coordinator の調停ループの Program(層 core — agora-redesign #2022 で doeff_cluster/coordinator.hy から移した)。
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
;;;   GET    /livez · /readyz   k8s の probe。調停ループを通さず、HTTP の受付(handler)が「ループが最後に要求を取りに来た時刻」だけで
;;;                             答える(probe-verdict)。fsync・k8s の API の読みでループが数秒遅れても落ちない(2026-09-25)。
;;;
;;; 形: 調停ループは doeff の Program(run-coordinator)。並んでいる要求をまとめて受け(NextRequests)、純粋な判断
;;; (api_policy.respond / tick / plan-rollouts)で 1 件ずつ次の状態と返事を導き、まとまりの変化を 1 回で永続化してから
;;; (Persist = 追記の log に 1 行・fsync 1 回)全員に返事をする(Reply)— group commit。返事を済ませた書き(版の番号を含む)は
;;; coordinator が落ちても消えない。永続化に失敗したら返事をせずに落ちる(送り手には失敗として見える)。
;;; k8s の Deployment の読みと台数の変更(ReadDeployment / ScaleDeployment)も effect。I/O は handler の中だけ。
(require doeff-hy.macros [defk <- val var])
(val MODULE-TAGS {:context "coordinator" :role "program"})
(import dataclasses [replace])
(import doeff_cluster.shared.core.clock [now-epoch-ms])
(import doeff_cluster.shared.intent.protocol [ClusterTiming Reply CoordinatorStopRequested Request])
(import doeff_cluster.coordinator.intent.cluster_model [ClusterState ClusterNaming IdleProbe NextRequests Persist Fault CoordinatorFault Watcher WatchRefusal WatchAnswer WatchStep])
(import doeff_cluster.coordinator.core.watch_policy [watch-of settle-watch earliest-deadline])
(import doeff_cluster.coordinator.core.cluster_policy [nodes-to-read with-derived-capabilities])
(import doeff_cluster.coordinator.core.durable_kv [durable-delta])
(import doeff_cluster.coordinator.core.api_policy [respond tick plan-rollouts deployments-to-observe scale-service record-action mark-alive ROLLOUT-ACTOR ROLLOUT-TICK-MS TICK-MS])
(import doeff_cluster.coordinator.core.resource_policy [stamp])
(import doeff_cluster.coordinator.intent.kube_model [ReadDeployment ScaleDeployment AnnotateDeployment ReadNodeLabels KubeUnavailable])



;; --- 調停ループ(Program) -------------------------------------------------------------

(defk rollout-tick [state timing naming now]
  {:pre [(: state ClusterState) (: timing ClusterTiming) (: naming ClusterNaming) (: now int)] :post [(: % ClusterState)]}
  ;; 1. Rollout の相手の Deployment を読む(届かなければ観測に error を置く = Unknown。台数は変えない)。
  (setv observed (dict state.deployments))
  (for [key (deployments-to-observe state now)]
    (setv #(ns name) (.split key "/" 1))
    (try
      (<- row dict (ReadDeployment ns name))
      (setv (get observed key) (| row {"at" now}))
      (except [error KubeUnavailable]
        (setv (get observed key) {"at" now "error" (str error)}))))
  (val observed-state (replace state :deployments observed))
  ;; 1b. 能力の導出(改訂 1 の I): worker の置かれた node の label を読み(古い観測だけ)、node-capabilities の表から derived を作り直す。
  (setv nodes (dict observed-state.nodes))
  (for [node (nodes-to-read observed-state now)]
    (try
      (<- labels dict (ReadNodeLabels node))
      (setv (get nodes node) {"labels" labels "at" now})
      (except [error KubeUnavailable]
        (setv (get nodes node) {"error" (str error) "at" now}))))
  (val before (with-derived-capabilities (replace observed-state :nodes nodes) naming.node-capabilities))
  ;; 2. 純粋な判断で段を進め、action を出す。
  (setv #(planned actions) (plan-rollouts before now timing naming))
  (var current (stamp before planned ROLLOUT-ACTOR now timing))
  ;; 3. action を実行する。Service の台数は状態の書き換え(送り手 = rollout/<名>)、Deployment は k8s の API。
  (for [action actions]
    (setv target (.get action "target") who (+ "rollout/" (get action "rollout")))
    (cond
      (and target (= (get target "kind") "Service"))
        (do (setv scaled (record-action (scale-service current (get target "name") (get action "replicas")) action True None now))
            (:= current (stamp current scaled who now timing)))
      (= (get action "op") "scale")
        (try
          (<- written int (ScaleDeployment (get target "namespace") (get target "name") (get action "replicas")
                                           :dry-run (get target "dryRun")))
          (:= current (stamp current (record-action current action True None now written) who now timing))
          (except [error KubeUnavailable]
            (:= current (stamp current (record-action current action False (str error) now) who now timing))))
      (= (get action "op") "annotate")
        (try
          (<- (AnnotateDeployment (get action "namespace") (get action "name") (get action "annotations")))
          (:= current (stamp current (record-action current action True None now) who now timing))
          (except [error KubeUnavailable]
            (:= current (stamp current (record-action current action False (str error) now) who now timing))))))
  (replace current :rollout-tick-ms now))


(defk fault-reply [fault]
  {:pre [(: fault Fault)] :post [(: % dict)] :tags {:context "doeff-cluster" :role "judgment"}}
  ;; coordinator の中の欠陥(api_policy.respond が 500 の Fault で返した例外)を log に 1 行出し、送り手に見せる本文を返す — 本文は
  ;; 送り手の誤りでないことを名乗る(#1024 — #1005 では中の TypeError が 400 に畳まれ、log にも出なかった)。
  (<- (CoordinatorFault fault))
  {"error" (.format "coordinator の中の欠陥: {}: {}({})" fault.error-type fault.message fault.where)
   "fault" True})


(defk request-reply [state request now timing]
  {:pre [(: state ClusterState) (: request Request) (: now int) (: timing ClusterTiming)] :post [(: % tuple)]
   :tags {:context "doeff-cluster" :role "program"}}
  ;; 版の変化を待つ読み(GET /watch)でない要求 1 件に答えるため: 判断(api_policy.respond)で次の状態と返事を導き、中の欠陥は log に
  ;; 1 行出して送り手に見せる本文にする。答え = #(次の状態 status 本文)。
  (val result (respond state request now timing))
  (val body (get result 2))
  (if (isinstance body Fault)
      (do (<- fault-body dict (fault-reply body))
          #((get result 0) (get result 1) fault-body))
      result))


(defk watch-answer-json [answer]
  {:pre [(: answer WatchAnswer)] :post [(: % dict)] :tags {:context "doeff-cluster" :role "judgment"}}
  "GET /watch の答えを返事の本文(JSON の object)にするため。"
  {"revision" answer.revision "changed" answer.changed})


(defk coordinator-step [state timing naming watchers]
  {:pre [(: state ClusterState) (: timing ClusterTiming) (: naming ClusterNaming) (: watchers tuple)] :post [(: % tuple)]}
  ;; 1 まとまり = 並んでいる要求を全部受ける(無ければ 1 秒待つ)→ 1 件ずつ判断 → Rollout(1 秒ごと)→ 永続化 → 全員に返事。
  ;; watchers = 返事を待たせている版の変化の待ち(Watcher の tuple — watch_policy)。永続化の後に、前からの待ちとこのまとまりで
  ;; 来た待ちを今の状態で判じ(settle-watch)、起きた物に返事をし、残りを次の拍へ持ち越す。
  ;; 返り値 = #(次の状態 まとまりの要求の数 待ち続ける待ちの tuple)。
  (<- wake (| int None) (earliest-deadline watchers))
  (<- batch list (NextRequests (/ TICK-MS 1000.0) :idle (IdleProbe state timing naming :wake-ms wake)))
  (<- now int (now-epoch-ms))
  ;; 期限の経過(worker の沈黙・task の lease・readiness の window)は、まとまりの有無と無関係に毎拍調停する(2026-09-25)。
  ;; 以前は要求の無い拍だけだったので、読みの要求(GET)が 1 秒より短い間隔で続く間は調停が走らず、担い手の死んだ切り離した task が
  ;; lost にならなかった(読みは状態を変えないので調停しない)。書きの要求は今までどおり要求ごとに調停する(api_policy.settle)。
  (var next (tick state now timing))
  (var replies #())
  (var waiting watchers)
  (for [request batch]
    (<- watch (| Watcher WatchRefusal None) (watch-of request now))
    (match watch
      (Watcher) (:= waiting (+ waiting #(watch)))
      (WatchRefusal) (:= replies (+ replies #(#(request 400 {"error" watch.reason}))))
      _ (do (<- answered tuple (request-reply next request now timing))
            (:= next (get answered 0))
            (:= replies (+ replies #(#(request (get answered 1) (get answered 2))))))))
  (when (>= (- now next.rollout-tick-ms) ROLLOUT-TICK-MS)
    (<- ticked ClusterState (rollout-tick next timing naming now))
    (:= next ticked))
  (:= next (mark-alive next now))
  (setv delta (durable-delta state next))
  (when delta
    (<- (Persist delta)))
  (for [#(request status body) replies]
    (<- (Reply request status body)))
  ;; 待ちへの返事は永続化の後(返した版の変化は coordinator が落ちても消えない — group commit と同じ)。
  (var kept #())
  (for [watcher waiting]
    (<- judged WatchStep (settle-watch watcher next now timing))
    (if (is judged.answer None)
        (:= kept (+ kept #(judged.watcher)))
        (do (<- body dict (watch-answer-json judged.answer))
            (<- (Reply watcher.request 200 body)))))
  #(next (len batch) kept))


(defk release-watchers [state watchers]
  {:pre [(: state ClusterState) (: watchers tuple)] :post [(: % int)] :tags {:context "doeff-cluster" :role "program"}}
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
  (while True
    (<- stopping bool (CoordinatorStopRequested))
    (when stopping
      (<- (release-watchers current watchers))
      (return current))
    (<- stepped tuple (coordinator-step current timing naming watchers))
    (:= current (get stepped 0))
    (:= watchers (get stepped 2))))
