;;; doeff worker の coordinator(実験)。資源(Service・Worker・Task・Rollout)と盤を持ち、生きている worker へ割り当てる。
;;;
;;; HTTP(資源の口の詳細は api_policy.hy の冒頭):
;;;   GET/POST/PUT/DELETE /resources/<Kind>[/<名>]   資源ごとの compare-and-set(書きは X-Actor が要る)
;;;   POST   /resources/Service/<名>/readiness       service の ReportReady(送り手の process の世代つき)
;;;   POST   /resources/Service/<名>/metrics         service の ReportMetrics(同じ)
;;;   GET    /metrics                                今動いている process の計器(Prometheus の text・label service・worker)
;;;   GET    /events                                 出来事の記録(誰が・いつ・何を・前後の版)
;;;   PUT    /jobs          旧い口。資源ごとの compare-and-set に写す(一覧に無い Service は消さない)
;;;   POST   /heartbeat     worker の生存と状態 {name, labels, capacity, versions, statuses} → {"jobs": […], "tasks": […], "timing": …}
;;;   GET    /state         宣言・worker・割り当て・task・各 worker の最新の状態・直近の出来事
;;;   GET    /board?prefix=[&withVersions=1]   盤の行(鍵が prefix で始まる物)
;;;   PUT    /board/<鍵>     {"value": …, "expect"?: …, "expectVersion"?: …} compare-and-set。合わなければ 409
;;;   POST   /tasks · GET /tasks/<id> · DELETE /tasks/<id>   task を出す・問い合わせる(lease を延ばす)・落とす
;;;   PUT /detached/<key> · GET /detached/<key> · POST /detached/<key>/cancel · DELETE /detached/<key>
;;;                           切り離した task を送る(job id で冪等)・読む(lease に触らない)・取り消す・保持を解く(detached_policy)
;;;   GET    /livez · /readyz   k8s の probe。調停ループを通さず、HTTP の受付(handler)が「ループが最後に要求を取りに来た時刻」だけで
;;;                             答える(probe-verdict)。fsync・k8s の API・registry の読みでループが数秒遅れても落ちない(2026-09-25)。
;;;
;;; 形: 調停ループは doeff の Program(run-coordinator)。並んでいる要求をまとめて受け(NextRequests)、純粋な判断
;;; (api_policy.respond / tick / plan-rollouts)で 1 件ずつ次の状態と返事を導き、まとまりの変化を 1 回で永続化してから
;;; (Persist = 追記の log に 1 行・fsync 1 回)全員に返事をする(Reply)— group commit。返事を済ませた書き(版の番号を含む)は
;;; coordinator が落ちても消えない。永続化に失敗したら返事をせずに落ちる(送り手には失敗として見える)。
;;; k8s の Deployment の読みと台数の変更(ReadDeployment / ScaleDeployment)も effect。I/O は handler の中だけ。
(require doeff-hy.macros [defk <-])
(import argparse)
(import dataclasses [replace])
(import json)
(import os)
(import signal)
(import sys)
(import time)
(import pathlib [Path])
(import doeff [run with_handlers])
(import doeff_core_effects.scheduler [scheduled])
(import doeff_cluster.clock [now-epoch-ms])
(import .cluster_model [ClusterState ClusterTiming ClusterNaming naming-from-json NextRequests Reply Persist CoordinatorStopRequested])
(import .cluster_policy [state-from-json])
(import .durable_kv [durable-kv kv-delta full-kv state-from-kv legacy-key-moves resume-writes])
(import .wal_store [WalStore wal-store])
(import .api_policy [respond tick plan-rollouts deployments-to-observe scale-service record-action mark-alive resume-after-downtime
                     ROLLOUT-ACTOR])
(import .resource_policy [stamp adopt-legacy])
(import .kube_model [ReadDeployment ScaleDeployment AnnotateDeployment KubeUnavailable])
(import .kube_handlers [kube-api kube-unavailable KubeClient])
(import .image_model [ReadImageLabels ImageUnavailable])
(import .base_follow_policy [due-deployments images-to-resolve image-entry follow-bases BASE-FOLLOW-ACTOR])
;; HTTP の受付と停止の合図(2026-09-25 に coordinator_inbox.hy へ分けた — 以前の import の口のためにここでも出す)。
(import .coordinator_inbox [READY-STALL-SECONDS LIVE-STALL-SECONDS probe-verdict ReplySlot RequestInbox http-requests stop-flag
                            StopState])
(import .coordinator_handler_sets [production-handlers])

(setv ROLLOUT-TICK-MS 1000)


;; --- 調停ループ(Program) -------------------------------------------------------------

(defk rollout-tick [state timing naming now]
  {:pre [(: state ClusterState) (: timing ClusterTiming) (: naming ClusterNaming) (: now int)] :post [(: % ClusterState)]}
  ;; 1. Rollout の相手と、土台の版を追う相手の Deployment を読む(届かなければ観測に error を置く = Unknown。台数は変えない)。
  (setv observed (dict state.deployments))
  (for [key (list (dict.fromkeys (+ (deployments-to-observe state now) (due-deployments state now))))]
    (setv #(ns name) (.split key "/" 1))
    (try
      (<- row dict (ReadDeployment ns name))
      (setv (get observed key) (| row {"at" now}))
      (except [error KubeUnavailable]
        (setv (get observed key) {"at" now "error" (str error)}))))
  (setv before (replace state :deployments observed))
  ;; 1b. 土台の版の追随(base_follow_policy): 本番の Deployment の image の LABEL を読み(新しい image の時だけ)、Service の base を進める。
  (setv images (dict before.images))
  (for [image (images-to-resolve before now)]
    (try
      (<- labels dict (ReadImageLabels image))
      (setv (get images image) (image-entry labels now naming))
      (except [error ImageUnavailable]
        (setv (get images image) {"error" (str error) "at" now}))))
  (setv with-images (replace before :images images))
  (setv before (stamp with-images (follow-bases with-images now) BASE-FOLLOW-ACTOR now timing))
  ;; 2. 純粋な判断で段を進め、action を出す。
  (setv #(planned actions) (plan-rollouts before now timing naming))
  (setv state (stamp before planned ROLLOUT-ACTOR now timing))
  ;; 3. action を実行する。Service の台数は状態の書き換え(送り手 = rollout/<名>)、Deployment は k8s の API。
  (for [action actions]
    (setv target (.get action "target") who (+ "rollout/" (get action "rollout")))
    (cond
      (and target (= (get target "kind") "Service"))
        (do (setv scaled (record-action (scale-service state (get target "name") (get action "replicas")) action True None now))
            (setv state (stamp state scaled who now timing)))
      (= (get action "op") "scale")
        (try
          (<- written int (ScaleDeployment (get target "namespace") (get target "name") (get action "replicas")
                                           :dry-run (get target "dryRun")))
          (setv state (stamp state (record-action state action True None now written) who now timing))
          (except [error KubeUnavailable]
            (setv state (stamp state (record-action state action False (str error) now) who now timing))))
      (= (get action "op") "annotate")
        (try
          (<- (AnnotateDeployment (get action "namespace") (get action "name") (get action "annotations")))
          (setv state (stamp state (record-action state action True None now) who now timing))
          (except [error KubeUnavailable]
            (setv state (stamp state (record-action state action False (str error) now) who now timing))))))
  (replace state :rollout-tick-ms now))


(defk coordinator-step [state timing naming]
  {:pre [(: state ClusterState) (: timing ClusterTiming) (: naming ClusterNaming)] :post [(: % tuple)]}
  ;; 1 まとまり = 並んでいる要求を全部受ける(無ければ 1 秒待つ)→ 1 件ずつ判断 → Rollout(1 秒ごと)→ 永続化 → 全員に返事。
  ;; 返り値 = #(次の状態 まとまりの要求の数)。
  (<- batch list (NextRequests 1.0))
  (<- now int (now-epoch-ms))
  ;; 期限の経過(worker の沈黙・task の lease・readiness の window)は、まとまりの有無と無関係に毎拍調停する(2026-09-25)。
  ;; 以前は要求の無い拍だけだったので、読みの要求(GET)が 1 秒より短い間隔で続く間は調停が走らず、担い手の死んだ切り離した task が
  ;; lost にならなかった(読みは状態を変えないので調停しない)。書きの要求は今までどおり要求ごとに調停する(api_policy.settle)。
  (setv next (tick state now timing) replies [])
  (for [request batch]
    (setv #(next status body) (respond next request now timing))
    (.append replies #(request status body)))
  (when (>= (- now next.rollout-tick-ms) ROLLOUT-TICK-MS)
    (<- next ClusterState (rollout-tick next timing naming now)))
  (setv next (mark-alive next now))
  (setv delta (kv-delta (durable-kv state) (durable-kv next) state next))
  (when delta
    (<- (Persist delta)))
  (for [#(request status body) replies]
    (<- (Reply request status body)))
  #(next (len batch)))


(defk run-coordinator [state timing naming]
  {:pre [(: state ClusterState) (: timing ClusterTiming) (: naming ClusterNaming)] :post [(: % ClusterState)]}
  ;; naming = 外の系と取り交わす名(Rollout の annotation・image の LABEL)。composition root(main・模擬環境)が渡す。
  (while True
    (<- stopping bool (CoordinatorStopRequested))
    (when stopping (return state))
    (<- stepped tuple (coordinator-step state timing naming))
    (setv state (get stepped 0))))



(defn #^ ClusterState load-state [#^ str state-file #^ WalStore store #^ int now]
  "耐久の置き場(snapshot + log)から状態を読む。置き場がまだ無ければ、以前の形の file から移す:
   state.json(formatVersion 2)+ board/ の行の file、または盤込みの旧い state.json(版を振り直す)。移した結果は snapshot に
   書き(fsync 済み)、元の file はそのまま残す(戻す時に使える)。"
  (setv timing (ClusterTiming))
  (when (.exists store)
    ;; 改名の前の置き先の鍵は、新しい鍵を書き終えてから消す(durable_kv.legacy-key-moves の 2 つの書きを順に fsync)。
    (setv moves (legacy-key-moves (.load store)))
    (for [delta moves]
      (.persist store delta))
    (when moves
      (print (.format "coordinator: 置き先の鍵を新しい名へ移した({} 件)" (len (get moves -1))) :file sys.stderr :flush True))
    (setv #(state gap) (resume-after-downtime (state-from-kv store.kv now) now))
    ;; ずらした時計(worker の最後の連絡・task の lease・Rollout の段の起点)と生きていた時刻を、受け付けを始める前に耐久にする
    ;; (durable_kv.resume-writes)。
    (.persist store (resume-writes store.kv state))
    (when (> gap 0)
      (print (.format "coordinator: 止まっていた {:.1f} 秒を、進行中の Rollout の段と task の lease の時間に数えない" (/ gap 1000))
             :file sys.stderr :flush True))
    (when store.recovered
      (print (.format "coordinator: 置き場の読み直しで最後の読めない行を捨てた: {}" store.recovered) :file sys.stderr :flush True))
    (return state))
  (.load store)
  (setv file (Path state-file))
  (when (not (.exists file))
    (return (ClusterState :started-ms now)))
  (setv data (json.loads (.read-text file :encoding "utf-8")))
  (if (= (.get data "formatVersion") 2)
      (do (setv board {} versions {} d (/ file.parent "board"))
          (when (.exists d)
            (for [entry (sorted (.glob d "*.json"))]
              (setv row (json.loads (.read-text entry :encoding "utf-8")))
              (setv (get board (get row "key")) (get row "value") (get versions (get row "key")) (get row "resourceVersion"))))
          (setv state (state-from-json data now board versions)))
      (setv state (adopt-legacy (state-from-json data now) now timing)))
  (setv store.kv (full-kv state))
  (.checkpoint store)
  (print (.format "coordinator: 以前の形の状態を追記の log の置き場へ移した(Service {}・盤 {} 行・版 {})"
                  (len state.jobs) (len state.board) state.revision) :file sys.stderr :flush True)
  state)


;; --- composition root ------------------------------------------------------------------


(defn main []
  (setv parser (argparse.ArgumentParser :description "doeff worker の coordinator(実験)"))
  (.add-argument parser "--state-file" :required True)
  (.add-argument parser "--port" :type int :default 8080)
  (.add-argument parser "--naming" :default "{}"
                 :help "外の系と取り交わす名(JSON: ownerAnnotation・ownerScope・revisionLabel・versionLabels)— cluster_model.ClusterNaming")
  (setv args (.parse-args parser))
  (setv naming (naming-from-json args.naming))
  (setv stop (StopState))
  (defn on-signal [signum frame] (setv stop.requested True))
  (signal.signal signal.SIGTERM on-signal)
  (signal.signal signal.SIGINT on-signal)
  (setv store (WalStore (str (/ (. (Path args.state-file) parent) "wal"))))
  (setv state (load-state args.state-file store (int (* 1000 (time.time)))))
  (setv inbox (RequestInbox args.port))
  (.start inbox)
  ;; k8s の API は Pod の ServiceAccount の token が在る時だけ(手元の coordinator では Rollout の Deployment の観測が Unknown のまま)。
  ;; 読みも台数の変更も 3 秒で打ち切る(読むのは進行中の Rollout の相手だけ・1 秒に 1 回)。
  (setv kube (if (KubeClient.available)
                 (kube-api (KubeClient :timeout 3.0))
                 (kube-unavailable "k8s の ServiceAccount の token が無い(Pod の外の coordinator)")))
  (print (.format "coordinator: :{} で受けます(Service {}・task {}・盤 {} 行・Rollout {}・版 {}・k8s {})"
                  args.port (len state.jobs) (len state.tasks) (len state.board) (len state.rollouts) state.revision
                  (if (KubeClient.available) "あり" "なし")) :file sys.stderr :flush True)
  ;; handler の組は coordinator_handler_sets の値(本番の組)。
  (run (scheduled (with_handlers (production-handlers inbox store stop kube) (run-coordinator state (ClusterTiming) naming))))
  (print "coordinator: 止まりました" :file sys.stderr :flush True))


(when (= __name__ "__main__")
  (main))
