;; worker の世界の観測のまとめ(worker/protocol/world の local-host — #2469): ObserveWorld に、各言い換えの観測(コードの木・実行環境の
;; root・子 process・入口の検め・root の置き場の disk・root ごとの待ちの子 — #3646・Pod の止まり — AL-D2p)を問うて 1 つの WorldView で
;; 答える。言い換えは小さな答え手で代える(止まりの印の本物の答え手との往復は test_leaving_mark.hy)。
(require doeff-hy.macros [defhandler deftest <- val])
(val MODULE-TAGS {:context "doeff-cluster-test" :role "test"})
(import doeff [run with-handlers])
(import doeff_cluster [CodeState CodeView ObserveWorld WorldView]
        doeff_cluster._worker.intent.worker_model [EnvDisk ProgramGap WarmChildView])
(import doeff_cluster [ObserveProcesses ObserveCode ObserveEnvs ObserveEnvDisk ObserveProbes ObserveWarmChildren ObserveLeaving ObserveMemoryTight ObserveMemoryKills ObserveMemoryStops ObserveJobsRoom ObserveUrgentJobsRoom ObservePrograms ObserveCompartments MemoryStops])
(import doeff_cluster [local-host])
(import doeff_cluster._shared.intent.compartment_model [HeldCompartment])

(val CODE (CodeView "rev1" CodeState.READY :path "/c/rev1"))
(val ENV (CodeView "env-0123" CodeState.PREPARING))
(val DISK (EnvDisk :free 10 :sweep-wanted False :pinned (frozenset #("env-0123"))))
(val CHILD (WarmChildView :key "env-0123" :pid 77 :started-ms 5))
(val GAP (ProgramGap :job "svc"))
(val HELD (HeldCompartment :use "tasks" :layer "jobs" :memory-mib 896 :memory-limit-mib 896 :cpu-millis 4000))


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
  (ObserveMemoryKills [] (resume (frozenset #(#("svc" 10)))))
  (ObserveMemoryStops [] (resume (MemoryStops)))
  (ObserveJobsRoom [] (resume (* 300 1024 1024)))
  (ObserveUrgentJobsRoom [] (resume (* 900 1024 1024)))
  (ObservePrograms [] (resume #(GAP)))
  (ObserveCompartments [] (resume #(HELD))))


(deftest test-the-world-gathers-every-observation
  (val world (run (with-handlers [observed local-host] (ObserveWorld))))
  (assert (isinstance world WorldView) world)
  (assert (= world (WorldView #(CODE ENV) #() #() :env-disk DISK :warm-children #(CHILD) :leaving True :memory-tight True
                         :memory-killed (frozenset #(#("svc" 10))) :jobs-room (* 300 1024 1024) :urgent-jobs-room (* 900 1024 1024) :program-gaps #(GAP)
                         :compartments #(HELD))) world))
