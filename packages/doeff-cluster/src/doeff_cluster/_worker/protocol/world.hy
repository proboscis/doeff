;;; worker の世界の観測のまとめ local-host(handlers.hy から移した・#2469)— 判断(worker/core/policy)が拍ごとに問う ObserveWorld に、
;;; 各言い換え(外側に置く)の観測を問うて 1 つの WorldView にまとめて答える: 版ごとのコードの木 = worker/protocol/code_store(#2466)・
;;; 実行環境の root = worker/protocol/env_store(#2467)・job の子 process = worker/protocol/process_host(#2464)・入口の検め =
;;; worker/protocol/probes(#2465)・root ごとの待ちの子 = worker/protocol/warm_host(#3646)・この Pod の止まり = worker/protocol/leaving_observer
;;; (AL-D2p)・機体の memory の逼迫と jobs の層の空き = worker/protocol/memory_observer・手元に無い Program の file = worker/protocol/coordinator_link。I/O を持たない。
(require doeff-hy.macros [defhandler <- val])
(val MODULE-TAGS {:context "worker" :role "protocol"})
(import doeff_cluster._worker.intent.worker_model [ObserveWorld WorldView EnvDisk MemoryStops
])
(import doeff_cluster._worker.protocol.observations [ObserveProcesses ObserveCode ObserveEnvs ObserveEnvDisk ObserveProbes ObserveWarmChildren
  ObserveLeaving ObserveMemoryTight ObserveMemoryKills ObserveMemoryStops ObserveJobsRoom ObserveUrgentJobsRoom ObservePrograms])


(defhandler local-host
  ;; ObserveWorld の答え = 各言い換えの観測のまとめ(頭の註)。
  (ObserveWorld []
    (<- codes tuple (ObserveCode))
    (<- envs tuple (ObserveEnvs))
    (<- processes tuple (ObserveProcesses))
    (<- probed tuple (ObserveProbes))
    (<- disk EnvDisk (ObserveEnvDisk))
    (<- children tuple (ObserveWarmChildren))
    (<- leaving bool (ObserveLeaving))
    (<- tight bool (ObserveMemoryTight))
    (<- killed frozenset (ObserveMemoryKills))
    (<- stops MemoryStops (ObserveMemoryStops))
    (<- room (| int None) (ObserveJobsRoom))
    (<- urgent-room (| int None) (ObserveUrgentJobsRoom))
    (<- gaps tuple (ObservePrograms))
    (resume (WorldView (+ codes envs) processes probed :env-disk disk :warm-children children :leaving leaving :memory-tight tight
                       :memory-killed killed :memory-stops stops :jobs-room room :urgent-jobs-room urgent-room :program-gaps gaps))))
