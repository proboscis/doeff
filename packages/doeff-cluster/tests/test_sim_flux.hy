;; 模擬の Flux(sim/flux.hy — #3366 の単位 2b)の検: 宣言の置き場(記憶の中の file)に manifest を書き、模擬の Flux で当てると、
;; 2026-10-05 の版上げの順(1 台ずつ・前の 1 台が戻ってから次・coordinator は最後・待ち行列が空の時)では条 V1〜V4 が緑で、
;; 壊した書き方・壊した drain では破った条の名で赤になる。manifest の env の行は本番が宣言を書く時と同じ写し(launch_rules)で作る。
(require doeff-hy.macros [deftest defk <- val var])
(val MODULE-TAGS {:context "doeff-cluster-test" :role "test"})
(import collections.abc [Callable])
(import yaml)
(import doeff_time [Delay])
(import doeff_core_effects.file_effects [ReadText WriteText MemoryFile MemoryFiles])
(import doeff_core_effects.memory_file [memory-file-handler])
(import doeff_cluster.shared.entry.service_build [system-of])
(import doeff_cluster.shared.core.detached_rules [submit-detached-task])
(import doeff_cluster.shared.intent.detached_model [AwaitDetached DetachedLost DetachedSucceeded])
(import doeff_cluster.shared.intent.launch_model [WorkerLaunch CoordinatorLaunch])
(import doeff_cluster.shared.core.launch_rules [worker-launch-env coordinator-launch-env])
(import doeff_cluster.coordinator.core.upgrade_invariants [UpgradeKind coordinator-after-every-worker worker-swap-waits-for-its-tasks
                                                            one-worker-at-a-time coordinator-swap-on-an-empty-queue])
(import doeff_cluster.sim.local [sim-cluster SimWorker SimOutside DrainWorker WorkerOf])
(import doeff_cluster.sim.flux [FluxPass manifest-state reconcile-manifests prestop-drain roster-snapshot])
(import tests.detached_rig [slow-add])

(val OLD "d563ab95a0000000000000000000000000000000")
(val NEW "90fd9a81d97ddf1cf5ae13a4036fa615108abbe7")
(val NO-JOBS (system-of "flux-scenarios" #()))
(val MANIFEST "/flux/deploy.yaml")
(val PATHS #(MANIFEST))
(val ON-X (frozenset ["x-tool"]))
(val A (SimWorker :name "a" :provides (frozenset ["x-tool" "host-a"]) :task-reserve 0 :capacity 1 :doeff-commit OLD))
(val B (SimWorker :name "b" :provides (frozenset ["y-tool" "host-b"]) :task-reserve 0 :capacity 1 :doeff-commit OLD))
(val JUDGES #(coordinator-after-every-worker worker-swap-waits-for-its-tasks one-worker-at-a-time coordinator-swap-on-an-empty-queue))
(val COORDINATOR-SECONDS 5.0)


(defk deployment [name role env]
  {:pre [(: name str) (: role str) (: env tuple)] :post [(: % dict)] :tags {:context "doeff-cluster-test" :role "program"}}
  "Deployment 1 つの YAML の文書(dict — 筋書きが manifest を綴る境界)。env の行に ROLE を足す。"
  {"apiVersion" "apps/v1" "kind" "Deployment" "metadata" {"name" name}
   "spec" {"template" {"spec" {"containers" [{"name" name
                                              "env" (+ [{"name" "ROLE" "value" role}]
                                                       (lfor e env {"name" e.name "value" e.value}))}]}}}})


(defk manifest-of [a-commit b-commit coordinator-commit]
  {:pre [(: a-commit str) (: b-commit str) (: coordinator-commit str)] :post [(: % str)] :tags {:context "doeff-cluster-test" :role "program"}}
  "worker a・b と coordinator の Deployment の manifest — env の行は本番が宣言を書く時と同じ写し(worker-launch-env)で作る。"
  (<- a-env tuple (worker-launch-env (WorkerLaunch :name "a" :provides #("x-tool" "host-a") :exclusive #() :capacity 1 :task-reserve 0
                                                   :doeff-commit a-commit)))
  (<- b-env tuple (worker-launch-env (WorkerLaunch :name "b" :provides #("y-tool" "host-b") :exclusive #() :capacity 1 :task-reserve 0
                                                   :doeff-commit b-commit)))
  (<- c-env tuple (coordinator-launch-env (CoordinatorLaunch :doeff-commit coordinator-commit)))
  (<- doc-a dict (deployment "worker-a" "worker" a-env))
  (<- doc-b dict (deployment "worker-b" "worker" b-env))
  (<- doc-c dict (deployment "coordinator" "coordinator" c-env))
  (yaml.safe-dump-all [doc-a doc-b doc-c] :sort-keys False))


(defk write-manifest [a-commit b-commit coordinator-commit]
  {:pre [(: a-commit str) (: b-commit str) (: coordinator-commit str)] :post [(: % None)] :tags {:context "doeff-cluster-test" :role "program"}}
  "宣言の置き場(git の main の代わり)の manifest を書く(本番の 1 commit の着地に当たる)。"
  (<- text str (manifest-of a-commit b-commit coordinator-commit))
  (<- (WriteText MANIFEST text :replace True))
  None)


(defk await-back [name commit]
  {:pre [(: name str) (: commit str)] :post [(: % bool)] :tags {:context "doeff-cluster-test" :role "program"}}
  "worker name が版 commit で live に戻るのを読む(版上げの Program が次へ進む前に読む物 — 60 秒まで)。"
  (var back False)
  (var waited 0)
  (while (and (not back) (< waited 60))
    (<- (Delay 1.0))
    (:= waited (+ waited 1))
    (<- seen (roster-snapshot UpgradeKind.WORKER name commit))
    (:= back (any (gfor e seen.roster (and (= e.worker name) e.live (= e.doeff-commit commit))))))
  back)


(defk breaches-of [starts]
  {:pre [(: starts tuple)] :post [(: % tuple)] :tags {:context "doeff-cluster-test" :role "program"}}
  "入れ替えの記録の列に、条 V1〜V4 の判定を全部当てた破りの条の名(重ねない・名の順)。"
  (var rules #())
  (for [judge JUDGES]
    (<- found tuple (judge starts))
    (:= rules (+ rules (tuple (gfor b found b.rule)))))
  (tuple (sorted (set rules))))


(defk flux-outside []
  {:pre [] :post [(: % SimOutside)] :tags {:context "doeff-cluster-test" :role "program"}}
  "宣言の置き場(記憶の中の file — 初めの中身は a・b・coordinator が全部旧い版の manifest)を sim の外の世界として置くため — 模擬の
   Flux と筋書きが ReadText・WriteText で読み書きする。"
  (<- text str (manifest-of OLD OLD OLD))
  (SimOutside :handlers [(memory-file-handler (MemoryFiles :files #((MemoryFile :path MANIFEST :content (.encode text "utf-8")))
                                                           :dirs #("/flux")))]
              :effects #(ReadText WriteText)))


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
