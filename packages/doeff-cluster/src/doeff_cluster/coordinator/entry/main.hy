;;; coordinator の起動の入口(層 entry — #2022 で doeff_cluster/coordinator.hy から移した)。
;;; `hy -m doeff_cluster.coordinator.entry.main`(boot.sh)で撃つ。調停ループの Program は doeff_cluster/coordinator/core/program.hy。

(require doeff-hy.macros [defk deff <- val var])
(val MODULE-TAGS {:context "coordinator" :role "main"})
(import argparse)
(import json)
(import os)
(import signal)
(import types [FrameType])
(import sys)
(import time)
(import pathlib [Path])
(import doeff [run with_handlers])
(import doeff_core_effects.effects [slog])
(import doeff_core_effects.file_effects [FileFailed ListDirectory PathKind ReadText StatPath file-done])
(import doeff_core_effects.handlers [slog-handler])
(import doeff_core_effects.os_file [os-file-handler])
(import doeff_core_effects.scheduler [scheduled])
(import doeff_cluster.shared.intent.protocol [ClusterTiming])
(import doeff_cluster.coordinator.intent.cluster_model [ClusterState ACCEPTED-FORMATS])
(import doeff_cluster.coordinator.protocol.cluster_json [naming-from-json])
(import doeff_cluster.coordinator.core.cluster_policy [fresh-task-prefix] doeff_cluster.coordinator.protocol.state_json [state-from-json])
(import doeff_cluster.coordinator.protocol.durable_kv [full-kv state-from-kv legacy-key-moves resume-writes])
(import doeff_cluster.foundation.wal_store [WalStore])
(import doeff_cluster.coordinator.core.api_policy [resume-after-downtime])
(import doeff_cluster.coordinator.core.resource_policy [adopt-legacy])
(import doeff_cluster.coordinator.protocol.kube [kube-api kube-unavailable] doeff_cluster.foundation.kube_client [KubeClient] doeff_cluster.coordinator.intent.kube_model [KubeUnavailable])
;; HTTP の受付と停止の合図(coordinator_inbox — 以前の coordinator.hy の再輸出は #2022 で消した)。
(import doeff_cluster.foundation.coordinator_inbox [RequestInbox StopState])
(import doeff_cluster.coordinator.entry.handler_sets [production-handlers])



(import doeff_cluster.coordinator.core.program [run-coordinator])


(defk board-file-rows [board-dir]
  {:pre [(: board-dir str)] :post [(: % tuple)] :tags {:context "coordinator" :role "main" :reads "json"}}
  "以前の形(formatVersion 2)の盤の行の file(board/<…>.json — 1 つに key・value・resourceVersion)を名の順に読むため。dir が無ければ空。"
  (<- entries (ListDirectory board-dir))
  (var rows #())
  (when (not (isinstance entries FileFailed))
    (for [entry (sorted entries :key (fn [e] e.name))]
      (when (.endswith entry.name ".json")
        (<- text str (file-done (ReadText (os.path.join board-dir entry.name))))
        (:= rows (+ rows #((json.loads text)))))))
  rows)


(defk legacy-state [state-file now]
  {:pre [(: state-file str) (: now int)] :post [(: % (| ClusterState None))] :tags {:context "coordinator" :role "main" :reads "json"}}
  "置き場がまだ無い時に、以前の形の file から状態を読むため: state.json(formatVersion 2)+ board/ の行の file、または盤込みの旧い
   state.json(版を振り直す)。file が無ければ None。"
  (<- found (StatPath state-file))
  (when (or (isinstance found FileFailed) (= found.kind PathKind.MISSING))
    (return None))
  (<- text str (file-done (ReadText state-file)))
  (val data (json.loads text))
  (when (!= (.get data "formatVersion") 2)
    (return (adopt-legacy (state-from-json data now) now (ClusterTiming))))
  (<- rows tuple (board-file-rows (os.path.join (os.path.dirname state-file) "board")))
  (state-from-json data now (dfor row rows (get row "key") (get row "value")) (dfor row rows (get row "key") (get row "resourceVersion"))))


(defk load-state [state-file store now]
  {:pre [(: state-file str) (: store WalStore) (: now int)] :post [(: % ClusterState)] :tags {:context "coordinator" :role "main"}}
  "coordinator が受け付けを始める前に、耐久の置き場(snapshot + log)から状態を読むため。置き場がまだ無ければ、以前の形の file から移す
   (legacy-state)。移した結果は snapshot に書き(fsync 済み)、元の file はそのまま残す(戻す時に使える)。以前の形の file の読みは
   file system の effect・1 行の報告は slog で、入口が答え手を並べる。置き場の読み書きは置き場の値を直に呼ぶ(#2760・#2764 で effect へ)。"
  (when (.exists store)
    ;; 改名の前の置き先の鍵は、新しい鍵を書き終えてから消す(durable_kv.legacy-key-moves の 2 つの書きを順に fsync)。
    (val moves (legacy-key-moves (.load store)))
    (for [delta moves]
      (.persist store delta))
    (when moves
      (<- (slog (.format "coordinator: 置き先の鍵を新しい名へ移した({} 件)" (len (get moves -1))))))
    (val resumed (resume-after-downtime (state-from-kv store.kv now) now))
    (val state (get resumed 0))
    (val gap (get resumed 1))
    ;; ずらした時計(worker の最後の連絡・task の lease・Rollout の段の起点)と生きていた時刻を、受け付けを始める前に耐久にする
    ;; (durable_kv.resume-writes)。
    (.persist store (resume-writes store.kv state))
    (when (> gap 0)
      (<- (slog (.format "coordinator: 止まっていた {:.1f} 秒を、進行中の Rollout の段と task の lease の時間に数えない" (/ gap 1000)))))
    (when store.recovered
      (<- (slog (.format "coordinator: 置き場の読み直しで最後の読めない行を捨てた: {}" store.recovered))))
    (return state))
  (.load store)
  (<- legacy (| ClusterState None) (legacy-state state-file now))
  ;; 置き場の無いところから起きた: task の id の頭を起動ごとに違う物にする(前の coordinator の id を振り直さない — #757)。
  (when (is legacy None)
    (return (ClusterState :started-ms now :task-prefix (fresh-task-prefix now))))
  (setv store.kv (full-kv legacy))
  (.checkpoint store)
  (<- (slog (.format "coordinator: 以前の形の状態を追記の log の置き場へ移した(Service {}・盤 {} 行・版 {})"
                     (len legacy.jobs) (len legacy.board) legacy.revision)))
  legacy)


;; --- composition root ------------------------------------------------------------------


(deff main []  ; defk にできない: console script の main(`hy -m doeff_cluster.coordinator.entry.main` の __main__ と boot.sh が素の関数として呼ぶ)
  {:pre [] :post [(: % None)] :tags {:context "coordinator" :role "main"}}
  "coordinator の process の入口: 起動の引数から置き場と受け口を組み立て、置き場から状態を読み直して、本番の handler の組の上で調停ループを回すため。"
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
  ;; 読み直しの以前の形の file の読みは os の file system・1 行の報告は stderr の slog が答える。
  (setv state (run (scheduled (with_handlers [slog-handler os-file-handler]
                                (load-state args.state-file store (int (* 1000 (time.time))))))))
  (setv inbox (RequestInbox args.port :formats ACCEPTED-FORMATS))
  (.start inbox)
  ;; k8s の API は Pod の ServiceAccount の token が在る時だけ(手元の coordinator では Rollout の Deployment の観測が Unknown のまま)。
  ;; 読みも台数の変更も 3 秒で打ち切る(読むのは進行中の Rollout の相手だけ・1 秒に 1 回)。
  (setv kube (if (KubeClient.available)
                 (kube-api (KubeClient KubeUnavailable :timeout 3.0))
                 (kube-unavailable "k8s の ServiceAccount の token が無い(Pod の外の coordinator)")))
  (print (.format "coordinator: :{} で受けます(Service {}・task {}・盤 {} 行・Rollout {}・版 {}・k8s {})"
                  args.port (len state.jobs) (len state.tasks) (len state.board) (len state.rollouts) state.revision
                  (if (KubeClient.available) "あり" "なし")) :file sys.stderr :flush True)
  ;; handler の組は coordinator_handler_sets の値(本番の組)。
  (run (scheduled (with_handlers (production-handlers inbox store stop kube) (run-coordinator state (ClusterTiming) naming))))
  (print "coordinator: 止まりました" :file sys.stderr :flush True))


(when (= __name__ "__main__")
  (main))
