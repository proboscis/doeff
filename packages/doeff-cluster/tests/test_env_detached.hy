;; 実行環境(runtime env)の task を送って走らせる検 — 速い模擬(detached-local の同じ VM の task・env_fake の fake-env・仮想の時計)と、
;; coordinator と worker の純粋な判断。
;;
;; 速い模擬の筋書き(設計 worker-runtime-env.md 節 5):
;;   1 宣言 → 準備 → 実行: 結果が返り、task は準備した root で走った
;;   2 送り手の repo の commit だけ変えて再送: 新しい root で走り、download は 0(worker の pid が同じは丁寧な模擬で確かめる)
;;   6 同じ env の task を 2 本同時に: 準備は 1 本・2 本とも走る
;; 失敗: 準備の失敗は Program を走らせずに DetachedEnvUnavailable(kind・一時か)。一時の物は置き直し(準備し直し)の後の答え。
;; coordinator: env の task は worker の版と比べずに置く・一時の失敗は試した worker を避けて 2 回まで置き直す・宣言と本文の形の版の誤りは 400。
;; worker: env の task は PrepareEnv で root を準備し、失敗は ENV-FAILED と kind を報告する。
(require doeff-hy.macros [deftest defk <- val var])
(import dataclasses [replace])
(import json)
(import pytest)
(import doeff [Program])
(import doeff_core_effects.handlers [state reader])
(import doeff_core_effects.effects [Ask])
(import doeff_time [SimClock sim-time-handler])
(import doeff_cluster.runtime_env_model [RuntimeEnv EnvFailure EnvFailureKind runtime-env->json env-key current-platform])
(import doeff_cluster.env_fake [fake-env FakeEnvWorld FakeEnvLog ReadFakeEnvLog])
(import doeff_cluster.detached_model [SubmitDetached AwaitDetached DetachedSucceeded DetachedEnvUnavailable DetachedAwaited
                                      DetachedVersionMismatch outcome-of-view])
(import doeff_cluster.detached [detached-local DetachedLocalStore])
(import doeff_cluster.remote_model [version-diffs VersionDiff VersionMismatch failed-from])
(import doeff_cluster.detached_model [outcome-from-task-outcome])
(import doeff_cluster.cluster_model [ClusterState ClusterTiming TaskRecord WorkerInfo ComponentVersion Requirement])
(import doeff_cluster.cluster_policy [can-run-task absorb-env-failure place-tasks submit-task ENV-RETRIES])
(import doeff_cluster.detached_policy [submit-detached])
(import doeff_cluster.worker_model [JobSpec CodeView CodeState WorldView JobRecord WorkerPolicy JobPhase PrepareEnv
                                    PrepareCode code-key])
(import doeff_cluster.worker_policy [plan statuses])
(import doeff_cluster.handlers [task-spec status-row])
(import tests.env_fixtures [LOCK APP-URL LIB-URL env-of base-world])

;; 走った印(Program が走ったかを数える — 準備の失敗では 0 のまま)。
(val RAN [])


(defk add-base [n]
  {:pre [(: n int)] :post [(: % int)]}
  "送る Program: 実行先の base に n を足して返す(走った印を残す)。"
  (<- base int (Ask "base"))
  (.append RAN n)
  (+ base n))


(defk run-sim [world program]
  {:pre [(: world FakeEnvWorld) (: program Program)] :post [(: % bool)]}
  "筋書きを速い模擬の組(状態・仮想の時計・fake-env・実行先の reader)の下で走らせる。"
  (<- ok bool ((state) ((sim-time-handler :clock (SimClock)) ((fake-env world) ((reader {"base" 100}) program)))))
  ok)


(defk send-and-wait [store key n]
  {:pre [(: store DetachedLocalStore) (: key str) (: n int)] :post [(: % DetachedAwaited)]}
  "store の送り手の env で 1 本送り、答えを待つ。"
  (<- ((detached-local store) (SubmitDetached (add-base n) :env "tests.fixtures.envs:plain_env" :key key)))
  (<- outcome ((detached-local store) (AwaitDetached key)))
  outcome)


;; --- 速い模擬の筋書き ----------------------------------------------------------------------

(defk scenario-1-and-2 []
  {:pre [] :post [(: % bool)]}
  "筋書き 1・2: 宣言 → 準備 → 実行、次に project の commit だけ変えて再送。"
  (<- env-1 RuntimeEnv (env-of "app-1" "lib-1" LOCK))
  (val store (DetachedLocalStore :runtime-env env-1))
  (<- first (send-and-wait store "job-1" 1))
  (assert (= first (DetachedSucceeded 101)) first)
  (val root-1 (. (get store.records "job-1") root))
  (<- key-1 str (env-key env-1 (current-platform)))
  (assert (= root-1 (.format "/state/roots/{}" key-1)) "task は準備した env の root で走った")
  (<- before FakeEnvLog (ReadFakeEnvLog))
  (<- env-2 RuntimeEnv (env-of "app-2" "lib-1" LOCK))
  (setv store.runtime-env env-2)
  (<- second (send-and-wait store "job-2" 2))
  (<- after FakeEnvLog (ReadFakeEnvLog))
  (assert (= second (DetachedSucceeded 102)) second)
  (val root-2 (. (get store.records "job-2") root))
  (assert (!= root-2 root-1) "新しい commit は新しい root で走る")
  (assert (= (- after.syncs before.syncs) 1))
  (assert (= (- after.downloads before.downloads) 0) "同じ lock なので download は 0")
  True)


(deftest test-a-task-runs-in-the-prepared-root-and-a-new-commit-gets-a-new-root
  (.clear RAN)
  (<- world FakeEnvWorld (base-world))
  (<- ok bool (run-sim world (scenario-1-and-2)))
  (assert ok)
  (assert (= RAN [1 2])))


(defk scenario-6 []
  {:pre [] :post [(: % bool)]}
  "筋書き 6: 同じ env の task を 2 本同時に送る → 準備は 1 本・2 本とも走る。"
  (<- env RuntimeEnv (env-of "app-1" "lib-1" LOCK))
  (val store (DetachedLocalStore :runtime-env env))
  (<- ((detached-local store) (SubmitDetached (add-base 1) :env "tests.fixtures.envs:plain_env" :key "a")))
  (<- ((detached-local store) (SubmitDetached (add-base 2) :env "tests.fixtures.envs:plain_env" :key "b")))
  (<- a ((detached-local store) (AwaitDetached "a")))
  (<- b ((detached-local store) (AwaitDetached "b")))
  (<- log FakeEnvLog (ReadFakeEnvLog))
  (assert (= #(a b) #((DetachedSucceeded 101) (DetachedSucceeded 102))) #(a b))
  (assert (= store.prepares 1) store.prepares)
  (assert (= log.syncs 1) log)
  True)


(deftest test-two-tasks-of-one-env-share-one-preparation
  (<- world FakeEnvWorld (base-world))
  (<- ok bool (run-sim world (scenario-6)))
  (assert ok))


(defk failure-scenario [expect-kind expect-retryable expect-prepares]
  {:pre [(: expect-kind EnvFailureKind) (: expect-retryable bool) (: expect-prepares int)] :post [(: % bool)]}
  "準備が失敗する世界で 1 本送る → Program は走らず DetachedEnvUnavailable。一時の失敗は準備し直した後の答え。"
  (<- env RuntimeEnv (env-of "app-1" "lib-1" LOCK))
  (val store (DetachedLocalStore :runtime-env env))
  (<- outcome (send-and-wait store "job" 7))
  (assert (isinstance outcome DetachedEnvUnavailable) outcome)
  (assert (= outcome.kind expect-kind.value) outcome)
  (assert (= outcome.retryable expect-retryable) outcome)
  (assert (= store.prepares expect-prepares) store.prepares)
  True)


(deftest test-a-failed-preparation-answers-its-kind-without-running-the-program
  (.clear RAN)
  (<- world FakeEnvWorld (base-world))
  ;; 恒久の失敗は 1 回で答える
  (<- (run-sim (replace world :denied (frozenset #(LIB-URL))) (failure-scenario EnvFailureKind.REPO-DENIED False 1)))
  ;; 一時の失敗は置き直し(ENV-RETRIES 回)の後に答える
  (<- (run-sim (replace world :unreachable (frozenset #(APP-URL)))
               (failure-scenario EnvFailureKind.REPO-UNREACHABLE True (+ 1 ENV-RETRIES))))
  (assert (= RAN []) "準備に失敗した task の Program は走らない"))


;; --- 版の突き合わせ ------------------------------------------------------------------------

(deftest test-the-env-key-is-compared-only-when-both-sides-name-it
  (assert (= (version-diffs {"doeff" "1" "envKey" "k"} {"doeff" "1"}) #()) "送り手だけが env を名乗る時は比べない")
  (assert (= (version-diffs {"doeff" "1" "envKey" "k1"} {"doeff" "1" "envKey" "k2"})
             #((VersionDiff "envKey" "k1" "k2"))))
  ;; 反例: 送り手の版の doeff を 1 つずらすと、DetachedVersionMismatch に欄の名と env のキーが載る
  (val diffs (version-diffs {"doeff" "0.4.0"} {"doeff" "0.4.1"}))
  (val outcome (outcome-from-task-outcome (failed-from (VersionMismatch "版が違う" diffs "k1"))))
  (assert (isinstance outcome DetachedVersionMismatch) outcome)
  (assert (= outcome.diffs #((VersionDiff "doeff" "0.4.0" "0.4.1"))))
  (assert (= outcome.env-key "k1")))


;; --- coordinator の判断 ---------------------------------------------------------------------

(defk env-task [env]
  {:pre [(: env RuntimeEnv)] :post [(: % TaskRecord)]}
  "env を持つ待ちの task 1 本(送り手の版は worker と違う)。"
  (<- declared dict (runtime-env->json env))
  (TaskRecord "t1" "" "tests.fixtures.envs:plain_env" "blob" "" #((ComponentVersion "doeff" "old")) #() 60000 60000 0
              :runtime-env declared))


(deftest test-env-tasks-are-placed-without-comparing-worker-versions
  (<- env RuntimeEnv (env-of "app-1" "lib-1" LOCK))
  (<- task TaskRecord (env-task env))
  (val worker (WorkerInfo "w1" #() 1 0 #((ComponentVersion "doeff" "new"))))
  (assert (can-run-task task worker) "env の task は worker の版と比べない")
  (assert (not (can-run-task (replace task :runtime-env None) worker)) "今の commit だけの task は今のまま版を比べる")
  (assert (not (can-run-task (replace task :avoid #("w1")) worker)) "準備に一時の失敗をした worker には置き直さない"))


(deftest test-a-temporary-env-failure-is-placed-again-at-most-twice
  (<- env RuntimeEnv (env-of "app-1" "lib-1" LOCK))
  (<- task TaskRecord (env-task env))
  (val report {"name" "task/t1" "phase" "env-failed" "detail" "届かない" "failureKind" "repo-unreachable" "retryable" True})
  (var current (replace task :phase "assigned" :worker "w1"))
  (for [n (range ENV-RETRIES)]
    (:= current (absorb-env-failure current (.format "w{}" (+ n 1)) report 10))
    (assert (= current.phase "queued") current)
    (:= current (replace current :phase "assigned" :worker (.format "w{}" (+ n 2)))))
  (val last (absorb-env-failure current "w3" report 10))
  (assert (= last.phase "env-failed") last)
  (assert (= last.avoid #("w1" "w2")))
  (val permanent (absorb-env-failure (replace task :phase "assigned" :worker "w1")
                                     "w1" (| report {"failureKind" "lock-mismatch" "retryable" False}) 10))
  (assert (= permanent.phase "env-failed") "恒久の失敗は置き直さない")
  ;; 置き直せる別の worker が無ければ、最後の失敗で終える
  (val timing (ClusterTiming))
  (val requeued (absorb-env-failure (replace task :phase "assigned" :worker "w1" :detached True) "w1" report 10))
  (val cluster (ClusterState :workers {"w1" (WorkerInfo "w1" #() 1 10 #())} :tasks {"t1" requeued}))
  (val placed (place-tasks 20 cluster {} timing))
  (assert (= (. (get placed "t1") phase) "env-failed") (get placed "t1"))
  (val view {"key" "k" "phase" "env-failed" "detail" "d" "failureKind" "repo-unreachable" "retryable" True})
  (assert (= (outcome-of-view view) (DetachedEnvUnavailable "repo-unreachable" "d" True))))


(deftest test-a-bad-declaration-or-format-is-refused-with-400
  (<- env RuntimeEnv (env-of "app-1" "lib-1" LOCK))
  (<- declared dict (runtime-env->json env))
  (val base {"env" "e" "blob" "b" "revision" "" "versions" {} "leaseSeconds" 10})
  (val broken (| declared {"repos" [{"name" "app" "url" APP-URL "commit" "main"}]}))
  (for [body [(| base {"runtimeEnv" broken}) (| base {"format" 99})]]
    (assert (= (get (submit-task (ClusterState) body 0) 1) 400) body)
    (assert (= (. (submit-detached (ClusterState) "k" body 0) status) 400) body))
  (val accepted (submit-task (ClusterState) (| base {"runtimeEnv" declared "format" 1}) 0))
  (assert (= (get accepted 1) 200) accepted)
  (assert (= (. (get (. (get accepted 0) tasks) "t1") runtime-env) declared)))


;; --- worker の判断 -------------------------------------------------------------------------

(deftest test-the-worker-prepares-an-env-root-and-reports-its-failure
  (<- env RuntimeEnv (env-of "app-1" "lib-1" LOCK))
  (<- declared dict (runtime-env->json env))
  (val spec (task-spec {"id" "t1" "env" "tests.fixtures.envs:plain_env" "revision" "" "versions" {} "blob" "b"
                        "runtimeEnv" declared}
                       (. (__import__ "pathlib") (Path "/tmp/tasks"))))
  (<- key str (env-key env (current-platform)))
  (assert (= spec.revision (+ "env-" key)))
  (assert (= (json.loads spec.runtime-env) declared))
  (val policy (WorkerPolicy))
  (val actions (plan 0 #(spec) (WorldView #() #()) {} policy))
  (assert (= actions #((PrepareEnv (code-key spec) spec.runtime-env))) actions)
  (val failure (EnvFailure :kind EnvFailureKind.COMMIT-MISSING :detail "push していない" :retryable False))
  (val world (WorldView #((CodeView (code-key spec) CodeState.FAILED :detail "push していない" :failed-ms 0 :failure failure)) #()))
  (val status (get (statuses 1 #(spec) world {} policy) 0))
  (assert (= status.phase JobPhase.ENV-FAILED) status)
  (val row (status-row status))
  (assert (= #((get row "phase") (get row "failureKind") (get row "retryable")) #("env-failed" "commit-missing" False)) row)
  ;; 今の commit だけの task は今のまま木を展開する
  (val plain (replace spec :revision (* "a" 40) :runtime-env None))
  (assert (= (plan 0 #(plain) (WorldView #() #()) {} policy) #((PrepareCode (* "a" 40))))))
