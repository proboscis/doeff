;;; doeff worker の composition root。宣言(file か coordinator)を読み、job と task を子 process として管理する。
;;;
;;;   hy -m doeff_cluster.main --desired desired.json --repo . --state-dir DIR
;;;   hy -m doeff_cluster.main --coordinator URL --name NAME [--labels k=v,…] --repo REPO --state-dir DIR
(import argparse)
(import signal)
(import sys)
(import pathlib [Path])
(import doeff [run])
(import doeff_core_effects.handlers [await-handler slog-handler])
(import doeff_core_effects.scheduler [scheduled])
(import doeff_time [async-time-handler])
(import .handlers [CodeStore EnvStore CoordinatorLink ProcessHost ProbeStore coordinator-desired desired-file local-host
                   status-file status-to-coordinator stop-flag lease-release-coordinator lease-release-none])
(import .remote_model [current-versions])
(import .cluster_model [ClusterTiming])
(import .worker [run-worker])
(import .worker_model [WorkerPolicy CodeLayout])


(defclass StopState []
  (defn __init__ [self] (setv self.requested False)))


(defn #^ dict parse-labels [#^ str text]
  (dict (gfor kv (.split text ",") :if kv (.split kv "=" 1))))


(defn main []
  (setv parser (argparse.ArgumentParser :description "doeff worker(実験)"))
  (setv source (.add-mutually-exclusive-group parser :required True))
  (.add-argument source "--desired" :help "job の宣言(JSON の file)")
  (.add-argument source "--coordinator" :help "job を割り当てる coordinator の URL。`,` で並べると前から順に試す(Mac は LAN・tailnet の順)")
  (.add-argument parser "--name" :help "coordinator に名乗る worker の名前")
  (.add-argument parser "--labels" :default "" :help "k=v,k=v")
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
  (.add-argument parser "--overlay-path" :default ""
                 :help "「<base>~<revision>」の木で revision の物を重ねる dir(空 = 重ねない)— worker_model.CodeLayout")
  (.add-argument parser "--repo-keys" :default ""
                 :help "実行環境の task の許可表(JSON の file — clone してよい URL → deploy key の file。空 = どの URL も断る)")
  (.add-argument parser "--uv" :default "uv" :help "実行環境の準備と子の起動に使う uv の命令")
  (.add-argument parser "--env-min-free" :type int :default 0 :help "実行環境の準備を始める空きの下限(byte)")
  (.add-argument parser "--tools" :default "" :help "この worker が名乗る道具(名=版,… — 実行環境の宣言の tools と照らす)")
  (setv args (.parse-args parser))
  (setv layout (CodeLayout :import-roots (tuple (gfor r (.split args.import-roots ",") :if r r))
                           :overlay-path (or args.overlay-path None)
                           :base-paths (tuple (gfor p (.split args.base-pythonpath ",") :if p p))))
  (when (and args.coordinator (not args.name))
    (.error parser "--coordinator には --name が要る"))
  (setv state-dir (Path args.state-dir)
        hy-command (str (/ (. (Path sys.executable) parent) "hy"))
        codes (CodeStore args.repo (str (/ state-dir "code")) (if args.no-warm None hy-command) :layout layout)
        ;; 子 process(service の env)が coordinator と自分の名を知る口。資格は渡さない。
        host (ProcessHost (str (/ state-dir "logs")) hy-command
                          {"DOEFF_WORKER_NAME" (or args.name "local")
                           "DOEFF_WORKER_COORDINATOR" (or args.coordinator "")}
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
  (setv source-handlers
    (if args.coordinator
        (do (setv link (CoordinatorLink args.coordinator args.name (parse-labels args.labels) args.capacity
                                        (int (* args.fence 1000))
                                        :task-dir (str (/ state-dir "tasks")) :versions (current-versions)
                                        :tools (parse-labels args.tools)))
            [(coordinator-desired link) (status-to-coordinator link) (lease-release-coordinator link)])
        [(desired-file args.desired) lease-release-none]))
  (setv program (run-worker policy))
  (for [h [(local-host codes host probes envs) #* source-handlers (status-file (str (/ state-dir "status.json")) codes)
           (stop-flag stop) slog-handler (async-time-handler) (await-handler)]]
    (setv program (h program)))
  (print "worker: 起動します" :file sys.stderr :flush True)
  (run (scheduled program))
  (print "worker: 全 job を回収しました" :file sys.stderr :flush True))


(when (= __name__ "__main__")
  (main))
