;; worker の世界の観測のまとめ(worker/protocol/world の local-host — #2469): ObserveWorld に、各言い換えの観測(コードの木・実行環境の
;; root・子 process・入口の検め・root の置き場の disk)を問うて 1 つの WorldView で答える。言い換えは小さな答え手で代える。
(require doeff-hy.macros [defhandler deftest <- val])
(val MODULE-TAGS {:context "doeff-cluster-test" :role "test"})
(import doeff [run with-handlers])
(import doeff_cluster.worker.intent.worker_model [CodeState CodeView EnvDisk ObserveWorld 
 WorldView])
(import doeff_cluster.worker.protocol.observations [ObserveProcesses ObserveCode ObserveEnvs ObserveEnvDisk ObserveProbes])
(import doeff_cluster.worker.protocol.world [local-host])

(val CODE (CodeView "rev1" CodeState.READY :path "/c/rev1"))
(val ENV (CodeView "env-0123" CodeState.PREPARING))
(val DISK (EnvDisk 10 5 (frozenset #("env-0123"))))


(defhandler observed
  ;; 各言い換えの観測の代わり。
  (ObserveCode [] (resume #(CODE)))
  (ObserveEnvs [] (resume #(ENV)))
  (ObserveProcesses [] (resume #()))
  (ObserveProbes [] (resume #()))
  (ObserveEnvDisk [] (resume DISK)))


(deftest test-the-world-gathers-every-observation
  (val world (run (with-handlers [observed local-host] (ObserveWorld))))
  (assert (isinstance world WorldView) world)
  (assert (= world (WorldView #(CODE ENV) #() #() :env-disk DISK)) world))
