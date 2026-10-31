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
(import doeff [Program with-handlers])
(import doeff_core_effects [slog-handler state])
(import doeff_core_effects [os-file-handler])
(import doeff_core_effects [scheduled])
(import doeff_core_effects [subprocess-handler])
(import doeff_core_effects [EnvEntry EnvMode])
(import doeff_time [sync-time-handler])
(import doeff_cluster [HOST-CONTRACT])
(import doeff_cluster [JobSpec])
(import doeff_cluster [CodeLayout StartJob ReapJob ProcessView WorkerPolicy]
        doeff_cluster._worker.intent.worker_model [Outcome])
(import doeff_cluster [ObserveProcesses])
(import doeff_cluster._worker.core.launch [JobLaunch job-launch spec-program-file CHILD-ENV-ALLOWED CHILD-ENV-PREFIXES])
(import doeff_cluster [shim-spans]
        doeff_cluster._worker.core.shim_timing [ShimSpans])
(import doeff_cluster [process-host]
        doeff_cluster._worker.protocol.process_host [HostSettings])
(import pytest)

;; この file の待ちは実時間が対象(何が対象かは名簿 tests/realtime_tests.toml のこの file の行)— realtime を名乗る file の中の眠りを、
;; 検の眠りの規則(doeff-linter の DOEFF211)は当てない。
(val pytestmark pytest.mark.realtime)


(defk host-settings [state [hy-command "hy"] [extra-env {}] [layout (CodeLayout)] [policy (WorkerPolicy)] [generation "0b6f5e2a9c1d"]]
  {:pre [(: state Path) (: hy-command str) (: extra-env dict) (: layout CodeLayout) (: policy WorkerPolicy) (: generation str)]
   :post [(: % HostSettings)] :tags {:context "doeff-cluster-test" :role "program"}}
  "検の state dir に、main と同じ置き方(logs・jobs・programs)と同じ shim の時間の導き方(policy から shim-spans — 既定は本番の方針)で
   job の子 process の設定を作るため。generation = log の名に入れる世代の印(既定は検の 1 つの世代 — 本番は places の WorkerPlaces.generation)。"
  (<- shim ShimSpans (shim-spans policy))
  (HostSettings :log-dir (str (/ state "logs")) :generation generation :jobs-dir (str (/ state "jobs")) :program-dir (str (/ state "programs"))
                :python sys.executable :hy-command hy-command
                :extra-env (tuple (gfor k (sorted extra-env) (EnvEntry :name k :value (get extra-env k))))
                :layout layout :program-env HOST-CONTRACT.program-env :shim shim :warm-dir (str (/ state "warm"))
                :notice-env HOST-CONTRACT.notice-env))


(defk launched [settings spec code-path instance attempt]
  {:pre [(: settings HostSettings) (: spec JobSpec) (: code-path str) (: instance str) (: attempt int)] :post [(: % tuple)]
   :tags {:context "doeff-cluster-test" :role "program"}}
  "子 process を起こさずに、process-host が起こす形の #(argv cwd 子の環境変数の dict)を返すため — 環境変数は子が実際に受ける全部
   (EXTEND は今の process の環境を継いで重ねた物・REPLACE は許可表で絞った物)。"
  (val allowed (dfor #(k v) (.items os.environ)
                     :if (or (in k CHILD-ENV-ALLOWED) (any (gfor p CHILD-ENV-PREFIXES (.startswith k p)))) k v))
  (<- plan JobLaunch (job-launch spec code-path instance attempt :entry spec.entry :python settings.python :hy-command settings.hy-command
                                 :extra-env (dfor e settings.extra-env e.name e.value) :layout settings.layout :allowed-env allowed
                                 :worker-pid (os.getpid)
                                 :program-path (if spec.program (str (! (spec-program-file (Path settings.program-dir) spec))) None)
                                 :program-env settings.program-env
                                 :work-dir (+ settings.jobs-dir "/" (.replace spec.name "/" "_"))
                                 :shim-grace-ms settings.shim.shim-grace-ms :notice-env settings.notice-env))
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


(defk run-on-host [settings program [around #()]]
  {:pre [(: settings HostSettings) (: program Program) (: around tuple)] :post [(: % (| int tuple ProcessView))] :tags {:context "doeff-cluster-test" :role "entry"}}
  "Program を process-host と本物の答え手の下で回し、その答えを返すため。around = process-host と本物の答え手の間に置く handler(反例 —
   本物へ渡す前に StartProcess を書き換える壊した handler など)。"
  ;; with-handlers の並びは先頭が外側。process-host の session の値(子の表)は外側の state が持つ。scheduler は本番と同じく外側に置く
  ;; (process-host は回収した作業 dir の消しを拍の外の task で走らせる)。
  (<- answer (scheduled (with-handlers [(state) (sync-time-handler) slog-handler os-file-handler subprocess-handler #* around (process-host settings)]
                                       program)))
  answer)
