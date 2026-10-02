;; 実行環境(runtime env)の task を送って走らせる検 — 手元の runner sim-cluster(本物の coordinator と本物の run-worker・仮想の時計)と、
;; coordinator と worker の純粋な判断(2026-09-28 まで速い模擬は同じ VM の模擬 detached-local だった — 呼び手の外側の handler を継ぐので
;; 消した)。
;;
;; sim-cluster の筋書き(設計 worker-runtime-env.md 節 5):
;;   1 宣言 → 準備 → 実行: 結果が返り、task は worker が準備した env の root が揃った後に走った
;;   2 送り手の repo の commit だけ変えて再送: 新しい env のキーの root を準備して走る(download 0 と worker の pid は準備の層の検と丁寧な
;;     模擬で確かめる)
;;   6 同じ env の task を 2 本同時に: 準備は 1 本・2 本とも走る
;; 失敗: 準備の失敗は Program を走らせずに DetachedEnvUnavailable(kind・一時か)。一時の物は試した worker を避けた置き直しの後の答え。
;; coordinator: env の task は worker の版と比べずに置く・一時の失敗は試した worker を避けて 2 回まで置き直す・宣言と本文の形の版の誤りは 400。
;; worker: env の task は PrepareEnv で root を準備し、失敗は ENV-FAILED と kind を報告する。
(require doeff-hy.macros [deftest defk <- val var])
(require doeff-hy.record [defrecord])
(import dataclasses [dataclass replace])
(import json)
(import pathlib [Path])
(import pytest)
(import doeff [with-handlers])
(import doeff_cluster.shared.intent.runtime_env_model [RuntimeEnv EnvFailure EnvFailureKind])
(import doeff_cluster.shared.core.runtime_env_rules [runtime-env->json env-key current-platform])
(import doeff_cluster.shared.intent.detached_model [AwaitDetached DetachedSucceeded DetachedEnvUnavailable
                                                    DetachedVersionMismatch])
(import doeff_cluster.shared.protocol.detached [outcome-of-view])
(import doeff_cluster.sim.local [sim-cluster SimWorker SimLink ClientLink coordinator-answers ReadCoordinator ProcessesOf PreparationsOf])
(import doeff_cluster.shared.entry.service_build [system-of])
(import doeff_cluster.shared.intent.remote_model [VersionDiff VersionMismatch])
(import doeff_cluster.shared.core.remote_rules [version-diffs failed-from])
(import doeff_cluster.shared.protocol.detached [outcome-from-task-outcome])
(import doeff_cluster.shared.intent.protocol [ClusterTiming])
(import doeff_cluster.coordinator.intent.cluster_model [ClusterState TaskRecord WorkerInfo ComponentVersion])
(import doeff_cluster.coordinator.core.cluster_policy [can-run-task absorb-env-failure place-tasks submit-task ENV-RETRIES])
(import doeff_cluster.coordinator.intent.request_bodies [StatusRow])
(import doeff_cluster.coordinator.core.detached_policy [submit-detached])
(import doeff_cluster.worker.intent.worker_model [CodeView CodeState WorldView JobRecord WorkerPolicy PrepareEnv
                                    PrepareCode] doeff_cluster.shared.intent.job_model [JobSpec JobPhase] doeff_cluster.worker.core.worker_rules [code-key])
(import doeff_cluster.worker.core.policy [plan statuses])
(import doeff_cluster.worker.protocol.declared [task-spec] doeff_cluster.worker.protocol.heartbeat [status-row])
(import doeff_cluster.shared.core.detached_rules [submit-detached-task])
(import tests.env_fixtures [LOCK APP-URL LIB-URL env-of])
(import tests.detached_rig [slow-add])
(import tests.program_rows [SAMPLE-TASK-PROGRAM program-placed task-body-of])

;; --- 手元の runner sim-cluster の筋書き ------------------------------------------------------------
;; 本物の coordinator が env の task を置き、本物の run-worker が PrepareEnv で root の準備を撃ち、sim の宿が準備(即座に揃う・env-failure を
;; 持つ worker は失敗で終わる)を記録する。送る task の実行環境の宣言は送り手の口(ClientLink を置き換えた SimLink の runtime-env — 本番の
;; DetachedSender の runtime-env)が運ぶ。root の中身(新しい commit は新しい root・同じ lock の download 0・同じキーの準備は 1 本の本物の
;; 答え手)は準備の層の検(test_env_prepare.hy — env_world の模擬の世界)と丁寧な模擬(test_env_careful.hy — 本物の env-host)が持つ。

(val NO-JOBS (system-of "env-scenarios" #()))
(val LOCAL (frozenset ["local"]))


(defrecord Sent
  "env の task を送った筋書きの読み: outcomes = key → 答え・preparations = worker ごとの実行環境の root の準備(SimPreparation の列)・
   processes = key → task の process の列(Program が走ったか)。"
  (#^ dict outcomes)
  (#^ dict preparations)
  (#^ dict processes))


(defk submit-and-await-all [keys n]
  {:pre [(: keys tuple) (: n int)] :post [(: % dict)] :tags {:context "doeff-cluster-test" :role "program"}}
  "keys の task(slow-add — 答え = 100 + n + 順番)を全部送ってから、全部の答えを待つため(答え = key → 答え)。"
  (for [#(i key) (enumerate keys)]
    (<- (submit-detached-task (slow-add 0.0 (+ n i)) :needs LOCAL :key key)))
  (var got {})
  (for [key keys]
    (<- outcome (AwaitDetached key))
    (:= got (| got {key outcome})))
  got)


(defk send-all [env keys n]
  {:pre [(: env RuntimeEnv) (: keys tuple) (: n int)] :post [(: % dict)] :tags {:context "doeff-cluster-test" :role "program"}}
  "env の送り手(実行環境の宣言を運ぶ口)として submit-and-await-all を回すため。"
  (<- link SimLink (ClientLink))
  (<- outcomes dict (with-handlers [(coordinator-answers (replace link :runtime-env env))] (submit-and-await-all keys n)))
  outcomes)


(defk seen-after [outcomes workers]
  {:pre [(: outcomes dict) (: workers tuple)] :post [(: % Sent)] :tags {:context "doeff-cluster-test" :role "program"}}
  "送った task の答えに、worker ごとの準備と key ごとの process の列を添えるため。"
  (<- state dict (ReadCoordinator "/state"))
  (var preparations {})
  (for [w workers]
    (<- made tuple (PreparationsOf w))
    (:= preparations (| preparations {w (tuple (gfor p made :if p.env p))})))
  (var processes {})
  (for [key outcomes]
    (val ids (lfor t (get state "tasks") :if (= (.get t "key") key) (get t "id")))
    (<- found tuple (ProcessesOf (+ "task/" (get ids 0))))
    (:= processes (| processes {key found})))
  (Sent :outcomes outcomes :preparations preparations :processes processes))


(defk scenario-1-and-2 []
  {:pre [] :post [(: % Sent)] :tags {:context "doeff-cluster-test" :role "program"}}
  "筋書き 1・2: 宣言 → 準備 → 実行、次に project の commit だけ変えて再送。"
  (<- env-1 RuntimeEnv (env-of "app-1" "lib-1" LOCK))
  (<- env-2 RuntimeEnv (env-of "app-2" "lib-1" LOCK))
  (<- first dict (send-all env-1 #("job-1") 1))
  (<- second dict (send-all env-2 #("job-2") 2))
  (<- seen Sent (seen-after (| first second) #("w1")))
  seen)


(deftest test-a-task-runs-after-its-root-is-prepared-and-a-new-commit-prepares-a-new-root
  (<- seen Sent (sim-cluster NO-JOBS (scenario-1-and-2) :workers #((SimWorker :name "w1" :provides LOCAL))))
  (assert (= seen.outcomes {"job-1" (DetachedSucceeded 101) "job-2" (DetachedSucceeded 102)}) seen.outcomes)
  (<- env-1 RuntimeEnv (env-of "app-1" "lib-1" LOCK))
  (<- env-2 RuntimeEnv (env-of "app-2" "lib-1" LOCK))
  (<- key-1 str (env-key env-1 (current-platform)))
  (<- key-2 str (env-key env-2 (current-platform)))
  ;; task は worker が準備した env の root(env-<キー>)で走った・新しい commit は新しい root を準備した。
  (assert (= (lfor p (get seen.preparations "w1") p.key) [(+ "env-" key-1) (+ "env-" key-2)]) (get seen.preparations "w1"))
  (assert (all (gfor p (get seen.preparations "w1") (<= p.ready-ms (. (get (get seen.processes (if (.endswith p.key key-1) "job-1" "job-2")) 0) started-ms))))
          #(seen.preparations seen.processes)))


(defk scenario-6 []
  {:pre [] :post [(: % Sent)] :tags {:context "doeff-cluster-test" :role "program"}}
  "筋書き 6: 同じ env の task を 2 本同時に送る。"
  (<- env RuntimeEnv (env-of "app-1" "lib-1" LOCK))
  (<- outcomes dict (send-all env #("a" "b") 1))
  (<- seen Sent (seen-after outcomes #("w1")))
  seen)


(deftest test-two-tasks-of-one-env-share-one-preparation
  ;; 準備は 1 本(worker は準備中・準備済みの root を準備し直さない)・2 本とも走る。準備に時間のかかる worker で確かめる。
  (<- seen Sent (sim-cluster NO-JOBS (scenario-6) :workers #((SimWorker :name "w1" :provides LOCAL :prepare-seconds 3.0))))
  (assert (= seen.outcomes {"a" (DetachedSucceeded 101) "b" (DetachedSucceeded 102)}) seen.outcomes)
  (assert (= (len (get seen.preparations "w1")) 1) seen.preparations))


(defk failure-scenario [workers]
  {:pre [(: workers tuple)] :post [(: % Sent)] :tags {:context "doeff-cluster-test" :role "program"}}
  "準備が失敗する worker の上で 1 本送る。"
  (<- env RuntimeEnv (env-of "app-1" "lib-1" LOCK))
  (<- outcomes dict (send-all env #("job") 7))
  (<- seen Sent (seen-after outcomes workers))
  seen)


(deftest test-a-failed-preparation-answers-its-kind-without-running-the-program
  ;; 恒久の失敗は 1 回で答える(Program は走らない)。
  (val denied (EnvFailure :kind EnvFailureKind.REPO-DENIED :detail "許可表に無い" :retryable False))
  (<- seen Sent (sim-cluster NO-JOBS (failure-scenario #("w1"))
                             :workers #((SimWorker :name "w1" :provides LOCAL :env-failure denied))))
  (val outcome (get seen.outcomes "job"))
  (assert (isinstance outcome DetachedEnvUnavailable) outcome)
  (assert (= #(outcome.kind outcome.retryable) #(EnvFailureKind.REPO-DENIED.value False)) outcome)
  (assert (= (len (get seen.preparations "w1")) 1) seen.preparations)
  (assert (= (get seen.processes "job") #()) "準備に失敗した task の Program は走らない")
  ;; 一時の失敗は、試した worker を避けて置き直した(ENV-RETRIES 回)後に答える。
  (val unreachable (EnvFailure :kind EnvFailureKind.REPO-UNREACHABLE :detail "届かない" :retryable True))
  (val names (tuple (gfor i (range (+ 1 ENV-RETRIES)) (.format "w{}" (+ i 1)))))
  (<- again Sent (sim-cluster NO-JOBS (failure-scenario names)
                              :workers (tuple (gfor n names (SimWorker :name n :provides LOCAL :env-failure unreachable)))))
  (val temporary (get again.outcomes "job"))
  (assert (isinstance temporary DetachedEnvUnavailable) temporary)
  (assert (= #(temporary.kind temporary.retryable) #(EnvFailureKind.REPO-UNREACHABLE.value True)) temporary)
  (assert (= (sorted (gfor #(n made) (.items again.preparations) (len made))) (* [1] (+ 1 ENV-RETRIES))) again.preparations)
  (assert (= (get again.processes "job") #()) "準備に失敗した task の Program は走らない"))


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
  (TaskRecord "t1" "" SAMPLE-TASK-PROGRAM "" #((ComponentVersion "doeff" "old")) #("net") 60000 60000 0
              :runtime-env declared))


(deftest test-env-tasks-are-placed-without-comparing-worker-versions
  (<- env RuntimeEnv (env-of "app-1" "lib-1" LOCK))
  (<- task TaskRecord (env-task env))
  (val worker (WorkerInfo "w1" #("net") 1 0 #((ComponentVersion "doeff" "new"))))
  (assert (can-run-task task worker) "env の task は worker の版と比べない")
  (assert (not (can-run-task (replace task :runtime-env None) worker)) "今の commit だけの task は今のまま版を比べる")
  (assert (not (can-run-task (replace task :avoid #("w1")) worker)) "準備に一時の失敗をした worker には置き直さない"))


(deftest test-a-temporary-env-failure-is-placed-again-at-most-twice
  (<- env RuntimeEnv (env-of "app-1" "lib-1" LOCK))
  (<- task TaskRecord (env-task env))
  (val report (StatusRow :name "task/t1" :phase "env-failed" :detail "届かない" :failure-kind "repo-unreachable" :retryable True))
  (var current (replace task :phase "assigned" :worker "w1"))
  (for [n (range ENV-RETRIES)]
    (:= current (absorb-env-failure current (.format "w{}" (+ n 1)) report 10))
    (assert (= current.phase "queued") current)
    (:= current (replace current :phase "assigned" :worker (.format "w{}" (+ n 2)))))
  (val last (absorb-env-failure current "w3" report 10))
  (assert (= last.phase "env-failed") last)
  (assert (= last.avoid #("w1" "w2")))
  (val permanent (absorb-env-failure (replace task :phase "assigned" :worker "w1")
                                     "w1" (replace report :failure-kind "lock-mismatch" :retryable False) 10))
  (assert (= permanent.phase "env-failed") "恒久の失敗は置き直さない")
  ;; 置き直せる別の worker が無ければ、最後の失敗で終える
  (val timing (ClusterTiming))
  (val requeued (absorb-env-failure (replace task :phase "assigned" :worker "w1" :detached True) "w1" report 10))
  (val cluster (ClusterState :workers {"w1" (WorkerInfo "w1" #("net") 1 10 #())} :tasks {"t1" requeued}))
  (val placed (place-tasks 20 cluster {} timing))
  (assert (= (. (get placed "t1") phase) "env-failed") (get placed "t1"))
  (val view {"key" "k" "phase" "env-failed" "detail" "d" "failureKind" "repo-unreachable" "retryable" True})
  (assert (= (outcome-of-view view) (DetachedEnvUnavailable "repo-unreachable" "d" True))))


(deftest test-a-bad-declaration-or-format-is-refused-with-400
  (<- env RuntimeEnv (env-of "app-1" "lib-1" LOCK))
  (<- declared dict (runtime-env->json env))
  ;; 本文は置き場に置いた詰めた Program のキーを運ぶ(置いた状態 placed で送る — 断りは宣言と形の版だけによる)。
  (<- placed tuple (program-placed (ClusterState) {}))
  (val base {"program" (get placed 1) "revision" "" "needs" ["net"] "leaseSeconds" 10})
  (val broken (| declared {"repos" [{"name" "app" "url" APP-URL "commit" "main"}]}))
  (for [body [(| base {"runtimeEnv" broken}) (| base {"format" 99})]]
    (assert (= (get (submit-task (get placed 0) (task-body-of body) 0) 1) 400) body)
    (assert (= (. (submit-detached (get placed 0) "k" (task-body-of body) 0) status) 400) body))
  (val accepted (submit-task (get placed 0) (task-body-of (| base {"runtimeEnv" declared "format" 1})) 0))
  (assert (= (get accepted 1) 200) accepted)
  (assert (= (. (get (. (get accepted 0) tasks) "t1") runtime-env) declared)))


;; --- worker の判断 -------------------------------------------------------------------------

(deftest test-the-worker-prepares-an-env-root-and-reports-its-failure
  (<- env RuntimeEnv (env-of "app-1" "lib-1" LOCK))
  (<- declared dict (runtime-env->json env))
  (<- spec (task-spec {"id" "t1" "revision" "" "versions" {} "program" SAMPLE-TASK-PROGRAM
                        "runtimeEnv" declared}
                       (Path "/tmp/tasks")))
  (<- key str (env-key env (current-platform)))
  (assert (= spec.revision (+ "env-" key)))
  (assert (is-not spec.runtime-env None) spec)
  (assert (= (json.loads spec.runtime-env) declared))
  (val policy (WorkerPolicy))
  (val actions (plan 0 #(spec) (WorldView #() #()) {} policy))
  (assert (= actions #((PrepareEnv (code-key spec) spec.runtime-env))) actions)
  (val failure (EnvFailure :kind EnvFailureKind.COMMIT-MISSING :detail "push していない" :retryable False))
  (val world (WorldView #((CodeView (code-key spec) CodeState.FAILED :detail "push していない" :failed-ms 0 :failure failure)) #()))
  (val status (get (statuses 1 #(spec) world {} policy) 0))
  (assert (= status.phase JobPhase.ENV-FAILED) status)
  (<- row (status-row status))
  (assert (= #((get row "phase") (get row "failureKind") (get row "retryable")) #("env-failed" "commit-missing" False)) row)
  ;; 今の commit だけの task は今のまま木を展開する
  (val plain (replace spec :revision (* "a" 40) :runtime-env None))
  (assert (= (plan 0 #(plain) (WorldView #() #()) {} policy) #((PrepareCode (* "a" 40))))))
