;;; coordinator の起動の入口(層 entry — #2022 で doeff_cluster/coordinator.hy から移した)。
;;; `hy -m doeff_cluster.coordinator.entry.main`(boot.sh)で撃つ。調停ループの Program は doeff_cluster/coordinator/core/program.hy。

(require doeff-hy.macros [defk deff <- val var])
(val MODULE-TAGS {:context "coordinator" :role "main"})
(import argparse)
(import json)
(import os)
(import sys)
(import pathlib [Path])
(import dataclasses [replace])
(import doeff [run with_handlers])
(import doeff_time [sync-time-handler])
(import doeff_cluster.shared.core.clock [now-epoch-ms])
(import doeff_core_effects.effects [slog])
(import doeff_core_effects.file_effects [FileFailed ListDirectory PathKind ReadText StatPath file-done])
(import doeff_core_effects.handlers [slog-handler])
(import doeff_core_effects.os_file [os-file-handler])
(import doeff_core_effects.os_process [subprocess-handler])
(import doeff_core_effects.process_effects [EnvEntry ReadEnvironment])
(import doeff_cluster.shared.core.launch_rules [coordinator-commit-env-name])
(import doeff_core_effects.scheduler [scheduled])
(import doeff_cluster.shared.intent.protocol [ClusterTiming])
(import doeff_cluster.coordinator.intent.cluster_model [ClusterState ACCEPTED-FORMATS])
(import doeff_cluster.coordinator.protocol.cluster_json [naming-from-json])
(import doeff_cluster.coordinator.core.cluster_policy [fresh-task-prefix] doeff_cluster.coordinator.protocol.state_json [state-from-json])
(import doeff_cluster.coordinator.protocol.durable_kv [full-kv state-from-kv legacy-key-moves resume-writes])
(import doeff_cluster.foundation.wal_store [WalStore])
(import doeff_cluster.coordinator.protocol.store [DurableStore durable-exists durable-load durable-persist durable-checkpoint])
(import doeff_cluster.coordinator.core.api_policy [resume-after-downtime])
(import doeff_cluster.coordinator.core.resource_policy [adopt-legacy])
(import doeff_cluster.coordinator.protocol.kube [ObjectWatches kube-api kube-unavailable] doeff_cluster.foundation.kube_client [KubeClient] doeff_cluster.coordinator.intent.kube_model [KubeUnavailable])
;; HTTP の受付と停止の合図(coordinator_inbox — 以前の coordinator.hy の再輸出は #2022 で消した)。
(import doeff_cluster.foundation.coordinator_inbox [RequestInbox StopState stop-on-signals])
(import doeff_cluster.coordinator.entry.handler_sets [memory-notices production-handlers redis-notices])
(import doeff_events [MemoryBroker])



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
    (<- old ClusterState (state-from-json data now))
    (return (adopt-legacy old now (ClusterTiming))))
  (<- rows tuple (board-file-rows (os.path.join (os.path.dirname state-file) "board")))
  (<- state ClusterState (state-from-json data now (dfor row rows (get row "key") (get row "value"))
                                          (dfor row rows (get row "key") (get row "resourceVersion"))))
  state)


(defk load-state [state-file store now]
  {:pre [(: state-file str) (: store DurableStore) (: now int)] :post [(: % ClusterState)] :tags {:context "coordinator" :role "main"}}
  "coordinator が受け付けを始める前に、耐久の置き場(snapshot + log)から状態を読むため。置き場がまだ無ければ、以前の形の file から移す
   (legacy-state)。移した結果は snapshot に書き(fsync 済み)、元の file はそのまま残す(戻す時に使える)。以前の形の file の読みは
   file system の effect・1 行の報告は slog で、入口が答え手を並べる。置き場の読み書きは置き場の口(protocol/store の durable-exists・
   durable-load・durable-persist・durable-checkpoint — file と memory の置き場を切り分ける・#2785)を通す。"
  (when (! (durable-exists store))
    ;; 改名の前の置き先の鍵は、新しい鍵を書き終えてから消す(durable_kv.legacy-key-moves の 2 つの書きを順に fsync)。
    (<- loaded dict (durable-load store))
    (<- moves list (legacy-key-moves loaded))
    (for [delta moves]
      (<- (durable-persist store delta)))
    (when moves
      (<- (slog (.format "coordinator: 置き先の鍵を新しい名へ移した({} 件)" (len (get moves -1))))))
    (<- stored ClusterState (state-from-kv (.table store) now))
    (val resumed (resume-after-downtime stored now))
    (val state (get resumed 0))
    (val gap (get resumed 1))
    ;; ずらした時計(worker の最後の連絡・task の lease・Rollout の段の起点)と生きていた時刻を、受け付けを始める前に耐久にする
    ;; (durable_kv.resume-writes)。
    (<- writes dict (resume-writes (.table store) state))
    (<- (durable-persist store writes))
    (when (> gap 0)
      (<- (slog (.format "coordinator: 止まっていた {:.1f} 秒を、進行中の Rollout の段と task の lease の時間に数えない" (/ gap 1000)))))
    (val recovery (.recovery store))
    (when recovery
      (<- (slog (.format "coordinator: 置き場の読み直しで最後の読めない行を捨てた: {}" recovery))))
    (return state))
  (<- (durable-load store))
  (<- legacy (| ClusterState None) (legacy-state state-file now))
  ;; 置き場の無いところから起きた: task の id の頭を起動ごとに違う物にする(前の coordinator の id を振り直さない — #757)。
  (when (is legacy None)
    (return (ClusterState :started-ms now :task-prefix (fresh-task-prefix now))))
  (<- kv dict (full-kv legacy))
  (.replace-table store kv)
  (<- (durable-checkpoint store))
  (<- (slog (.format "coordinator: 以前の形の状態を追記の log の置き場へ移した(Service {}・盤 {} 行・版 {})"
                     (len legacy.jobs) (len legacy.board) legacy.revision)))
  legacy)


(defk running-commit []
  {:pre [] :post [(: % (| str None))] :tags {:context "coordinator" :role "main"}}
  "この coordinator の process が走っている doeff の版を、起動の時に 1 度、自分の環境変数(名は launch_rules の表 — boot.sh が自己起動の
   root を選んだ版)から読むため。GET /state の答えに載せ、版上げの Program が「新しい版の coordinator が答えた」を宣言でなく答えた
   process で判じる(#3772)。無い・空なら None(空の文字を版として載せない)。環境の読みは汎用の効果 ReadEnvironment — 本番の入口は
   subprocess-handler、模擬の coordinator の Pod は模擬の Pod の環境で答える。"
  (<- name str (coordinator-commit-env-name))
  (<- found (get tuple #(EnvEntry ...)) (ReadEnvironment #(name)))
  (val values (tuple (gfor e found :if (and (= e.name name) e.value) e.value)))
  (if values (get values 0) None))


(defk with-running-commit [state]
  {:pre [(: state ClusterState)] :post [(: % ClusterState)] :tags {:context "coordinator" :role "main"}}
  "読み直した状態に、この process が走っている doeff の版(running-commit)を載せるため — 本番の入口と模擬の coordinator の Pod が
   同じこの 1 つを通る(読み方のコピーを 2 か所に持たない・#3772)。"
  (<- commit (| str None) (running-commit))
  (replace state :running-commit commit))


(defk state-on-start [state-file store]
  {:pre [(: state-file str) (: store DurableStore)] :post [(: % ClusterState)] :tags {:context "coordinator" :role "main"}}
  "起動の時刻を時計の effect で読み、その時刻で置き場から状態を読み直し、この process が走っている doeff の版を載せるため(時計と
   環境の答え手は入口が被せる)。"
  (<- now int (now-epoch-ms))
  (<- state ClusterState (load-state state-file store now))
  (<- started ClusterState (with-running-commit state))
  started)


;; --- composition root ------------------------------------------------------------------


(deff main []  ; defk にできない: console script の main(`hy -m doeff_cluster.coordinator.entry.main` の __main__ と boot.sh が素の関数として呼ぶ)
  {:pre [] :post [(: % None)] :tags {:context "coordinator" :role "main"}}
  "coordinator の process の入口: 起動の引数から置き場と受け口を組み立て、置き場から状態を読み直して、本番の handler の組の上で調停ループを回すため。"
  (setv parser (argparse.ArgumentParser :description "doeff worker の coordinator(実験)"))
  (.add-argument parser "--state-file" :required True)
  (.add-argument parser "--port" :type int :default 8080)
  (.add-argument parser "--naming" :default "{}"
                 :help "外の系と取り交わす名(JSON: ownerAnnotation・ownerScope・nodeCapabilities)— cluster_model.ClusterNaming")
  ;; worker の生死の出来事(#3864)を出す知らせの broker。既定は無い — 本番の manifest は Redis の URL を、Redis の無い機体の本物の
  ;; process のテストは memory(この process の中だけ — 誰にも届かない)を名指す。
  (.add-argument parser "--notice-broker" :required True
                 :help "redis://<host>:<port>/<db> か memory")
  (.add-argument parser "--notice-timeout-seconds" :type float :default None
                 :help "Redis へ繋ぐ・送るの答えを待つ上限(秒)— --notice-broker が Redis の時は必須")
  (.add-argument parser "--notice-retry-seconds" :type float :default None
                 :help "Redis の戻りを待つ間だけ繋がるかを試す間隔(秒)— --notice-broker が Redis の時は必須")
  (setv args (.parse-args parser))
  (when (and (!= args.notice-broker "memory") (or (is args.notice-timeout-seconds None) (is args.notice-retry-seconds None)))
    (.error parser "--notice-broker が Redis の時は --notice-timeout-seconds と --notice-retry-seconds が要る(既定なし)"))
  (setv notices (if (= args.notice-broker "memory")
                    (memory-notices (MemoryBroker))
                    (redis-notices args.notice-broker args.notice-timeout-seconds args.notice-retry-seconds)))
  (setv naming (naming-from-json args.naming))
  (setv stop (StopState))
  ;; 受付の箱は合図の受け手より先に作る(合図が箱を起こす — 要求の無い間に眠る待ちを合図の刻に抜ける・#3865)。待ち受けは読み直しの後。
  (setv inbox (RequestInbox args.port (/ (. (ClusterTiming) inbox-reply-ms) 1000.0) :formats ACCEPTED-FORMATS))
  (run (stop-on-signals stop :wake inbox.wake))
  (setv store (WalStore (str (/ (. (Path args.state-file) parent) "wal"))))
  ;; 読み直しの以前の形の file の読みは os の file system・1 行の報告は stderr の slog・起動の時刻は壁時計・自分の環境変数(走っている
  ;; doeff の版)は本物の process の handler が答える。
  (setv state (run (scheduled (with_handlers [slog-handler os-file-handler subprocess-handler (sync-time-handler)]
                                (state-on-start args.state-file store)))))
  (.start inbox)
  ;; k8s の API は Pod の ServiceAccount の token が在る時だけ(手元の coordinator では Rollout の Deployment の観測が Unknown のまま)。
  ;; Rollout の相手の Deployment と、worker の置かれた Node は時間で読みに行かず、list の後の watch で見張る(ObjectWatches — 種類ごとに
  ;; 1 つ。変化を受け渡したら受付の箱を起こす・#3868・#4070)。見張りは調停ループの外の thread で走り、ループは待たない(#2807)。
  ;; 台数の変更は 3 秒で打ち切る。
  (setv kube (if (KubeClient.available)
                 (kube-api (KubeClient KubeUnavailable :timeout 3.0) (ObjectWatches) (ObjectWatches) inbox.wake)
                 (kube-unavailable "k8s の ServiceAccount の token が無い(Pod の外の coordinator)" (ObjectWatches) (ObjectWatches))))
  (print (.format "coordinator: :{} で受けます(Service {}・task {}・盤 {} 行・Rollout {}・版 {}・k8s {}・doeff {})"
                  args.port (len state.jobs) (len state.tasks) (len state.board) (len state.rollouts) state.revision
                  (if (KubeClient.available) "あり" "なし") (or state.running-commit "版を読めない")) :file sys.stderr :flush True)
  ;; handler の組は coordinator_handler_sets の値(本番の組)。
  (run (scheduled (with_handlers (production-handlers inbox store stop kube notices) (run-coordinator state (ClusterTiming) naming))))
  (print "coordinator: 止まりました" :file sys.stderr :flush True))


(when (= __name__ "__main__")
  (main))
