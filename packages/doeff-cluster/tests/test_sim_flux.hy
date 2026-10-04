;; 模擬の Flux(sim/flux.hy — #3366 の単位 2b)の検: 宣言の置き場(記憶の中の file)に manifest を書き、模擬の Flux で当てると、
;; 2026-10-05 の版上げの順(1 台ずつ・前の 1 台が戻ってから次・coordinator は最後・待ち行列が空の時)では条 V1〜V4 が緑で、
;; 壊した書き方・壊した drain では破った条の名で赤になる。manifest の env の行は本番が宣言を書く時と同じ写し(launch_rules)で作る。
(require doeff-hy.macros [deftest defk <- val var])
(val MODULE-TAGS {:context "doeff-cluster-test" :role "test"})
(import collections.abc [Callable])
(import doeff_time [Delay])
(import doeff_cluster.shared.core.detached_rules [submit-detached-task])
(import doeff_cluster.shared.intent.detached_model [AwaitDetached DetachedLost DetachedSucceeded])
(import doeff_cluster.sim.local [sim-cluster SimWorker SimOutside DrainWorker WorkerOf])
(import doeff_cluster.sim.flux [FluxPass manifest-state reconcile-manifests prestop-drain])
(import tests.flux_fixtures [OLD NEW NO-JOBS PATHS ON-X A B COORDINATOR-SECONDS write-manifest await-back breaches-of flux-outside])
(import tests.detached_rig [slow-add])

(defk upgrade-in-order []
  {:pre [] :post [(: % tuple)] :tags {:context "doeff-cluster-test" :role "program"}}
  "筋書き: 1 台ずつ書いて当て、戻りを読んでから次・coordinator は最後(2026-10-05 の版上げの順)。答え = #(破りの条の名 a の版)。"
  (<- (Delay 3.0))
  (<- applied tuple (manifest-state PATHS))
  (<- (write-manifest NEW OLD OLD))
  (<- first FluxPass (reconcile-manifests PATHS applied prestop-drain COORDINATOR-SECONDS))
  (<- a-back bool (await-back "a" NEW))
  (<- (write-manifest NEW NEW OLD))
  (<- second FluxPass (reconcile-manifests PATHS first.applied prestop-drain COORDINATOR-SECONDS))
  (<- b-back bool (await-back "b" NEW))
  (<- (write-manifest NEW NEW NEW))
  (<- third FluxPass (reconcile-manifests PATHS second.applied prestop-drain COORDINATOR-SECONDS))
  (<- now SimWorker (WorkerOf "a"))
  (<- rules tuple (breaches-of (+ first.starts second.starts third.starts)))
  #(rules a-back b-back now.doeff-commit (len (+ first.starts second.starts third.starts))))


(deftest test-the-2026-10-05-order-through-the-emulated-flux-is-green
  (<- outside SimOutside (flux-outside))
  (<- seen tuple (sim-cluster NO-JOBS (upgrade-in-order) :workers #(A B) :outside outside))
  (assert (= seen #(#() True True NEW 3)) seen))


(defk both-workers-in-one-write []
  {:pre [] :post [(: % tuple)] :tags {:context "doeff-cluster-test" :role "program"}}
  "筋書き(壊した書き方): a と b を 1 回の書きで新しい版にして当てる — 模擬の Flux は違う Deployment を全部同時に作り直す。"
  (<- (Delay 3.0))
  (<- applied tuple (manifest-state PATHS))
  (<- (write-manifest NEW NEW OLD))
  (<- pass FluxPass (reconcile-manifests PATHS applied prestop-drain COORDINATOR-SECONDS))
  (<- rules tuple (breaches-of pass.starts))
  rules)


(deftest test-two-workers-in-one-write-break-v3
  (<- outside SimOutside (flux-outside))
  (<- rules tuple (sim-cluster NO-JOBS (both-workers-in-one-write) :workers #(A B) :outside outside))
  (assert (= rules #("V3 one-worker-at-a-time")) rules))


(defk coordinator-first []
  {:pre [] :post [(: % tuple)] :tags {:context "doeff-cluster-test" :role "program"}}
  "筋書き(壊した書き方): worker を上げる前に coordinator を新しい版にして当てる。"
  (<- (Delay 3.0))
  (<- applied tuple (manifest-state PATHS))
  (<- (write-manifest OLD OLD NEW))
  (<- pass FluxPass (reconcile-manifests PATHS applied prestop-drain COORDINATOR-SECONDS))
  (<- rules tuple (breaches-of pass.starts))
  rules)


(deftest test-the-coordinator-before-the-workers-breaks-v1
  (<- outside SimOutside (flux-outside))
  (<- rules tuple (sim-cluster NO-JOBS (coordinator-first) :workers #(A B) :outside outside))
  (assert (= rules #("V1 coordinator-after-every-worker")) rules))


(defk drain-without-waiting [name]
  {:pre [(: name str)] :post [(: % None)] :tags {:context "doeff-cluster-test" :role "program"}}
  "壊した drain(本番の preStop の空くのを待つ手を欠いた形): drain を頼むだけで止めへ進む。"
  (<- (DrainWorker name))
  None)


(defk swap-a-under-a-running-task [drain]
  {:pre [(: drain Callable)] :post [(: % tuple)] :tags {:context "doeff-cluster-test" :role "program"}}
  "筋書き: a に 20 秒の task を出し、走り出した後で a を新しい版にして当てる。答え = #(破りの条の名 task の結末)。"
  (<- (Delay 3.0))
  (<- applied tuple (manifest-state PATHS))
  (<- (submit-detached-task (slow-add 20.0 4) :key "k-run" :needs ON-X :lease-seconds 60.0))
  (<- (Delay 3.0))
  (<- (write-manifest NEW OLD OLD))
  (<- pass FluxPass (reconcile-manifests PATHS applied drain COORDINATOR-SECONDS))
  (<- outcome (AwaitDetached "k-run"))
  (<- rules tuple (breaches-of pass.starts))
  #(rules outcome))


(deftest test-a-drain-that-does-not-wait-breaks-v2-and-loses-the-running-task
  (<- outside SimOutside (flux-outside))
  (<- broken tuple (sim-cluster NO-JOBS (swap-a-under-a-running-task drain-without-waiting) :workers #(A B)
                                :outside outside))
  (assert (= (get broken 0) #("V2 worker-swap-waits-for-its-tasks")) broken)
  (assert (isinstance (get broken 1) DetachedLost) broken)
  ;; 本番の preStop と同じく空くのを待つ drain なら破りは無く、task は走り切る。
  (<- waited tuple (sim-cluster NO-JOBS (swap-a-under-a-running-task prestop-drain) :workers #(A B)
                                :outside outside))
  (assert (= waited #(#() (DetachedSucceeded 104))) waited))


(defk coordinator-with-a-queued-task []
  {:pre [] :post [(: % tuple)] :tags {:context "doeff-cluster-test" :role "program"}}
  "筋書き(壊した順): worker を全部上げた後、a を 30 秒の task で埋めて次の task を queued にしたまま coordinator を当てる。"
  (<- (Delay 3.0))
  (<- applied tuple (manifest-state PATHS))
  (<- (write-manifest NEW OLD OLD))
  (<- first FluxPass (reconcile-manifests PATHS applied prestop-drain COORDINATOR-SECONDS))
  (<- (await-back "a" NEW))
  (<- (write-manifest NEW NEW OLD))
  (<- second FluxPass (reconcile-manifests PATHS first.applied prestop-drain COORDINATOR-SECONDS))
  (<- (await-back "b" NEW))
  (<- (submit-detached-task (slow-add 30.0 1) :key "k-busy" :needs ON-X :lease-seconds 60.0))
  (<- (Delay 3.0))
  (<- (submit-detached-task (slow-add 1.0 2) :key "k-queued" :needs ON-X :lease-seconds 60.0))
  (<- (Delay 1.0))
  (<- (write-manifest NEW NEW NEW))
  (<- third FluxPass (reconcile-manifests PATHS second.applied prestop-drain COORDINATOR-SECONDS))
  (<- rules tuple (breaches-of third.starts))
  rules)


(deftest test-the-coordinator-with-a-queued-task-breaks-v4
  (<- outside SimOutside (flux-outside))
  (<- rules tuple (sim-cluster NO-JOBS (coordinator-with-a-queued-task) :workers #(A B) :outside outside))
  (assert (= rules #("V4 coordinator-swap-on-an-empty-queue")) rules))
