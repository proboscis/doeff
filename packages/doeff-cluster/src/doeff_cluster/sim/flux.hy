;;; 模擬の Flux — 宣言の置き場(git の main の代わり = 記憶の中の file の置き場・ReadText で読む)の manifest を読み、Deployment の env が
;;; 前に当てた物と違う worker / coordinator を作り直す(本物の Flux・k8s・Pod・boot.sh の代わり — #3366 の単位 2b)。
;;;
;;;   (<- applied (manifest-state paths))                                  ; 今の宣言を「当てた物」として読むだけ(何も作り直さない)
;;;   (<- pass FluxPass (reconcile-manifests paths applied prestop-drain 5.0))  ; 1 回当てる — 答え = 当てた物と、入れ替えを始めた瞬間の記録
;;;
;;; 当てるのは呼ばれた時の 1 回だけ(本番の Program が置く「すぐ読み直せ」の印に当たる)— 間隔で回り続けない(筋書きに要る回数だけ・
;;; cisco-c8 の条件 3)。1 回の当てで違う Deployment は全部同時に作り直す(本物の Flux と同じ — 1 台ずつにするのは版上げの Program の役)。
;;; worker の作り直しは本番の Recreate と同じ順: drain(preStop — 渡す drain の Program)→ 止め → 値の差し替え(ReplaceWorker)→ 新しい
;;; 世代(StartWorker)。入れ替えを始めた瞬間 = 古い process が止まる瞬間(drain の後)に、名簿と task の写し(UpgradeStart)を残す —
;;; 条 V1〜V4(coordinator/core/upgrade_invariants.hy)が判じる。
;;;
;;; env から worker / coordinator の値への読みは launch_rules の表(worker-launch-of-env・coordinator-launch-of-env)だけを通す。表から
;;; 引けない欄 = ROLE(Deployment が worker か coordinator かの見分け — 起動の値ではない)。
;;; yaml を import するのはこの module だけ(依存の組 doeff-cluster[sim])— sim/__init__ と既存の sim の module はこの module を引かない。
(require doeff-hy.macros [val var defk <-])
(require doeff-hy.record [defrecord])
(val MODULE-TAGS {:context "doeff-cluster" :role "program"})
(import collections.abc [Callable])
(import dataclasses [dataclass replace])
(import yaml)
(import doeff_core_effects.file_effects [ReadText FileFailed])
(import doeff_core_effects.process_effects [EnvEntry])
(import doeff_core_effects.scheduler [Spawn Gather Task])
(import doeff_cluster.shared.core.clock [now-epoch-ms])
(import doeff_cluster.shared.core.launch_rules [worker-launch-of-env coordinator-launch-of-env])
(import doeff_cluster.shared.intent.launch_model [WorkerLaunch CoordinatorLaunch])
(import doeff_cluster.shared.intent.detached_model [AwaitDetached])
(import doeff_cluster.coordinator.core.upgrade_invariants [UpgradeKind PendingPhase RosterEntry PendingTask UpgradeStart])
(import doeff_cluster.sim.local [SimWorker HostTruth DrainWorker StopWorker ReplaceWorker StartWorker WorkerOf HostTruthOf
                                 StopCoordinator ReadCoordinator])


;; Deployment の role(env の ROLE)のうち、模擬の Flux が作り直す物。
(val WORKER-ROLE "worker")
(val COORDINATOR-ROLE "coordinator")
;; coordinator の task の phase のうち、worker に置かれている物(置かれた・準備中・走り中)。
(val PLACED-PHASES (frozenset #("assigned" "preparing" "running")))


(defrecord DeployedEnv
  "manifest の Deployment 1 つの env を模擬の Flux が読んだ物: deployment = Deployment の名・role = env の ROLE(worker / coordinator)・
   env = 値を直に持つ env の行(valueFrom の行は持たない — 起動の値の表の名はどれも値を直に持つ)。"
  {:tags {:context "doeff-cluster" :role "type"}}
  (#^ str deployment)
  (#^ str role)
  (#^ (get tuple #(EnvEntry ...)) env))


(defrecord FluxPass
  "1 回の当ての答え: applied = 当てた後の宣言(次の当ての比べの元)・starts = この当てで入れ替えを始めた瞬間の記録(始めた順)。"
  {:tags {:context "doeff-cluster" :role "type"}}
  (#^ (get tuple #(DeployedEnv ...)) applied)
  (#^ (get tuple #(UpgradeStart ...)) starts))


(defk deployed-envs [text]
  {:pre [(: text str)] :post [(: % (get tuple #(DeployedEnv ...)))] :tags {:context "doeff-cluster" :role "judgment"}}
  "manifest の YAML の text から、ROLE が worker か coordinator の Deployment の env を読むため(本物の Flux と k8s が Pod の env にする
   物の代わり)。YAML の文書は dict の JSON の境界として読み、ここで型の付いた DeployedEnv にする。"
  (val docs (tuple (gfor d (yaml.safe-load-all text) :if (and (isinstance d dict) (= (.get d "kind") "Deployment")) d)))
  (tuple (gfor d docs
               c (get (get (get (get d "spec") "template") "spec") "containers")
               :setv env (tuple (gfor e (.get c "env" []) :if (in "value" e)
                                      (EnvEntry :name (get e "name") :value (str (get e "value")))))
               :setv role (next (gfor e env :if (= e.name "ROLE") e.value) "")
               :if (in role #(WORKER-ROLE COORDINATOR-ROLE))
               (DeployedEnv :deployment (get (get d "metadata") "name") :role role :env env))))


(defk manifest-state [paths]
  {:pre [(: paths (get tuple #(str ...)))] :post [(: % (get tuple #(DeployedEnv ...)))] :tags {:context "doeff-cluster" :role "program"}}
  "宣言の置き場の manifest(paths)の今の Deployment の env を読むため — 筋書きの初めの「当てた物」(今の模擬の worker と同じ宣言)にし、
   reconcile-manifests の比べの元に渡す。"
  (var found #())
  (for [path paths]
    (<- text (ReadText path))
    (when (isinstance text FileFailed)
      (raise (RuntimeError (.format "宣言 {} を読めない: {}" path text))))
    (<- envs (get tuple #(DeployedEnv ...)) (deployed-envs text))
    (:= found (+ found envs)))
  found)


(defk roster-snapshot [kind target commit]
  {:pre [(: kind UpgradeKind) (: target str) (: commit str)] :post [(: % UpgradeStart)] :tags {:context "doeff-cluster" :role "program"}}
  "入れ替えを始めた瞬間の記録を作るため(条 V1〜V4 が判じる写し)。名簿 = coordinator の状態の worker ごとの live と、その worker の今の
   世代が動いている版(sim の WorkerOf)— live は、coordinator が live と答え、宿が止まっておらず、今の世代の heartbeat に coordinator が
   1 度でも返事をした時(作り直した直後の新しい世代が名乗り終える前は live に数えない)。task = queued と、worker に置かれた物。"
  (<- at int (now-epoch-ms))
  (<- state dict (ReadCoordinator "/state"))
  (var roster #())
  (for [#(name view) (.items (get state "workers"))]
    (<- truth HostTruth (HostTruthOf name))
    (<- now SimWorker (WorkerOf name))
    (:= roster (+ roster #((RosterEntry :worker name :live (and (bool (get view "live")) (not truth.down) (> truth.beats 0))
                                        :doeff-commit now.doeff-commit)))))
  (val tasks (tuple (gfor t (get state "tasks")
                          :if (or (= (get t "phase") "queued") (in (get t "phase") PLACED-PHASES))
                          (if (= (get t "phase") "queued")
                              (PendingTask :task (get t "id") :phase PendingPhase.QUEUED :worker None)
                              (PendingTask :task (get t "id") :phase PendingPhase.ASSIGNED :worker (get t "worker"))))))
  (UpgradeStart :at-ms at :kind kind :target target :doeff-commit commit :roster roster :tasks tasks))


(defk prestop-drain [name]
  {:pre [(: name str)] :post [(: % None)] :tags {:context "doeff-cluster" :role "program"}}
  "本番の preStop(drain_main の await-drained — drain を頼み、空くのを待ってから止める)の代わり: drain を頼み、その worker に置かれた
   切り離した task が終わるのを待つため(sim の DrainWorker は頼むだけで空くのを待たない — 本物と違う所を、ここで埋める)。"
  (<- asked dict (DrainWorker name))
  (when (!= (.get asked "status") 200)
    (raise (RuntimeError (.format "worker {} の drain が断られた: {}" name asked))))
  (<- state dict (ReadCoordinator "/state"))
  (for [t (get state "tasks")]
    (when (and (= (.get t "worker") name) (in (get t "phase") PLACED-PHASES) (.get t "detached"))
      (<- (AwaitDetached (get t "key")))))
  None)


(defk recreate-worker [deployed drain]
  {:pre [(: deployed DeployedEnv) (: drain Callable)] :post [(: % UpgradeStart)] :tags {:context "doeff-cluster" :role "program"}}
  "worker の Deployment 1 つを本番の Recreate と同じ順で作り直すため: drain(渡された preStop の代わり)→ 止まる瞬間の記録 → 止め →
   値の差し替え(env から launch_rules の表で読んだ値)→ 新しい世代。答え = 入れ替えを始めた瞬間の記録。"
  (<- launch WorkerLaunch (worker-launch-of-env deployed.env))
  (<- (drain launch.name))
  (<- start UpgradeStart (roster-snapshot UpgradeKind.WORKER launch.name launch.doeff-commit))
  (<- (StopWorker launch.name))
  (<- current SimWorker (WorkerOf launch.name))
  (<- replaced bool (ReplaceWorker launch.name (replace current :provides (frozenset launch.provides) :exclusive (frozenset launch.exclusive)
                                                        :capacity launch.capacity :task-reserve launch.task-reserve
                                                        :doeff-commit launch.doeff-commit)))
  (when (not replaced)
    (raise (RuntimeError (.format "worker {} を差し替えられない(止まっていない・名が違う)" launch.name))))
  (<- (StartWorker launch.name))
  start)


(defk recreate-coordinator [deployed seconds]
  {:pre [(: deployed DeployedEnv) (: seconds float)] :post [(: % UpgradeStart)] :tags {:context "doeff-cluster" :role "program"}}
  "coordinator の Deployment を作り直すため: 止まる瞬間の記録 → seconds 秒止めて同じ置き場から作り直す(sim の coordinator は版を
   持たない — 記録の版は宣言の版)。答え = 入れ替えを始めた瞬間の記録。"
  (<- launch CoordinatorLaunch (coordinator-launch-of-env deployed.env))
  (<- start UpgradeStart (roster-snapshot UpgradeKind.COORDINATOR "coordinator" launch.doeff-commit))
  (<- (StopCoordinator seconds))
  start)


(defk reconcile-manifests [paths applied drain coordinator-seconds]
  {:pre [(: paths (get tuple #(str ...))) (: applied (get tuple #(DeployedEnv ...))) (: drain Callable) (: coordinator-seconds float)]
   :post [(: % FluxPass)] :tags {:context "doeff-cluster" :role "program"}}
  "宣言の置き場の manifest を 1 回当てるため: 前に当てた物(applied)と違う Deployment を全部同時に作り直す(本物の Flux と同じ)。
   drain = worker を止める前の待ち(本番の preStop の代わり — 正しい形は prestop-drain)・coordinator-seconds = coordinator の止まりの秒。"
  (<- desired (get tuple #(DeployedEnv ...)) (manifest-state paths))
  (val changed (tuple (gfor d desired :if (not-in d applied) d)))
  (var spawned #())
  (for [d changed]
    (<- task Task (Spawn (if (= d.role WORKER-ROLE) (recreate-worker d drain) (recreate-coordinator d coordinator-seconds))))
    (:= spawned (+ spawned #(task))))
  (<- starts list (Gather #* spawned))
  (FluxPass :applied desired :starts (tuple (sorted starts :key (fn [s] s.at-ms)))))
