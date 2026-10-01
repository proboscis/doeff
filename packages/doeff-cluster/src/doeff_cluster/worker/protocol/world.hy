;;; worker の世界の観測のまとめ local-host(handlers.hy から移した・#2469)— 判断(worker/core/policy)が拍ごとに問う ObserveWorld に、
;;; 各言い換え(外側に置く)の観測を問うて 1 つの WorldView にまとめて答える: 版ごとのコードの木 = worker/protocol/code_store(#2466)・
;;; 実行環境の root = worker/protocol/env_store(#2467)・job の子 process = worker/protocol/process_host(#2464)・入口の検め =
;;; worker/protocol/probes(#2465)。I/O を持たない。
(require doeff-hy.macros [defhandler <- val])
(val MODULE-TAGS {:context "doeff-cluster" :role "protocol"})
(import doeff_cluster.worker.intent.worker_model [ObserveWorld WorldView EnvDisk 
])
(import doeff_cluster.worker.protocol.observations [ObserveProcesses ObserveCode ObserveEnvs ObserveEnvDisk ObserveProbes])


(defhandler local-host
  ;; ObserveWorld の答え = 各言い換えの観測のまとめ(頭の註)。
  (ObserveWorld []
    (<- codes tuple (ObserveCode))
    (<- envs tuple (ObserveEnvs))
    (<- processes tuple (ObserveProcesses))
    (<- probed tuple (ObserveProbes))
    (<- disk EnvDisk (ObserveEnvDisk))
    (resume (WorldView (+ codes envs) processes probed :env-disk disk))))
