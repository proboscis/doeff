;; worker の入口の検めの言い換え(worker/protocol/probes — #2465)を検で使う道具。
;;   probe-settings … 検の dir に ProbeSettings を作る(hy は検の venv の物)。
;;   run-probes     … 筋書きの Program を probe-host と本物の答え手(subprocess-handler・os-file-handler)の下で 1 回の run で回す — 検めの記録は
;;                    handler の session の値なので、積んでから答えを見るまでを 1 本の Program で行う。
;;   observed       … 検めが終わるまで観測する(上限 60 秒)。
(require doeff-hy.macros [defk <- val var])
(import sys)
(import time)
(import pathlib [Path])
(import doeff [run with-handlers])
(import doeff_core_effects.handlers [slog-handler state])
(import doeff_core_effects.os_file [os-file-handler])
(import doeff_core_effects.os_process [subprocess-handler])
(import doeff_time [sync-time-handler])
(import doeff_cluster.shared.core.job_rules [spec-hash])
(import doeff_cluster.shared.intent.job_model [JobSpec])
(import doeff_cluster.worker.intent.worker_model [CodeLayout ProbeState ProbeView])
(import doeff_cluster.worker.protocol.observations [ObserveProbes])
(import doeff_cluster.worker.core.probe_rules [PROBE-SECONDS])
(import doeff_cluster.worker.protocol.probes [ProbeSettings probe-host])

(val HY (str (/ (. (Path sys.executable) parent) "hy")))


(defk probe-settings [base [timeout-seconds PROBE-SECONDS]]
  {:pre [(: base Path) (: timeout-seconds (| int float))] :post [(: % ProbeSettings)] :tags {:context "doeff-cluster-test" :role "program"}}
  "検の dir に入口の検めの設定を作るため(hy は検の venv の物・probe-dir は検の dir の下)。"
  (ProbeSettings :python sys.executable :hy-command HY :uv "uv" :layout (CodeLayout) :probe-dir (str (/ base "probe"))
                 :timeout-seconds timeout-seconds))


(defk observed [spec]
  {:pre [(: spec JobSpec)] :post [(: % ProbeView)] :tags {:context "doeff-cluster-test" :role "program"}}
  "検めが終わる(待ちでも走りでもない)まで 0.05 秒ずつ観測し、その spec の答えを返すため(上限 60 秒)。"
  (val key (spec-hash spec))
  (val deadline (+ (time.monotonic) 60))
  (var found None)
  (while (and (is found None) (< (time.monotonic) deadline))
    (<- views tuple (ObserveProbes))
    (for [view views]
      (when (and (= view.spec-hash key) (not-in view.state #(ProbeState.RUNNING ProbeState.QUEUED))) (:= found view)))
    (when (is found None) (time.sleep 0.05)))
  (when (is found None) (raise (AssertionError "検めが 60 秒で終わらない")))
  found)


(defn #^ object run-probes [#^ ProbeSettings settings #^ object program]  ; defk にできない: 検が Program の外から本物の答え手の組で 1 回走らせる入口
  "筋書きの Program を probe-host と本物の答え手の下で 1 回の run で回す(with-handlers の並びは先頭が外側 — 検めの記録は外側の state が持つ)。"
  (run (with-handlers [(state) (sync-time-handler) slog-handler os-file-handler subprocess-handler (probe-host settings)] program)))
