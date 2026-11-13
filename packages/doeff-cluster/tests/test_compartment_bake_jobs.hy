;;; bytecode を焼く道具の並べる数は、worker の持つ準備の区画(無ければ task の区画)の CPU の宣言(cpuMillis)から決まる
;;; (ADR-DOE-CLUSTER-005 R3 — CPU の量は区画の cpuMillis だけ・law the-preparation-parallelism-follows-the-compartment)。
;;;   - 判断: 準備の区画(持たない worker は task の区画)の cpuMillis を core へ切り上げた数(1 以上・天井 BAKE-JOBS-CEILING まで)。
;;;     recreate の job の旧い process が動いている間の絞りとは小さい方。
;;;   - 道筋: heartbeat の返事の compartments → 見張りの入れ物 → 本番と同じ観測のまとめ(memory-observer と local-host)の
;;;     WorldView.compartments → plan の PrepareCode・PrepareEnv の compile-jobs(道具の --jobs)。
;;;   失敗ケース(区画を読まない前の形): 準備の action の並べる数は None で、道具は container の CPU の上限(cgroup の cpu.max)から決めた。
;;;   CPU の上限を持たない Pod の worker では、小さい機体でも使える CPU の数(天井 8 まで)で焼き、準備の層の大きさを決めた並列数と食い違う。
(require doeff-hy.macros [defhandler deftest <- val])
(val MODULE-TAGS {:context "doeff-cluster-test" :role "test"})
(import dataclasses [replace])
(import doeff [run with-handlers])
(import doeff_cluster [CodeState CodeView ProcessView WorldView WorkerPolicy JobSpec PrepareCode PrepareEnv ObserveWorld plan])
(import doeff_cluster [ObserveProcesses ObserveCode ObserveEnvs ObserveEnvDisk ObserveProbes ObserveWarmChildren ObserveLeaving ObservePrograms])
(import doeff_cluster [memory-observer local-host])
(import doeff_cluster._shared.intent.compartment_model [HeldCompartment])
(import doeff_cluster._shared.core.compartment_rules [held-compartments-of held-cpu-millis])
(import doeff_cluster._worker.core.bake_plan [BAKE-JOBS-CEILING])
(import doeff_cluster._worker.core.policy [compartment-jobs])
(import doeff_cluster._worker.intent.worker_model [EnvDisk WarmEnv])
(import doeff_cluster._worker.protocol.memory_watch [MemoryCell])

;; 4 core・memory の小さい機体の worker が受け取る heartbeat の返事の compartments: 準備の層は 2 core 分・task の層は 4 core 分。
(val SMALL-NODE-REPLY [{"use" "prepare" "layer" "prepare" "memoryMiB" 640 "memoryLimitMiB" 640 "cpuMillis" 2000}
                       {"use" "services" "layer" "services" "memoryMiB" 896 "memoryLimitMiB" 1792 "cpuMillis" 50}
                       {"use" "tasks" "layer" "jobs" "memoryMiB" 896 "memoryLimitMiB" 896 "cpuMillis" 4000}])
(val CODE-JOB (JobSpec "a" "jobs.a" #() "rev1"))
(val ENV-JOB (JobSpec "e" "jobs.e" #() "rev1" :runtime-env "{\"repos\": []}" :env-key "k1"))
(val WARM (WarmEnv :key "env-k2" :runtime-env "{\"repos\": []}"))


(defhandler nothing-else-observed
  ;; 持つ区画と機体の memory のほかの観測は空(木も root も無い — plan は準備を撃つ)。
  (ObserveCode [] (resume #()))
  (ObserveEnvs [] (resume #()))
  (ObserveProcesses [] (resume #()))
  (ObserveProbes [] (resume #()))
  (ObserveEnvDisk [] (resume (EnvDisk :free 10 :sweep-wanted False :pinned (frozenset))))
  (ObserveWarmChildren [] (resume #()))
  (ObserveLeaving [] (resume False))
  (ObservePrograms [] (resume #())))


(deftest test-the-bake-parallelism-follows-the-compartment-cpu
  (<- held (held-compartments-of SMALL-NODE-REPLY))
  ;; 準備の区画を持つ worker は準備の区画の宣言(task の区画の 4 core ではない — 準備の層の大きさを決めた並列数)。
  (assert (= (compartment-jobs held None) 2) "準備の区画 2000 millicore は 2 並列")
  ;; 準備の区画を持たない worker の準備は task の層に置かれる — task の区画の宣言で決まる。
  (val no-prepare (tuple (gfor h held :if (!= h.use "prepare") h)))
  (assert (= (compartment-jobs no-prepare None) 4) "準備の区画が無ければ task の区画 4000 millicore の 4 並列")
  ;; core への切り上げ・1 以上・天井まで。
  (val tasks-of (fn [millis] #((HeldCompartment :use "tasks" :layer "jobs" :memory-mib 1024 :memory-limit-mib 1024 :cpu-millis millis))))
  (assert (= (compartment-jobs (tasks-of 1500) None) 2) "端数は切り上げる")
  (assert (= (compartment-jobs (tasks-of 0) None) 1) "CPU の宣言 0 の区画は並べない")
  (assert (= (compartment-jobs (tasks-of 36000) None) BAKE-JOBS-CEILING) "大きい区画でも天井まで")
  ;; 呼び手の絞り(recreate の job の旧い process が動いている間)とは小さい方。
  (assert (= (compartment-jobs no-prepare 2) 2) "区画 4 と絞り 2 は 2")
  (assert (= (compartment-jobs (tasks-of 1000) 2) 1) "区画 1 と絞り 2 は 1")
  ;; 準備の区画も task の区画も持たない worker(区画を持たない・services だけを持つ)は呼び手の値のまま — None は道具の既定。
  (val services-only (tuple (gfor h held :if (= h.use "services") h)))
  (assert (is (compartment-jobs #() None) None) "区画を持たなければ道具の既定")
  (assert (= (compartment-jobs #() 2) 2) "区画を持たなければ絞りのまま")
  (assert (is (compartment-jobs services-only None) None) "準備も task も持たなければ道具の既定")
  (assert (is (held-cpu-millis services-only #("prepare" "tasks")) None) services-only))


(deftest test-the-plan-prepares-with-the-jobs-of-the-held-compartments
  ;; heartbeat の返事の写し(coordinator_link の accepted-compartments が見張りの入れ物へ置く)を、本番と同じ観測のまとめで読んで plan へ渡す。
  (val cell (MemoryCell))
  (val observe (with-handlers [(memory-observer cell) nothing-else-observed local-host] (ObserveWorld)))
  (val policy (WorkerPolicy))
  ;; 失敗ケース(区画をまだ読めていない・持たない worker): 並べる数は None(道具が container の CPU の上限から決める)。
  (val before (run observe))
  (assert (= before.compartments #()) before)
  (assert (= (! (plan 0 #(CODE-JOB ENV-JOB) before {} policy :warm #(WARM)))
             #((PrepareCode "rev1" None) (PrepareEnv "env-k1" ENV-JOB.runtime-env None) (PrepareEnv WARM.key WARM.runtime-env None :warm True)))
          "区画を持たない worker は道具の既定")
  (<- held (held-compartments-of SMALL-NODE-REPLY))
  (setv cell.eviction.compartments held)
  (val world (run observe))
  (assert (= world.compartments held) world)
  (assert (= (! (plan 0 #(CODE-JOB ENV-JOB) world {} policy :warm #(WARM)))
             #((PrepareCode "rev1" 2) (PrepareEnv "env-k1" ENV-JOB.runtime-env 2) (PrepareEnv WARM.key WARM.runtime-env 2 :warm True)))
          "木の準備・実行環境の準備・先読みは準備の区画の 2")
  ;; recreate の job の旧い process が動いている間の準備は、方策の絞り(compile-jobs-while-replacing)と区画の数の小さい方。
  (val no-prepare (tuple (gfor h held :if (!= h.use "prepare") h)))
  (val running (ProcessView "a" CODE-JOB 1 10 0))
  (val moving (replace world :codes #((CodeView "rev1" CodeState.READY :path "/c/rev1")) :processes #(running) :compartments no-prepare))
  (val newer (replace CODE-JOB :revision "rev2"))
  (assert (= (! (plan 0 #(newer) moving {} (replace policy :compile-jobs-while-replacing 3))) #((PrepareCode "rev2" 3)))
          "区画 4 と方策の絞り 3 は 3")
  (assert (= (! (plan 0 #(newer) moving {} (replace policy :compile-jobs-while-replacing 6))) #((PrepareCode "rev2" 4)))
          "区画 4 と方策の絞り 6 は 4"))
