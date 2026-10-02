;; 版上げの下見(#2788): 本番の coordinator の今の版(doeff ec727f20)が本物の置き場(WalStore — snapshot + log の file)に残した状態を、
;; この版の起動の読み(coordinator.entry.main の load-state)で読み直せること。読めなければ、版上げした coordinator は起きた所で落ちるか、
;; Service・worker・task・盤を失う。
;;
;; 置き場の file は tests/fixtures/coordinator_state_ec727f20/wal(作り方 = 同じ dir の README.md — ec727f20 の模擬の coordinator で service
;; beacon を動かし、切り離した task を 1 本 終えて保持させ、盤に 1 行書き、snapshot を取ってから起き直して少し回した)。本番の状態の file では
;; ない。load-state は読み直しの時に置き場へ書く(鍵の移し・止まっていた時間のずらし)ので、写しを読む。
(require doeff-hy.macros [deftest <- val])
(import shutil)
(import pathlib [Path])
(import doeff [with-handlers])
(import doeff_core_effects.handlers [slog-handler])
(import doeff_core_effects.os_file [os-file-handler])
(import doeff_cluster.coordinator.intent.cluster_model [ClusterState])
(import doeff_cluster.foundation.wal_store [WalStore])
(import doeff_cluster.coordinator.entry.main [load-state])
(import doeff_cluster.coordinator.core.api_policy [tick])
(import doeff_cluster.coordinator.protocol.request_bodies [responded])
(import doeff_cluster.coordinator.protocol.durable_kv [full-kv])
(import doeff_cluster.shared.protocol.inbox [http-request])
(import doeff_cluster.shared.intent.protocol [ClusterTiming])
(import doeff_cluster.sim.local [SIM-START-MS])

(val FIXTURE (/ (. (Path __file__) (resolve) parent) "fixtures" "coordinator_state_ec727f20" "wal"))
;; 読み直す時刻: 置き場を作った模擬の時計(SIM-START-MS から数十秒)の 1 時間後 — 止まっていた間の時計のずらしも通る。
(val NOW (+ SIM-START-MS 3600000))


(deftest test-the-current-coordinator-reads-the-state-the-deployed-version-wrote [tmp-path]
  (shutil.copytree FIXTURE (/ tmp-path "wal"))
  (val store (WalStore (str (/ tmp-path "wal"))))
  ;; load-state は Program(1 行の報告は slog・以前の形の file の読みは file system の effect — 入口と同じ答え手を並べる)。
  (<- state ClusterState (with-handlers [slog-handler os-file-handler] (load-state (str (/ tmp-path "state.json")) store NOW)))
  ;; 宣言した Service・名乗った worker・終えて保持中の切り離した task・盤の行が、旧い版の置き場から読める。
  (assert (= (lfor job state.jobs job.spec.name) ["beacon"]) (lfor job state.jobs job.spec.name))
  (assert (in "sim-worker" state.workers) (sorted state.workers))
  (val kept (lfor task (.values state.tasks) :if (= task.key "k-done") task))
  (assert (= (lfor task kept #(task.phase task.detached (is-not task.result None))) [#("finished" True True)]) kept)
  (assert (in "note/a" state.board) (sorted state.board))
  (assert (in "beacon/a" state.board) (sorted state.board))
  ;; 読んだ状態で 1 拍の調停が回り、状態の画面(GET /state)と Service の画面が答える。
  (val ticked (! (tick state NOW (ClusterTiming))))
  ;; responded の答え = #(次の状態 status 返事)。
  (val state-answer (responded ticked (http-request "GET" "/state" {} None :actor "test") NOW (ClusterTiming)))
  (assert (= (get state-answer 1) 200) state-answer)
  (val service-answer (responded ticked (http-request "GET" "/resources/Service/beacon" {} None :actor "test") NOW (ClusterTiming)))
  (assert (= (get service-answer 1) 200) service-answer)
  ;; この版の綴りで置き場へ書き戻せる(次の checkpoint の形)。
  (<- kv dict (full-kv ticked))
  (assert (in "task/" (.join " " kv)) (sorted kv)))
