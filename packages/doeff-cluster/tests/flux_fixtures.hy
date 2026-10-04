;; 模擬の Flux と版上げの Program の検が共に使う道具(#3366): worker a・b と coordinator の manifest を本番が宣言を書く時と同じ写し
;; (launch_rules)で作る・宣言の置き場(記憶の中の file)を sim の外の世界に置く・条 V1〜V4 を全部当てる。
(require doeff-hy.macros [defk <- val var])
(val MODULE-TAGS {:context "doeff-cluster-test" :role "test"})
(import collections.abc [Callable])
(import yaml)
(import doeff_time [Delay])
(import doeff_core_effects.file_effects [ReadText WriteText MemoryFile MemoryFiles])
(import doeff_core_effects.memory_file [memory-file-handler])
(import doeff_cluster.shared.entry.service_build [system-of])
(import doeff_cluster.shared.intent.launch_model [WorkerLaunch CoordinatorLaunch])
(import doeff_cluster.shared.intent.upgrade_model [UpgradeKind])
(import doeff_cluster.shared.core.launch_rules [worker-launch-env coordinator-launch-env])
(import doeff_cluster.coordinator.core.upgrade_invariants [coordinator-after-every-worker worker-swap-waits-for-its-tasks
                                                            one-worker-at-a-time coordinator-swap-on-an-empty-queue])
(import doeff_cluster.sim.local [SimWorker SimOutside])
(import doeff_cluster.sim.flux [roster-snapshot])

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
