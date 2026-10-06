;; 模擬の Flux(sim/flux.hy — #3366 の単位 2b)の検: 宣言の置き場(記憶の中の file)に manifest を書き、模擬の Flux で当てると、
;; 2026-10-05 の版上げの順(1 台ずつ・前の 1 台が戻ってから次・coordinator は最後・待ち行列が空の時)では条 V1〜V4 が緑で、
;; 壊した書き方・壊した drain では破った条の名で赤になる。manifest の env の行は本番が宣言を書く時と同じ写し(launch_rules)で作る。
;; worker の drain は本番の preStop と同じ待ち(prestop-drain — coordinator が drained と答えるか上限まで頼み直す・#3669)。
(require doeff-hy.macros [deftest defk <- val var])
(val MODULE-TAGS {:context "doeff-cluster-test" :role "test"})
(import doeff_events [MemoryBroker])
(import collections.abc [Callable])
(import doeff_time [Delay])
(import doeff_cluster.shared.core.detached_rules [submit-detached-task])
(import doeff_cluster.shared.intent.detached_model [AwaitDetached DetachedLost DetachedSucceeded])
(import doeff_cluster.shared.core.clock [now-epoch-ms])
(import doeff_cluster.sim.local [sim-cluster SimWorker SimOutside DrainWorker WorkerOf ProcessesOf])
(import doeff_cluster.sim.flux [FluxPass manifest-state reconcile-manifests prestop-drain])
(import doeff_cluster.worker.core.drain_client [DRAIN-DEADLINE-SECONDS DRAIN-INTERVAL-SECONDS])
(import tests.flux_fixtures [OLD NEW NO-JOBS PATHS ON-X A B COORDINATOR-SECONDS SAME-VERSION write-manifest await-back breaches-of
                            flux-outside])
(import tests.detached_rig [slow-add])
(import tests.fixtures.envs [sim-foundation])
(import tests.fixtures.sim_programs [host-a-pulses])

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
  (<- rules tuple (breaches-of (+ first.starts second.starts third.starts) SAME-VERSION))
  #(rules a-back b-back now.doeff-commit (len (+ first.starts second.starts third.starts))))


(deftest test-the-2026-10-05-order-through-the-emulated-flux-is-green
  (<- outside SimOutside (flux-outside))
  (<- seen tuple (sim-cluster :notice-broker (MemoryBroker) NO-JOBS (upgrade-in-order) :workers #(A B) :outside outside))
  (assert (= seen #(#() True True NEW 3)) seen))


(defk both-workers-in-one-write []
  {:pre [] :post [(: % tuple)] :tags {:context "doeff-cluster-test" :role "program"}}
  "筋書き(壊した書き方): a と b を 1 回の書きで新しい版にして当てる — 模擬の Flux は違う Deployment を全部同時に作り直す。"
  (<- (Delay 3.0))
  (<- applied tuple (manifest-state PATHS))
  (<- (write-manifest NEW NEW OLD))
  (<- pass FluxPass (reconcile-manifests PATHS applied prestop-drain COORDINATOR-SECONDS))
  (<- rules tuple (breaches-of pass.starts SAME-VERSION))
  rules)


(deftest test-two-workers-in-one-write-break-v3
  (<- outside SimOutside (flux-outside))
  (<- rules tuple (sim-cluster :notice-broker (MemoryBroker) NO-JOBS (both-workers-in-one-write) :workers #(A B) :outside outside))
  (assert (= rules #("V3 one-worker-at-a-time")) rules))


(defk coordinator-first []
  {:pre [] :post [(: % tuple)] :tags {:context "doeff-cluster-test" :role "program"}}
  "筋書き(壊した書き方): worker を上げる前に coordinator を新しい版にして当てる。"
  (<- (Delay 3.0))
  (<- applied tuple (manifest-state PATHS))
  (<- (write-manifest OLD OLD NEW))
  (<- pass FluxPass (reconcile-manifests PATHS applied prestop-drain COORDINATOR-SECONDS))
  (<- rules tuple (breaches-of pass.starts SAME-VERSION))
  rules)


(deftest test-the-coordinator-before-the-workers-breaks-v1
  (<- outside SimOutside (flux-outside))
  (<- rules tuple (sim-cluster :notice-broker (MemoryBroker) NO-JOBS (coordinator-first) :workers #(A B) :outside outside))
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
  (<- rules tuple (breaches-of pass.starts SAME-VERSION))
  #(rules outcome))


(deftest test-a-drain-that-does-not-wait-breaks-v2-and-loses-the-running-task
  (<- outside SimOutside (flux-outside))
  (<- broken tuple (sim-cluster :notice-broker (MemoryBroker) NO-JOBS (swap-a-under-a-running-task drain-without-waiting) :workers #(A B)
                                :outside outside))
  (assert (= (get broken 0) #("V2 worker-swap-waits-for-its-tasks")) broken)
  (assert (isinstance (get broken 1) DetachedLost) broken)
  ;; 本番の preStop と同じく空くのを待つ drain なら破りは無く、task は走り切る。
  (<- waited tuple (sim-cluster :notice-broker (MemoryBroker) NO-JOBS (swap-a-under-a-running-task prestop-drain) :workers #(A B)
                                :outside outside))
  (assert (= waited #(#() (DetachedSucceeded 104))) waited))


(defk swap-a-under-a-job-only-a-can-hold []
  {:pre [] :post [(: % tuple)] :tags {:context "doeff-cluster-test" :role "program"}}
  "筋書き(#3669 — 2026-10-05 の記録の表の service の入れ替えの形): a だけが持つ能力 host-a を要る service pulse が a で動く中、a を新しい
   版にして当て、a が戻るのを待つ。答え = #(破りの条の名 drain の待ちの秒 a が戻ったか 戻った後の pulse の process の worker と exit-code)。
   待ちの秒 = 当てを始めてから入れ替えを始めた瞬間(古い process が止まる瞬間 — drain の後)までの仮想の秒。"
  (<- (Delay 5.0))
  (<- applied tuple (manifest-state PATHS))
  (<- (write-manifest NEW OLD OLD))
  (<- asked int (now-epoch-ms))
  (<- pass FluxPass (reconcile-manifests PATHS applied prestop-drain COORDINATOR-SECONDS))
  (<- back bool (await-back "a" NEW))
  (<- (Delay 5.0))
  (<- processes tuple (ProcessesOf "pulse"))
  (val last (get processes -1))
  (<- rules tuple (breaches-of pass.starts SAME-VERSION))
  #(rules (/ (- (. (get pass.starts 0) at-ms) asked) 1000.0) back #(last.worker last.exit-code)))


(deftest test-a-drain-whose-job-no-other-worker-can-hold-does-not-wait-for-the-deadline
  ;; 失敗ケース(#3669): 能力の合う別の worker が名簿に無い job(host-a は a だけが持つ)を持つ worker の drain は、待っても移す先が来ない。
  ;; 本番の preStop と同じ待ち(prestop-drain)で、上限(DRAIN-DEADLINE-SECONDS)を待たずに drained で終わり、a の入れ替えへ進む。
  ;; job は a の上で止まるまで動き、新しい世代の a で動き直す(移せる先は無いので、置き先は a のまま)。
  (<- outside SimOutside (flux-outside))
  (<- seen tuple (sim-cluster :notice-broker (MemoryBroker) (host-a-pulses sim-foundation) (swap-a-under-a-job-only-a-can-hold) :workers #(A B) :outside outside))
  (assert (= (get seen 0) #()) seen)
  ;; 直す前は上限まで待って timeout(待ちの秒 = 90.0)。直した後は 1 回目の頼みの答えが drained(頼み直しの間隔より短い)。
  (assert (< (get seen 1) DRAIN-DEADLINE-SECONDS) seen)
  (assert (< (get seen 1) DRAIN-INTERVAL-SECONDS) seen)
  (assert (get seen 2) seen)
  (assert (= (get seen 3) #("a" None)) seen))


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
  (<- rules tuple (breaches-of third.starts SAME-VERSION))
  rules)


(deftest test-the-coordinator-with-a-queued-task-breaks-v4
  (<- outside SimOutside (flux-outside))
  (<- rules tuple (sim-cluster :notice-broker (MemoryBroker) NO-JOBS (coordinator-with-a-queued-task) :workers #(A B) :outside outside))
  (assert (= rules #("V4 coordinator-swap-on-an-empty-queue")) rules))


(deftest test-the-emulated-flux-imports-without-yaml
  ;; #3566: doeff-cluster の source は manifest の書式(YAML)を読まない — 模擬の Flux そのものが、yaml を塞いだ process で import できる
  ;; (書式を文書にするのは配備する側が ManifestDocuments に答える handler)。新しい process で、yaml の import を塞いでから読む。
  (import subprocess)
  (import sys)
  (val code "import sys; sys.modules['yaml'] = None; import hy; import doeff_cluster.sim.flux; print('ok')")
  (val done (subprocess.run [sys.executable "-c" code] :capture-output True :text True))
  (assert (= done.returncode 0) done.stderr)
  (assert (= (.strip done.stdout) "ok") done.stdout))
