;; 手元の道具が切り離した task を送り・待つ口(client_foundation.with-detached-client — #2782)の検。
;;
;; 組 = test_detached.hy の coordinator の組と同じ土台: 本物の coordinator の判断(MemoryCoordinator)を httpx.MockTransport の後ろに置き、
;; 口が出す HttpRequest に検の答え手(transport-http)で答える。担い手(RigWorker)は同じ VM で task の Program を走らせる・仮想の時計。
;; 確かめる事:
;;   (a) 口の下で SubmitDetached → AwaitDetached が task の答えを返す。
;;   (b) 呼び手が値で渡した名乗り(revision・実行環境の宣言)が、coordinator が受けた task の行に載る。
;; 失敗ケース: 口が名乗り(revision か runtime-env)を DetachedSender へ渡し落とすと、(b) の行の比べが赤になる。
(require doeff-hy.macros [deftest defk <- val])
(import pathlib [Path])
(import httpx)
(import doeff [Program EffectBase with-handlers])
(import doeff_core_effects.scheduler [Spawn Cancel Task])
(import doeff_time [SimClock sim-time-handler])
(import doeff_cluster.coordinator.intent.cluster_model [TaskRecord])
(import doeff_cluster.client_foundation [with-detached-client])
(import doeff_cluster.foundation.process_versions [current-versions])
(import doeff_cluster.shared.intent.runtime_env_model [RuntimeEnv RepoCheckout PythonProject])
(import doeff_cluster.shared.core.runtime_env_rules [runtime-env->json])
(import doeff_cluster.shared.core.detached_rules [submit-detached-task])
(import doeff_cluster.shared.intent.detached_model [AwaitDetached DetachedSubmitted DetachedSucceeded])
(import tests.transport_http [transport-http COORDINATOR-URL])
(import tests.detached_rig [slow-add RigWorker MemoryCoordinator worker-tick worker-loop RIG-PROVIDES])

;; 呼び手が値で渡す名乗り(手元の checkout の commit の形)。
(val REVISION (* "a" 40))
(val LOCAL (frozenset RIG-PROVIDES))


(defk declared-env []
  {:pre [] :post [(: % RuntimeEnv)] :tags {:context "doeff-cluster-test" :role "judgment"}}
  "呼び手が名乗る実行環境の宣言(repo 1 つ・project はその根)。"
  (RuntimeEnv :repos #((RepoCheckout :name "app" :url "https://example.com/app.git" :commit REVISION))
              :project (PythonProject :repo "app" :path "." :lock-sha256 (* "0" 64) :python "3.14")
              :import-roots #("app/.")))


(defk submit-and-await [key]
  {:pre [(: key str)] :post [(: % DetachedSucceeded)] :tags {:context "doeff-cluster-test" :role "program"}}
  "手元の道具の本体: task を 1 本送り、終わるまで待って答えを返す。"
  (<- submitted DetachedSubmitted (submit-detached-task (slow-add 1.0 7) key :needs LOCAL :lease-seconds 5.0))
  (assert (= submitted (DetachedSubmitted key True)) submitted)
  (<- outcome DetachedSucceeded (AwaitDetached key))
  outcome)


(defk with-runner [worker body]
  {:pre [(: worker RigWorker) (: body (| Program EffectBase))] :post [(: % DetachedSucceeded)] :tags {:context "doeff-cluster-test" :role "program"}}
  "担い手を 1 拍名乗らせてから heartbeat のループを走らせ、本体の後に止める(送った時に置ける担い手が在るように)。"
  (<- (worker-tick worker))
  (<- loop Task (Spawn (worker-loop worker 0.5) :daemon True))
  (<- answer DetachedSucceeded body)
  (setv worker.dead True)
  (<- (Cancel loop))
  answer)


(defk task-row [coordinator key]
  {:pre [(: coordinator MemoryCoordinator) (: key str)] :post [(: % TaskRecord)] :tags {:context "doeff-cluster-test" :role "judgment"}}
  "coordinator が受けた key の task の行(1 つだけ在る)。"
  (val rows (lfor t (.values coordinator.state.tasks) :if (= t.key key) t))
  (assert (= (len rows) 1) rows)
  (get rows 0))


(deftest test-the-client-submits-and-awaits-a-detached-task [tmp-path]
  ;; (a): 口の下の送りと待ちが、担い手が走らせた task の答えを返す。名乗りの revision も行に載る。
  (val clock (SimClock))
  (val coordinator (MemoryCoordinator clock))
  (val transport (httpx.MockTransport coordinator.handle))
  (val worker (RigWorker COORDINATOR-URL (/ tmp-path "tasks") (current-versions) :transport transport))
  (<- outcome DetachedSucceeded
      (with-handlers [(sim-time-handler :clock clock) (transport-http transport)]
        (with-detached-client COORDINATOR-URL REVISION None (with-runner worker (submit-and-await "k-client")))))
  (assert (= outcome (DetachedSucceeded 107)) outcome)
  (<- row TaskRecord (task-row coordinator "k-client"))
  (assert (= row.revision REVISION) row.revision))


(deftest test-the-client-submits-under-the-identity-given-by-value
  ;; (b): 呼び手が値で渡した revision と実行環境の宣言が、coordinator の受けた task の行に載る(担い手は要らない — 送るだけ)。
  (val clock (SimClock))
  (val coordinator (MemoryCoordinator clock))
  (val transport (httpx.MockTransport coordinator.handle))
  (<- env RuntimeEnv (declared-env))
  (<- submitted DetachedSubmitted
      (with-handlers [(sim-time-handler :clock clock) (transport-http transport)]
        (with-detached-client COORDINATOR-URL REVISION env
          (submit-detached-task (slow-add 1.0 7) "k-identity" :needs LOCAL :lease-seconds 5.0))))
  (assert (= submitted (DetachedSubmitted "k-identity" True)) submitted)
  (<- row TaskRecord (task-row coordinator "k-identity"))
  (<- declared dict (runtime-env->json env))
  (assert (= #(row.revision row.runtime-env) #(REVISION declared)) #(row.revision row.runtime-env)))
