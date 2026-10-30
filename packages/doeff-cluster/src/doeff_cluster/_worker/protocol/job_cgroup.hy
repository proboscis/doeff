;;; 子の job を兄弟の cgroup へ置く答え手 job-cgroup-placer(card ki-ed9dca403f58 — 判断は worker/core/job_cgroup)。worker の本体が出す
;;; 子 process の効果(StartProcess・RunProcess)の argv を、兄弟の cgroup へ移ってから exec する sh で包み、外の答え手(本番は
;;; subprocess-handler)へ回す。worker の入口(worker/entry/main)は、環境変数 DOEFF_WORKER_JOB_CGROUP が在る時だけ、この答え手を
;;; subprocess-handler のすぐ内側に並べる — process-host・warm-host・probe-host・code-host・env-host の起こす子はすべてここを通る。
;;; 待ちの子から分ける task(ForkFromWarm)は、包んで起こした待ちの子の cgroup を継ぐ。
(require doeff-hy.macros [defhandler <- val])
(val MODULE-TAGS {:context "worker" :role "protocol"})
(import dataclasses)
(import doeff_core_effects [RunProcess StartProcess])
(import doeff_cluster._worker.core.job_cgroup [placed-argv])


(defhandler job-cgroup-placer [#^ str procs]
  ;; 引数に残す理由: 兄弟の cgroup の cgroup.procs の path は起動の時に 1 度だけ決まる入口の値(worker/entry/main)。
  (StartProcess [argv]
    (<- placed tuple (placed-argv procs argv))
    (reperform (dataclasses.replace effect :argv placed)))
  (RunProcess [argv]
    (<- placed tuple (placed-argv procs argv))
    (reperform (dataclasses.replace effect :argv placed))))
