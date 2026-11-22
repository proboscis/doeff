;; task の要りを task の子 process へ渡す(ADR-DOE-CLUSTER-005 R4 — law a-task-reads-the-claim-it-is-compared-against): worker の memory の
;; 見張りは、上に載る系の残す線を割った回に、置いた時に固定した要り(heartbeat の返事の task の行の memoryMiB)を越えた task を止める。
;; task が中で並べる本数をその数の内に縛れるよう、worker は task の job を始める時に同じ数を環境変数 TASK-MEMORY-MIB-VAR で渡し、要りを
;; 名乗らない task(見張りが越えで止めない)と常駐の job には名を置かない。返事の行の読み(task-spec)から子の起こし方(job-launch)までを
;; 子 process を起こさずに通す。
(require doeff-hy.macros [defk deftest <- val])
(val MODULE-TAGS {:context "doeff-cluster-test" :role "test"})
(import dataclasses)
(import json)
(import pathlib [Path])
(import doeff_cluster [JobSpec CodeLayout WorkerPolicy shim-spans])
(import doeff_cluster._shared.core.run_context_rules [TASK-MEMORY-MIB-VAR])
(import doeff_cluster._worker.core.eviction_rules [MIB task-needs-of])
(import doeff_cluster._worker.core.launch [job-launch JobLaunch])
(import doeff_cluster._worker.core.shim_timing [ShimSpans])
(import doeff_cluster._worker.protocol.declared [task-spec])
(import doeff_cluster [RuntimeEnv] doeff_cluster._shared.core.runtime_env_rules [runtime-env->json])
(import tests.env_fixtures [LOCK env-of])


(defk declared-env []
  {:pre [] :post [(: % dict)] :tags {:context "doeff-cluster-test" :role "judgment"}}
  "task の行の runtimeEnv に載せる実行環境の宣言(JSON の形)を、検の共有の宣言 env-of から作るため。"
  (<- env RuntimeEnv (env-of "app-1" "lib-1" LOCK))
  (<- declared dict (runtime-env->json env))
  declared)


(defk launch-env [spec]
  {:pre [(: spec JobSpec)] :post [(: % (get dict #(str str)))] :tags {:context "doeff-cluster-test" :role "judgment"}}
  "worker が spec の job の子へ置く環境変数(名 → 値)を、本番の起こし方の判断 job-launch で読むため(実行環境の job は root の venv で
   起こす形・木の job は worker の環境を継ぐ形 — どちらも子へ渡す分だけ)。"
  (<- shim ShimSpans (shim-spans (WorkerPolicy)))
  (<- plan JobLaunch (job-launch spec "/roots/env-k1" "1-abc" 1 :entry spec.entry
                                 :venv (if spec.runtime-env "/roots/env-k1/.venv" None) :python "/py" :hy-command "/bin/hy" :extra-env {}
                                 :layout (CodeLayout) :allowed-env {"PATH" "/usr/bin"} :worker-pid 42 :program-path None
                                 :program-env "DOEFF_PROGRAM_FILE" :work-dir "/jobs/task_t1" :shim-grace-ms shim.shim-grace-ms
                                 :notice-env "DOEFF_NOTICE_FD"))
  (dfor e plan.env e.name e.value))


(deftest test-a-detached-task-receives-the-claim-the-watch-compares-against [#^ Path tmp-path]
  ;; 置いた時に固定した要り 9038 MiB の切り離した task: 見張りが比べる数(task-needs-of の bytes)と子が受ける数が同じ。実行環境の task
  ;; (許可表で組む REPLACE の環境)にも木の task(EXTEND の上書きの分)にも、待ちの子から分ける道の環境(entry-env)にも置く。
  (<- runtime dict (declared-env))
  (val row {"id" "t1" "name" "daily" "revision" "r" "program" None "versions" {} "detached" True "key" "k" "leaseMs" 1 "retainMs" 1
            "needs" [] "runtimeEnv" runtime "memoryMiB" 9038})
  (<- spec JobSpec (task-spec row tmp-path))
  (assert spec.detached spec)
  (assert (= spec.memory-mib 9038) spec)
  (<- needs tuple (task-needs-of #(row)))
  (<- env dict (launch-env spec))
  (assert (= (* (int (get env TASK-MEMORY-MIB-VAR)) MIB) (. (get needs 0) bytes)) #(env needs))
  (assert (. (get needs 0) declared) needs)
  (<- tree JobSpec (task-spec (dfor #(k v) (.items row) :if (!= k "runtimeEnv") k v) tmp-path))
  (<- tree-env dict (launch-env tree))
  (assert (= (get tree-env TASK-MEMORY-MIB-VAR) "9038") tree-env)
  ;; 名は DOEFF_ の予約の接頭辞の中(宣言の env-vars が同じ名で上書きできない)。
  (assert (.startswith TASK-MEMORY-MIB-VAR "DOEFF_WORKER_") TASK-MEMORY-MIB-VAR))


(deftest test-a-task-that-names-no-claim-receives-no-claim-variable [#^ Path tmp-path]
  ;; 要りを名乗らない行(memoryMiB が無い・真偽・負・数でない)は、見張りが越えで止めない task(declared が偽)。子へ名を置かない —
  ;; 見込みの要りを渡すと、task が止められない数を縛りに読む。常駐の job にも置かない。
  (<- runtime dict (declared-env))
  (val base {"id" "t2" "revision" "r" "program" None "versions" {} "detached" True "runtimeEnv" runtime})
  (for [extra [{} {"memoryMiB" True} {"memoryMiB" -1} {"memoryMiB" "4096"}]]
    (val row (| base extra))
    (<- spec JobSpec (task-spec row tmp-path))
    (assert (is spec.memory-mib None) #(extra spec))
    (<- needs tuple (task-needs-of #(row)))
    (assert (not (. (get needs 0) declared)) #(extra needs))
    (<- env dict (launch-env spec))
    (assert (not-in TASK-MEMORY-MIB-VAR env) #(extra env)))
  (<- service-env dict (launch-env (JobSpec "svc" "app.main" #("service") "rev1" :runtime-env (json.dumps runtime) :env-key "k1")))
  (assert (not-in TASK-MEMORY-MIB-VAR service-env) service-env))


(deftest test-the-claim-does-not-restart-the-task [#^ Path tmp-path]
  ;; 要りは比べない欄: 値だけが違う spec は同じ job(拍ごとの返事で process を起こし直さない — 子は起こした時の値を持つ)。
  (val row {"id" "t3" "revision" "r" "program" None "versions" {} "detached" True "memoryMiB" 1024})
  (<- spec JobSpec (task-spec row tmp-path))
  (assert (= spec (dataclasses.replace spec :memory-mib 2048)) spec))
