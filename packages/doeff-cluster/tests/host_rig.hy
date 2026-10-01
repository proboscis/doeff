;; worker の job の子 process の言い換え(worker/protocol/process_host — #2464)を検で使う道具。
;;   launched     … 子 process を起こさずに、job-launch の起こし方と、子が実際に受ける環境変数(EXTEND は今の process の環境を継いだ全部)を返す。
;;   host-settings … 検の state dir に main と同じ置き方(logs・jobs・programs)で HostSettings を作る。
;;   run-on-host  … Program を process-host と本物の答え手(subprocess-handler・os-file-handler)の下で 1 回の run で回す — 子の表は
;;                  handler の session の値なので、起こしてから回収までを 1 本の Program で行う。
(require doeff-hy.macros [defk <- val var])
(import os)
(import time)
(import sys)
(import pathlib [Path])
(import doeff [run with-handlers])
(import doeff_core_effects.handlers [slog-handler state])
(import doeff_core_effects.os_file [os-file-handler])
(import doeff_core_effects.os_process [subprocess-handler])
(import doeff_core_effects.process_effects [EnvEntry EnvMode])
(import doeff_time [sync-time-handler])
(import doeff_cluster.foundation.host_contract [HOST-CONTRACT])
(import doeff_cluster.shared.intent.job_model [JobSpec])
(import doeff_cluster.worker.intent.worker_model [CodeLayout StartJob ReapJob Outcome ProcessView])
(import doeff_cluster.worker.protocol.observations [ObserveProcesses])
(import doeff_cluster.worker.core.launch [JobLaunch job-launch program-file CHILD-ENV-ALLOWED CHILD-ENV-PREFIXES])
(import doeff_cluster.worker.protocol.process_host [HostSettings process-host])


(defk host-settings [state [hy-command "hy"] [extra-env {}] [layout (CodeLayout)] [uv "uv"]]
  {:pre [(: state Path) (: hy-command str) (: extra-env dict) (: layout CodeLayout) (: uv str)] :post [(: % HostSettings)]
   :tags {:context "doeff-cluster-test" :role "program"}}
  "検の state dir に、main と同じ置き方(logs・jobs・programs)で job の子 process の設定を作るため。"
  (HostSettings :log-dir (str (/ state "logs")) :jobs-dir (str (/ state "jobs")) :program-dir (str (/ state "programs"))
                :python sys.executable :hy-command hy-command :uv uv
                :extra-env (tuple (gfor k (sorted extra-env) (EnvEntry :name k :value (get extra-env k))))
                :layout layout :program-env HOST-CONTRACT.program-env))


(defk launched [settings spec code-path instance attempt]
  {:pre [(: settings HostSettings) (: spec JobSpec) (: code-path str) (: instance str) (: attempt int)] :post [(: % tuple)]
   :tags {:context "doeff-cluster-test" :role "program"}}
  "子 process を起こさずに、process-host が起こす形の #(argv cwd 子の環境変数の dict)を返すため — 環境変数は子が実際に受ける全部
   (EXTEND は今の process の環境を継いで重ねた物・REPLACE は許可表で絞った物)。"
  (val allowed (dfor #(k v) (.items os.environ)
                     :if (or (in k CHILD-ENV-ALLOWED) (any (gfor p CHILD-ENV-PREFIXES (.startswith k p)))) k v))
  (<- plan JobLaunch (job-launch spec code-path instance attempt :python settings.python :hy-command settings.hy-command :uv settings.uv
                                 :extra-env (dfor e settings.extra-env e.name e.value) :layout settings.layout :allowed-env allowed
                                 :worker-pid (os.getpid)
                                 :program-path (if spec.program (str (program-file (Path settings.program-dir) spec.program)) None)
                                 :program-env settings.program-env
                                 :work-dir (+ settings.jobs-dir "/" (.replace spec.name "/" "_"))))
  (val overlay (dfor e plan.env e.name e.value))
  #((list plan.argv) plan.cwd (if (= plan.env-mode EnvMode.EXTEND) (| (dict os.environ) overlay) overlay)))


(defk job-ended [spec code-path deadline-seconds]
  {:pre [(: spec JobSpec) (: code-path str) (: deadline-seconds (| int float))] :post [(: % ProcessView)]
   :tags {:context "doeff-cluster-test" :role "program"}}
  "job を 1 本起こし、終わりを観測するまで待って回収し、終わった子の観測を返すため(期限を過ぎたら AssertionError)。"
  (<- (StartJob spec 1 code-path))
  (val deadline (+ (time.monotonic) deadline-seconds))
  (var ended None)
  (while (is ended None)
    (when (> (time.monotonic) deadline) (raise (AssertionError (.format "{} が終わらない" spec.name))))
    (<- views tuple (ObserveProcesses))
    (for [view views]
      (when (and (= view.name spec.name) (is-not view.exit-code None)) (:= ended view)))
    (when (is ended None) (time.sleep 0.1)))
  (<- (ReapJob spec.name ended.pid Outcome.EXITED ended.exit-code))
  ended)


(defn #^ object run-on-host [#^ HostSettings settings #^ object program #^ tuple [around #()]]  ; defk にできない: 検が Program の外から本物の答え手の組で 1 回走らせる入口
  "Program を process-host と本物の答え手の下で 1 回の run で回す。around = process-host と本物の答え手の間に置く handler(反例 — 本物へ
   渡す前に StartProcess を書き換える壊した handler など)。"
  ;; with-handlers の並びは先頭が外側。process-host の session の値(子の表)は外側の state が持つ。
  (run (with-handlers [(state) (sync-time-handler) slog-handler os-file-handler subprocess-handler #* around (process-host settings)] program)))
