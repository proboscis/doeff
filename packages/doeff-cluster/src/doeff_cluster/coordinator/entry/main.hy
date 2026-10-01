;;; coordinator の起動の入口(層 entry — agora-redesign #2022 で doeff_cluster/coordinator.hy から移した)。
;;; `hy -m doeff_cluster.coordinator.entry.main`(boot.sh)で撃つ。調停ループの Program は doeff_cluster/coordinator/core/program.hy。

(require doeff-hy.macros [defk <- val var])
(val MODULE-TAGS {:context "coordinator" :role "main"})
(import argparse)
(import json)
(import signal)
(import types [FrameType])
(import sys)
(import time)
(import pathlib [Path])
(import doeff [run with_handlers])
(import doeff_core_effects.scheduler [scheduled])
(import doeff_cluster.cluster_model [ClusterState ClusterTiming naming-from-json])
(import doeff_cluster.coordinator.core.cluster_policy [state-from-json fresh-task-prefix])
(import doeff_cluster.durable_kv [full-kv state-from-kv legacy-key-moves resume-writes])
(import doeff_cluster.wal_store [WalStore])
(import doeff_cluster.coordinator.core.api_policy [resume-after-downtime])
(import doeff_cluster.coordinator.core.resource_policy [adopt-legacy])
(import doeff_cluster.kube_handlers [kube-api kube-unavailable KubeClient])
;; HTTP の受付と停止の合図(coordinator_inbox — 以前の coordinator.hy の再輸出は #2022 で消した)。
(import doeff_cluster.coordinator_inbox [RequestInbox StopState])
(import doeff_cluster.coordinator_handler_sets [production-handlers])



(import doeff_cluster.coordinator.core.program [run-coordinator])


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
  ;; 置き場の無いところから起きた: task の id の頭を起動ごとに違う物にする(前の coordinator の id を振り直さない — #757)。
  (when (not (.exists file))
    (return (ClusterState :started-ms now :task-prefix (fresh-task-prefix now))))
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


(defn #^ None main []
  (setv parser (argparse.ArgumentParser :description "doeff worker の coordinator(実験)"))
  (.add-argument parser "--state-file" :required True)
  (.add-argument parser "--port" :type int :default 8080)
  (.add-argument parser "--naming" :default "{}"
                 :help "外の系と取り交わす名(JSON: ownerAnnotation・ownerScope・nodeCapabilities)— cluster_model.ClusterNaming")
  (setv args (.parse-args parser))
  (setv naming (naming-from-json args.naming))
  (setv stop (StopState))
  (defn #^ None on-signal [#^ int signum #^ (| FrameType None) frame] (setv stop.requested True))
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
