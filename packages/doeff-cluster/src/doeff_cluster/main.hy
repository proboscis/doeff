;;; doeff worker の composition root。coordinator から job と task を受け、子 process として管理する。
;;;
;;;   hy -m doeff_cluster.main --coordinator URL --name NAME [--provides a,b] [--exclusive a] --repo REPO --state-dir DIR
;;;
;;; --provides = この worker が提供する能力の名(`,` で並べる)・--exclusive = 専用の能力(provides の一部 — このどれかを要る job / task
;;; だけを受ける)。置き場所の名ではなく能力を名乗る(ADR-DOE-CLUSTER-001 R4b)。旧い --labels は受け付けない。
;;; worker が job を受けるのは coordinator からだけ — 宣言の file から生の entry と args の job を直に起こす口(旧い --desired)は無い
;;; (job は Program の値 1 つ・ADR-DOE-CLUSTER-001 R1・R7)。
(require doeff-hy.macros [defk val])
(import argparse)
(import os)
(import signal)
(import sys)
(import pathlib [Path])
(import doeff [run])
(import doeff_core_effects.handlers [await-handler slog-handler])
(import doeff_core_effects.os_file [os-file-handler])
(import doeff_core_effects.scheduler [scheduled])
(import doeff_time [async-time-handler])
(import .handlers [CodeStore EnvStore CoordinatorLink ProcessHost ProbeStore coordinator-desired local-host
                   status-file status-to-coordinator stop-flag lease-release-coordinator StopState])
(import .process_versions [current-versions])
(import .cluster_model [ClusterTiming capabilities-of])
(import .worker [run-worker])
(import .worker_model [WorkerPolicy CodeLayout])
(import .job_context [worker-context-environ])


(defk passed-environment [names environ]
  {:pre [(: names str) (: environ dict)] :post [(: % dict)]}
  "子 process へ渡す worker の環境変数(名を `,` で並べる)— 機体の設定(家や作業場所の path・預かり所の URL)を job に届けるため。
   実行環境の job の子は worker の環境を許可表でしか継がない(handlers.child-environment)ので、機体の設定は worker が名で宣言する。
   名乗った名が worker の環境に無ければ起動を止める(黙って欠いたまま job を走らせない)。資格の値そのものは渡さない(file の path を渡す)。"
  (val wanted (lfor n (.split names ",") :if (.strip n) (.strip n)))
  (val missing (lfor n wanted :if (not-in n environ) n))
  (when missing
    (raise (ValueError (+ "--pass-env の名が worker の環境に無い: " (.join "," missing)))))
  (dfor n wanted n (get environ n)))


(defn #^ dict parse-labels [#^ str text]
  (dict (gfor kv (.split text ",") :if kv (.split kv "=" 1))))


(defn main []
  (setv parser (argparse.ArgumentParser :description "doeff worker(実験)"))
  (.add-argument parser "--coordinator" :required True
                 :help "job を割り当てる coordinator の URL。`,` で並べると前から順に試す(Mac は LAN・tailnet の順)")
  (.add-argument parser "--name" :required True :help "coordinator に名乗る worker の名前")
  (.add-argument parser "--provides" :default "" :help "提供する能力の名(a,b)")
  (.add-argument parser "--exclusive" :default "" :help "専用の能力(provides の一部・a,b)— このどれかを要る仕事だけを受ける")
  (.add-argument parser "--node" :default "" :help "この worker の置かれた k8s の node の名(coordinator が node の label から能力を導く)")
  (.add-argument parser "--labels" :default None :help "受け付けない(旧い形 — --provides / --exclusive で能力を名乗る)")
  (.add-argument parser "--capacity" :type int :default 10)
  (.add-argument parser "--fence" :type float :default (/ (. (ClusterTiming) fence-ms) 1000)
                 :help "連絡が途絶えて自分の job を止めるまでの秒(最初に coordinator へ届くまで。以後は coordinator の値)")
  (.add-argument parser "--repo" :required True :help "コードを取り出す git repo")
  (.add-argument parser "--state-dir" :required True :help "コードの cache・log・状態の置き場")
  (.add-argument parser "--no-warm" :action "store_true" :help "bytecode の準備を省く")
  (.add-argument parser "--stop-grace" :type float :default 10.0)
  (.add-argument parser "--import-roots" :default "."
                 :help "業務の repo の木の中の import の根(`,` で並べる・前が先)— worker_model.CodeLayout")
  (.add-argument parser "--base-pythonpath" :default ""
                 :help "土台の import の路(機体の絶対 path を `,` で並べる・木の根の後ろ)— worker_model.CodeLayout(pod は空)")
  (.add-argument parser "--repo-keys" :default ""
                 :help "実行環境の task の許可表(JSON の file — clone してよい URL → deploy key の file。空 = どの URL も断る)")
  (.add-argument parser "--uv" :default "uv" :help "実行環境の準備と子の起動に使う uv の命令")
  (.add-argument parser "--env-min-free" :type int :default 0 :help "実行環境の準備を始める空きの下限(byte)")
  (.add-argument parser "--tools" :default "" :help "この worker が名乗る道具(名=版,… — 実行環境の宣言の tools と照らす)")
  (.add-argument parser "--pass-env" :default ""
                 :help "子 process へ渡す worker の環境変数の名(`,` で並べる — 機体の設定の path や URL。無い名は起動を止める)")
  (setv args (.parse-args parser))
  ;; 能力の名乗りを起動の時点で検める(旧い --labels・名として受けられない値・provides の外の exclusive は起動しない)。
  (when (is-not args.labels None)
    (.error parser "旧い --labels は受け付けない — 置き場所の label ではなく、提供する能力を --provides(専用なら --exclusive)で名乗る"))
  (try
    (setv provides (capabilities-of (lfor n (.split args.provides ",") :if n n) "--provides")
          exclusive (capabilities-of (lfor n (.split args.exclusive ",") :if n n) "--exclusive"))
    (except [error ValueError]
      (.error parser (str error))))
  (when (not (<= (set exclusive) (set provides)))
    (.error parser (.format "--exclusive {} は --provides {} の一部で名乗る" (list exclusive) (list provides))))
  (setv layout (CodeLayout :import-roots (tuple (gfor r (.split args.import-roots ",") :if r r))
                           :base-paths (tuple (gfor p (.split args.base-pythonpath ",") :if p p))))
  (setv state-dir (Path args.state-dir)
        hy-command (str (/ (. (Path sys.executable) parent) "hy"))
        codes (CodeStore args.repo (str (/ state-dir "code")) (if args.no-warm None hy-command) :layout layout)
        ;; 子 process(service の env)が coordinator と自分の名を知る口。資格は渡さない。
        host (ProcessHost (str (/ state-dir "logs")) hy-command
                          (| (run (passed-environment args.pass-env (dict os.environ)))
                             (run (worker-context-environ args.coordinator args.name)))
                          :layout layout :uv args.uv)
        ;; 実行環境(runtime env)の root の準備(別の process・worker は再起動しない)。
        envs (EnvStore (str state-dir) hy-command :repo-keys args.repo-keys :uv args.uv :min-free-bytes args.env-min-free)
        ;; 入口の検め(service の job の木を worker の実行環境で読み込めるか — 起こす前に試す)。
        probes (ProbeStore hy-command :layout layout :uv args.uv :probe-dir (str (/ state-dir "probe")))
        policy (WorkerPolicy :stop-grace-ms (int (* args.stop-grace 1000)))
        stop (StopState))
  (defn on-signal [signum frame] (setv stop.requested True))
  (signal.signal signal.SIGTERM on-signal)
  (signal.signal signal.SIGINT on-signal)
  (setv link (CoordinatorLink args.coordinator args.name provides args.capacity
                              (int (* args.fence 1000))
                              :task-dir (str (/ state-dir "tasks")) :versions (current-versions)
                              :tools (parse-labels args.tools) :envs envs :exclusive exclusive :node args.node))
  (setv program (run-worker policy))
  (for [h [(local-host codes host probes envs)
           (coordinator-desired link) (status-to-coordinator link) (lease-release-coordinator link)
           (status-file (str (/ state-dir "status.json")) codes) os-file-handler
           (stop-flag stop) slog-handler (async-time-handler) (await-handler)]]
    (setv program (h program)))
  (print "worker: 起動します" :file sys.stderr :flush True)
  (run (scheduled program))
  (print "worker: 全 job を回収しました" :file sys.stderr :flush True))


(when (= __name__ "__main__")
  (main))
