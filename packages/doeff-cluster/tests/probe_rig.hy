;; worker の入口の検めの言い換え(worker/protocol/probes — #2465)を検で使う道具。
;;   probe-settings … 検の dir に ProbeSettings を作る(hy は検の venv の物)。
;;   run-probes     … 筋書きの Program を probe-host と本物の答え手(subprocess-handler・os-file-handler)の下で 1 回の run で回す — 検めの記録は
;;                    handler の session の値なので、積んでから答えを見るまでを 1 本の Program で行う。
;;   observed       … 検めが終わるまで観測する(上限 60 秒)。
;;   bytecode-kept-out … 起こす検めの子の環境に PYTHONDONTWRITEBYTECODE を足す(実行環境の検めは許可表で PYTHON* を継がない)。
(require doeff-hy.macros [defk defhandler <- val var])
(import sys)
(import time)
(import pathlib [Path])
(import doeff [Program with-handlers])
(import doeff_core_effects.handlers [slog-handler state])
(import doeff_core_effects.os_file [os-file-handler])
(import doeff_core_effects.os_process [subprocess-handler])
(import doeff_core_effects.process_effects [EnvEntry StartProcess])
(import doeff_time [sync-time-handler])
(import doeff_cluster.shared.core.job_rules [spec-hash])
(import doeff_cluster.shared.intent.job_model [JobSpec])
(import doeff_cluster.worker.intent.worker_model [CodeLayout ProbeState ProbeView WorkerPolicy])
(import doeff_cluster.worker.protocol.observations [ObserveProbes])
(import doeff_cluster.worker.core.probe_rules [PROBE-SECONDS])
(import doeff_cluster.worker.core.shim_timing [ShimSpans shim-spans])
(import doeff_cluster.worker.protocol.probes [ProbeSettings probe-host])
(import pytest)

;; この file の待ちは実時間が対象(何が対象かは名簿 tests/realtime_tests.toml のこの file の行)— realtime を名乗る file の中の眠りを、
;; 検の眠りの規則(doeff-linter の DOEFF211)は当てない。
(val pytestmark pytest.mark.realtime)

(val HY (str (/ (. (Path sys.executable) parent) "hy")))


(defk probe-settings [base [timeout-seconds PROBE-SECONDS] [policy (WorkerPolicy)]]
  {:pre [(: base Path) (: timeout-seconds (| int float)) (: policy WorkerPolicy)] :post [(: % ProbeSettings)]
   :tags {:context "doeff-cluster-test" :role "program"}}
  "検の dir に入口の検めの設定を作るため(hy は検の venv の物・probe-dir は検の dir の下・shim の時間は main と同じく policy から
   shim-spans で導く — 既定は本番の方針)。"
  (<- shim ShimSpans (shim-spans policy))
  (ProbeSettings :python sys.executable :hy-command HY :layout (CodeLayout) :probe-dir (str (/ base "probe"))
                 :shim shim :timeout-seconds timeout-seconds))


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


(defhandler bytecode-kept-out
  ;; 検の差し込み: 検めの shim の環境に PYTHONDONTWRITEBYTECODE を足してから本物へ渡す。実行環境の検めの子は許可表で組んだ環境(REPLACE)で
  ;; 起き、PYTHON* を継がないので、根の conftest の固定が届かず checkout の中に bytecode を書く(前は fake の uv の包みが立てていた —
  ;; 本体を uv run を挟まずに起こす card ki-869cdf21ec85 で包みを通らなくなった)。test_task_venv_on_both_paths.hy の同じ名の差し込みと同じ訳。
  (StartProcess [argv cwd env env-mode env-drop stdout-path stderr-path process-group hold-stdin reap-group]
    (<- answer (StartProcess :argv argv :cwd cwd :env (+ (or env #()) #((EnvEntry :name "PYTHONDONTWRITEBYTECODE" :value "1")))
                             :env-mode env-mode :env-drop env-drop :stdout-path stdout-path :stderr-path stderr-path
                             :process-group process-group :hold-stdin hold-stdin :reap-group reap-group))
    (resume answer)))


(defk run-probes [settings program]
  {:pre [(: settings ProbeSettings) (: program Program)] :post [(: % (| tuple list ProbeView))] :tags {:context "doeff-cluster-test" :role "entry"}}
  "筋書きの Program を probe-host と本物の答え手の下で回し、その答えを返すため(with-handlers の並びは先頭が外側 — 検めの記録は外側の
   state が持つ)。答えの型は筋書きで決まるので、呼び手は (<- 名 型 (run-probes …)) で型を書いて受ける。"
  (<- answer (with-handlers [(state) (sync-time-handler) slog-handler os-file-handler subprocess-handler bytecode-kept-out
                              (probe-host settings)] program))
  answer)
