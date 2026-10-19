;; worker の世界の観測のまとめ(worker/protocol/world の local-host — #2469): ObserveWorld に、各言い換えの観測(コードの木・実行環境の
;; root・子 process・入口の検め・root の置き場の disk・root ごとの待ちの子 — #3646・Pod の止まり — AL-D2p)を問うて 1 つの WorldView で
;; 答える。言い換えは小さな答え手で代える(止まりの印の本物の答え手との往復は test_leaving_mark.hy)。
(require doeff-hy.macros [defhandler deftest <- val])
(val MODULE-TAGS {:context "doeff-cluster-test" :role "test"})
(import doeff [run with-handlers])
(import doeff_cluster.worker.intent.worker_model [CodeState CodeView EnvDisk ObserveWorld WarmChildView
 WorldView])
(import doeff_cluster.worker.protocol.observations [ObserveProcesses ObserveCode ObserveEnvs ObserveEnvDisk ObserveProbes ObserveWarmChildren
  ObserveLeaving ObserveMemoryTight ObserveMemoryKills])
(import doeff_cluster.worker.protocol.world [local-host])

(val CODE (CodeView "rev1" CodeState.READY :path "/c/rev1"))
(val ENV (CodeView "env-0123" CodeState.PREPARING))
(val DISK (EnvDisk :free 10 :sweep-wanted False :pinned (frozenset #("env-0123"))))
(val CHILD (WarmChildView :key "env-0123" :pid 77 :started-ms 5))


(defhandler observed
  ;; 各言い換えの観測の代わり。
  (ObserveCode [] (resume #(CODE)))
  (ObserveEnvs [] (resume #(ENV)))
  (ObserveProcesses [] (resume #()))
  (ObserveProbes [] (resume #()))
  (ObserveEnvDisk [] (resume DISK))
  (ObserveWarmChildren [] (resume #(CHILD)))
  (ObserveLeaving [] (resume True))
  (ObserveMemoryTight [] (resume True))
  (ObserveMemoryKills [] (resume (frozenset #(#("svc" 10))))))


(deftest test-the-world-gathers-every-observation
  (val world (run (with-handlers [observed local-host] (ObserveWorld))))
  (assert (isinstance world WorldView) world)
  (assert (= world (WorldView #(CODE ENV) #() #() :env-disk DISK :warm-children #(CHILD) :leaving True :memory-tight True
                         :memory-killed (frozenset #(#("svc" 10))))) world))
