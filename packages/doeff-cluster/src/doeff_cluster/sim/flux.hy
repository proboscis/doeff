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
;;; manifest の書式(YAML)は読まない — manifest は配備する側の repo の物(README の「manifest は配備する側の repo が持つ」)。text を
;;; 文書(dict の列)にするのは、配備する側が ManifestDocuments に答える handler(配備する側の検と、この package の検は yaml で答える)。
;;; doeff-cluster の source は yaml を import しない(test_package_independence の許可表のまま — #3566)。
;;;
;;; 自己起動の root の保存先(本物の worker / coordinator の $WORK_DIR の代わり — #3725)は、版上げの Program の effect に答える
;;; flux-declarations が持つ: 在る root = 今 動いている版の物(その版で起動した — worker は模擬の世界の今の世代の版・coordinator は
;;; 当たっている宣言の版)と、PrepareBootRoot で先に準備した物(足すだけで消さない)。
;;; 当てた瞬間に、入れ替える対象の保存先のスナップショット(BootRootsAtStart)を残す — 条 V5 が判定する。宣言を当てるだけの reconcile-manifests は
;;; 保存先を持たない(版上げの Program を通さずに手で当てる筋書きには、準備の handler が居ない)。
;;;
;;; 名簿の内容(#3772): 宣言の内か外か = 当たっている宣言に、その名の worker の Deployment が在るか。coordinator の版 = 答えた coordinator が
;;; GET /state の答えの欄 coordinatorCommit で申告する版(本番の読みと同じ shared/protocol/coordinator_reads の coordinator-commit-of-state)。
;;; 模擬の Flux は coordinator を作り直す時、Deployment の env を模擬の Pod の環境として渡す(ReplaceCoordinatorEnviron — 次の一生から
;;; 効く)ので、作り直した coordinator は本番と同じ入口の読み(WORKER_DOEFF_COMMIT)で新しい版を申告する。
;;; 静かな時間帯の待ち(AwaitQuietWindow)には、模擬の世界ではすぐ「静か」と答える。worker の当てる直前の drain(AwaitWorkerDrained)には、
;;; 模擬の世界ではすぐ「中の仕事は 0」と答える(模擬の worker は長く生きる task の中で子の仕事を回さない — 数える物は配備する側の答え手が持つ)。
;;; その drain を外す頼み(ReleaseWorkerDrain)には何もせずに答える(模擬の答え手は drain の印を置いていない)。
(require doeff-hy.macros [val var defk defhandler defeffect <-])
(require doeff-hy.record [defrecord])
(val MODULE-TAGS {:context "doeff-cluster" :role "program"})
(import collections.abc [Callable])
(import dataclasses [dataclass replace])
(import doeff_core_effects.file_effects [ReadText FileFailed])
(import doeff_core_effects.process_effects [EnvEntry])
(import doeff_core_effects.scheduler [Spawn Gather Task])
(import doeff_cluster.shared.core.clock [now-epoch-ms])
(import doeff_cluster.shared.core.launch_rules [worker-launch-of-env coordinator-launch-of-env coordinator-launch-names])
(import doeff_cluster.shared.intent.launch_model [WorkerLaunch CoordinatorLaunch])
(import doeff [with-handlers])
(import doeff_cluster.shared.intent.remote_model [RemoteJobFailed])
(import doeff_cluster.worker.core.drain_client [await-drained DRAIN-DEADLINE-SECONDS DRAIN-INTERVAL-SECONDS])
(import doeff_cluster.worker.intent.drain_model [AskDrain])
(import doeff_cluster.shared.intent.upgrade_model [UpgradeKind PendingPhase WorkerDeclaration RosterEntry PendingTask UpgradeStart
                                                   UpgradeState UpgradeStateUnreachable ReadUpgradeState PublishDeclarations
                                                   ApplyDeclarations ConfirmCleanBoot CleanBootPassed CleanBootRefused BootRootsAtStart
                                                   PrepareBootRoot BootRootAlreadyPrepared BootRootBuilt BootRootRefused BootRootRefusal
                                                   AwaitQuietWindow QuietWindowOpened AwaitWorkerDrained WorkerDrained
                                                   ReleaseWorkerDrain])
(import doeff_cluster.shared.protocol.coordinator_reads [coordinator-commit-of-state])
(import doeff_cluster.sim.local [SimWorker HostTruth DrainWorker StopWorker ReplaceWorker StartWorker WorkerOf HostTruthOf
                                 StopCoordinator ReadCoordinator ReplaceCoordinatorEnviron])


;; Deployment の role(env の ROLE)のうち、模擬の Flux が作り直す物。
(val WORKER-ROLE "worker")
(val COORDINATOR-ROLE "coordinator")
;; coordinator の task の phase のうち、worker に置かれている物(置かれた・準備中・走り中)。
(val PLACED-PHASES (frozenset #("assigned" "preparing" "running")))
;; 模擬の保存先が自己起動の root を組むのにかかる秒 — 待たずに組む(本番は root の準備と初回の import で 15〜25 秒・空の保存先なら分の単位)。
(val SIM-BUILD-SECONDS 0.0)


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


(defeffect ManifestDocuments
  "manifest の text 1 つを、文書(dict の列 — k8s の object 1 つが 1 つ)にしてもらうため。書式は配備する側の物なので、答えるのは配備する側の
   handler(この package は書式を知らない — 頭の註)。dict は JSON の境界の値で、deployed-envs が型の付いた DeployedEnv にする。"
  {:fields [(: text str)]
   :answer (get tuple #(dict ...))
   :tags {:context "doeff-cluster" :role "intent"}})


(defk deployed-envs [documents]
  {:pre [(: documents (get tuple #(dict ...)))] :post [(: % (get tuple #(DeployedEnv ...)))] :tags {:context "doeff-cluster" :role "judgment"}}
  "manifest の文書(ManifestDocuments の答え)から、ROLE が worker か coordinator の Deployment の env を読むため(本物の Flux と k8s が
   Pod の env にする物の代わり)。文書は dict の JSON の境界として読み、ここで型の付いた DeployedEnv にする。"
  (val docs (tuple (gfor d documents :if (and (isinstance d dict) (= (.get d "kind") "Deployment")) d)))
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
    (<- documents (get tuple #(dict ...)) (ManifestDocuments text))
    (<- envs (get tuple #(DeployedEnv ...)) (deployed-envs documents))
    (:= found (+ found envs)))
  found)


(defk declared-workers [applied]
  {:pre [(: applied (get tuple #(DeployedEnv ...)))] :post [(: % (get frozenset str))] :tags {:context "doeff-cluster" :role "program"}}
  "当たっている宣言(applied)に Deployment の在る worker の名を返すため — 名簿の worker が宣言の内か外かの見分け(#3772)。"
  (var names #())
  (for [deployed applied]
    (when (= deployed.role WORKER-ROLE)
      (<- launch WorkerLaunch (worker-launch-of-env deployed.env))
      (:= names (+ names #(launch.name)))))
  (frozenset names))


(defrecord CoordinatorView
  "模擬の coordinator の状態(GET /state)を型にした物: roster = 名簿のコピー・tasks = 終わっていない task のコピー・known-tasks = coordinator が
   知る task の id の全部(終わった物も)・coordinator-commit = 答えた coordinator が申告する doeff の版(欄 coordinatorCommit — 申告しなければ
   None)。名簿の問い合わせ(upgrade-state)と入れ替えの瞬間の記録(roster-snapshot)が同じ 1 つの取り方を使うため。"
  {:tags {:context "doeff-cluster" :role "type"}}
  (#^ (get tuple #(RosterEntry ...)) roster)
  (#^ (get tuple #(PendingTask ...)) tasks)
  (#^ (get tuple #(str ...)) known-tasks)
  (#^ (| str None) coordinator-commit))


(defk coordinator-view [declared]
  {:pre [(: declared (get frozenset str))] :post [(: % (| CoordinatorView UpgradeStateUnreachable))]
   :tags {:context "doeff-cluster" :role "program"}}
  "模擬の coordinator の状態を読み、型の付いた値(CoordinatorView)にするため(JSON の境界はここだけ)。名簿 = worker ごとの live と、
   その worker の今の世代が動いている版(sim の WorkerOf)と、宣言の内か外か(declared に名が在るか)— live は、coordinator が live と
   答え、worker の動く機体(sim の HostTruth)が止まっておらず、今の世代の heartbeat に coordinator が 1 度でも返事をした時(作り直した
   直後の新しい世代が登録を終える前は live に数えない)。task = queued と、worker に置かれた物。coordinator の版 = 答えの欄 coordinatorCommit
   (本番の読みと同じ coordinator-commit-of-state)。coordinator に届かなければ UpgradeStateUnreachable(作り直しの間)。"
  (try
    (<- state dict (ReadCoordinator "/state"))
    (except [failed RemoteJobFailed]
      (return (UpgradeStateUnreachable :reason (str failed)))))
  (var roster #())
  (for [#(name worker) (.items (get state "workers"))]
    (<- truth HostTruth (HostTruthOf name))
    (<- now SimWorker (WorkerOf name))
    (:= roster (+ roster #((RosterEntry :worker name :live (and (bool (get worker "live")) (not truth.down) (> truth.beats 0))
                                        :doeff-commit now.doeff-commit
                                        :declaration (if (in name declared) WorkerDeclaration.DECLARED WorkerDeclaration.UNDECLARED))))))
  (val tasks (tuple (gfor t (get state "tasks")
                          :if (or (= (get t "phase") "queued") (in (get t "phase") PLACED-PHASES))
                          (if (= (get t "phase") "queued")
                              (PendingTask :task (get t "id") :phase PendingPhase.QUEUED :worker None)
                              (PendingTask :task (get t "id") :phase PendingPhase.ASSIGNED :worker (get t "worker"))))))
  (<- commit (| str None) (coordinator-commit-of-state state))
  (CoordinatorView :roster roster :tasks tasks :known-tasks (tuple (gfor t (get state "tasks") (get t "id"))) :coordinator-commit commit))


(defk upgrade-state [declared]
  {:pre [(: declared (get frozenset str))] :post [(: % (| UpgradeState UpgradeStateUnreachable))]
   :tags {:context "doeff-cluster" :role "program"}}
  "sim の世界で、版上げが次へ進むかを決める状態(ReadUpgradeState の sim の答え)を作るため。名簿・task・coordinator の版は
   coordinator-view(declared = 当たっている宣言の worker の名)。coordinator に届かなければ UpgradeStateUnreachable(作り直しの間)。"
  (<- view (| CoordinatorView UpgradeStateUnreachable) (coordinator-view declared))
  (match view
    (UpgradeStateUnreachable) view
    (CoordinatorView) (UpgradeState :roster view.roster :tasks view.tasks :coordinator-commit view.coordinator-commit
                                    :known-tasks view.known-tasks)))


(defk roster-snapshot [kind target commit declared]
  {:pre [(: kind UpgradeKind) (: target str) (: commit str) (: declared (get frozenset str))] :post [(: % UpgradeStart)]
   :tags {:context "doeff-cluster" :role "program"}}
  "入れ替えを始めた瞬間(古い process が止まる瞬間)の記録を作るため(条 V1〜V4 が判定するスナップショット — 状態の取り方は
   upgrade-state と同じ coordinator-view)。declared = その瞬間の宣言の worker の名(条 V1 は宣言の外の worker を数えない)。"
  (<- at int (now-epoch-ms))
  (<- view (| CoordinatorView UpgradeStateUnreachable) (coordinator-view declared))
  (when (isinstance view UpgradeStateUnreachable)
    (raise (RuntimeError (.format "入れ替えの瞬間に coordinator の状態を読めない: {}" view.reason))))
  (UpgradeStart :at-ms at :kind kind :target target :doeff-commit commit :roster view.roster :tasks view.tasks))


(defhandler drain-asks-on-sim
  ;; 本番の preStop の Program(drain_client.await-drained)が出す drain の頼み(AskDrain)に、sim の DrainWorker で答えるため。DrainWorker は
  ;; 本番の答え手(drain_requests.coordinator-calls)と同じ要求の形(drain-request)で頼み、sim の宿の今の世代の boot を載せる(頼みの
  ;; own-boot は読まない)。答えの形も本番と同じ {status body} / {error}。
  (AskDrain [name ttl-seconds own-boot]
    (<- answer dict (DrainWorker name ttl-seconds))
    (resume answer)))


(defk prestop-drain [name]
  {:pre [(: name str)] :post [(: % None)] :tags {:context "doeff-cluster" :role "program"}}
  "本番の preStop(drain_main の await-drained)と同じ待ちをするため: drain を DRAIN-INTERVAL-SECONDS ごとに頼み直し、coordinator が
   drained と答えるか、上限 DRAIN-DEADLINE-SECONDS に達するまで待つ。待つ Program は本番の await-drained そのもので、drain の頼みにだけ
   sim の DrainWorker で答える(drain-asks-on-sim)。本番と同じく、上限で諦めても・断られても止めへ進む(待った秒は入れ替えの記録の
   at-ms に出る)。#3669 の前は置かれた切り離した task だけを待ち、coordinator の drained を読まなかった — 移す先の無い job の drain が
   上限まで待つ形が模擬に出なかった。"
  (<- (with-handlers [drain-asks-on-sim] (await-drained name DRAIN-DEADLINE-SECONDS DRAIN-INTERVAL-SECONDS)))
  None)


(defk recreate-worker [deployed drain declared]
  {:pre [(: deployed DeployedEnv) (: drain Callable) (: declared (get frozenset str))] :post [(: % UpgradeStart)]
   :tags {:context "doeff-cluster" :role "program"}}
  "worker の Deployment 1 つを本番の Recreate と同じ順で作り直すため: drain(渡された preStop の代わり)→ 止まる瞬間の記録 → 止め →
   値の差し替え(env から launch_rules の表で読んだ値)→ 新しい世代。declared = 当てる宣言の worker の名(記録の名簿の宣言の内 / 外)。
   答え = 入れ替えを始めた瞬間の記録。"
  (<- launch WorkerLaunch (worker-launch-of-env deployed.env))
  (<- (drain launch.name))
  (<- start UpgradeStart (roster-snapshot UpgradeKind.WORKER launch.name launch.doeff-commit declared))
  (<- (StopWorker launch.name))
  (<- current SimWorker (WorkerOf launch.name))
  (<- replaced bool (ReplaceWorker launch.name (replace current :provides (frozenset launch.provides) :exclusive (frozenset launch.exclusive)
                                                        :capacity launch.capacity :task-reserve launch.task-reserve
                                                        :doeff-commit launch.doeff-commit)))
  (when (not replaced)
    (raise (RuntimeError (.format "worker {} を差し替えられない(止まっていない・名が違う)" launch.name))))
  (<- (StartWorker launch.name))
  start)


(defk recreate-coordinator [deployed seconds declared]
  {:pre [(: deployed DeployedEnv) (: seconds float) (: declared (get frozenset str))] :post [(: % UpgradeStart)]
   :tags {:context "doeff-cluster" :role "program"}}
  "coordinator の Deployment を作り直すため: 止まる瞬間の記録 → Deployment の env を模擬の Pod の環境にする(次の一生から効く —
   作り直した coordinator は入口の読みでその env の WORKER_DOEFF_COMMIT を申告する・#3772)→ seconds 秒止めて同じ保存先から作り直す。
   declared = 当てる宣言の worker の名。答え = 入れ替えを始めた瞬間の記録。"
  (<- launch CoordinatorLaunch (coordinator-launch-of-env deployed.env))
  (<- start UpgradeStart (roster-snapshot UpgradeKind.COORDINATOR "coordinator" launch.doeff-commit declared))
  (<- (ReplaceCoordinatorEnviron deployed.env))
  (<- (StopCoordinator seconds))
  start)


(defk reconcile-manifests [paths applied drain coordinator-seconds]
  {:pre [(: paths (get tuple #(str ...))) (: applied (get tuple #(DeployedEnv ...))) (: drain Callable) (: coordinator-seconds float)]
   :post [(: % FluxPass)] :tags {:context "doeff-cluster" :role "program"}}
  "宣言の置き場の manifest を 1 回当てるため: 前に当てた物(applied)と違う Deployment を全部同時に作り直す(本物の Flux と同じ)。
   drain = worker を止める前の待ち(本番の preStop の代わり — 正しい形は prestop-drain)・coordinator-seconds = coordinator の止まりの秒。"
  (<- desired (get tuple #(DeployedEnv ...)) (manifest-state paths))
  (<- declared (get frozenset str) (declared-workers desired))
  (val changed (tuple (gfor d desired :if (not-in d applied) d)))
  (var spawned #())
  (for [d changed]
    (<- task Task (Spawn (if (= d.role WORKER-ROLE)
                             (recreate-worker d drain declared)
                             (recreate-coordinator d coordinator-seconds declared))))
    (:= spawned (+ spawned #(task))))
  (<- starts list (Gather #* spawned))
  (FluxPass :applied desired :starts (tuple (sorted starts :key (fn [s] s.at-ms)))))


(defeffect UpgradeStartsSeen
  "検の effect: flux-declarations が当てた入れ替えの瞬間の記録の全部(始めた順)— 版上げの Program の後に条 V1〜V4 を判じるため。"
  {:answer (get tuple #(UpgradeStart ...))
   :tags {:context "doeff-cluster" :role "intent"}})


(defeffect BootRootsAtStartsSeen
  "テスト用の effect: flux-declarations が当てた入れ替えの瞬間ごとの保存先のスナップショットの全部(始めた順)— 版上げの Program の後に条 V5 を判定するため。"
  {:answer (get tuple #(BootRootsAtStart ...))
   :tags {:context "doeff-cluster" :role "intent"}})


(defrecord BootRoot
  "模擬の保存先に準備済み(完成のマークつき)で在る自己起動の root 1 つ: target = 保存先の持ち主(worker の名か \"coordinator\")・
   doeff-commit = root の版。"
  {:tags {:context "doeff-cluster" :role "type"}}
  (#^ str target)
  (#^ str doeff-commit))


(defk worker-running-roots [name]
  {:pre [(: name str)] :post [(: % (get tuple #(BootRoot ...)))] :tags {:context "doeff-cluster" :role "program"}}
  "worker name が今 動いている版の自己起動の root を返すため(模擬の世界の今の世代の版 — その版で起動したので、root は保存先に在る)。"
  (<- now SimWorker (WorkerOf name))
  #((BootRoot :target name :doeff-commit now.doeff-commit)))


(defk coordinator-running-roots [applied]
  {:pre [(: applied (get tuple #(DeployedEnv ...)))] :post [(: % (get tuple #(BootRoot ...)))] :tags {:context "doeff-cluster" :role "program"}}
  "coordinator が今 動いている版の自己起動の root を返すため — 模擬の coordinator は版を持たないので、当たっている宣言(applied)の
   coordinator の版を動いている版とみなす(その版で起動したので、root は保存先に在る)。版の行を持たない宣言(自己起動でない coordinator)は
   数えない — 動いている版の root が無いので、上げる前の版の root も無い。"
  (<- names (get frozenset str) (coordinator-launch-names))
  (var found #())
  (for [deployed applied]
    (when (and (= deployed.role COORDINATOR-ROLE) (.issubset names (frozenset (gfor line deployed.env line.name))))
      (<- launch CoordinatorLaunch (coordinator-launch-of-env deployed.env))
      (<- target str (launch-target launch))
      (:= found (+ found #((BootRoot :target target :doeff-commit launch.doeff-commit))))))
  found)


(defk running-roots-of [launch applied]
  {:pre [(: launch (| WorkerLaunch CoordinatorLaunch)) (: applied (get tuple #(DeployedEnv ...)))]
   :post [(: % (get tuple #(BootRoot ...)))] :tags {:context "doeff-cluster" :role "program"}}
  "入れ替え先の値 launch が指す対象が、今 動いている版の自己起動の root を返すため(保存先に在る root のうち、準備しなくても在る物 —
   上げる前の版の root が在るかもここから読む)。読むのは指定された 1 つだけ(ほかの Deployment の env は読まない)。"
  (<- found (get tuple #(BootRoot ...))
      (match launch
        (WorkerLaunch :name name) (worker-running-roots name)
        (CoordinatorLaunch) (coordinator-running-roots applied)))
  found)


(defk running-roots-at [start applied]
  {:pre [(: start UpgradeStart) (: applied (get tuple #(DeployedEnv ...)))] :post [(: % (get tuple #(BootRoot ...)))]
   :tags {:context "doeff-cluster" :role "program"}}
  "入れ替えを始めた瞬間に、入れ替える対象が動いていた版の自己起動の root を返すため: worker はその瞬間の名簿のコピーにある版(版を読めた時)・
   coordinator は当てる前の宣言(applied)の版。"
  (<- found (get tuple #(BootRoot ...))
      (match start.kind
        UpgradeKind.WORKER (roster-roots start)
        UpgradeKind.COORDINATOR (coordinator-running-roots applied)))
  found)


(defk roster-roots [start]
  {:pre [(: start UpgradeStart)] :post [(: % (get tuple #(BootRoot ...)))] :tags {:context "doeff-cluster" :role "judgment"}}
  "worker の入れ替えを始めた瞬間の名簿のコピーから、その worker が動いていた版の自己起動の root を引くため(版を読めた時だけ)。"
  (tuple (gfor entry start.roster
               :if (and (= entry.worker start.target) (is-not entry.doeff-commit None))
               (BootRoot :target start.target :doeff-commit entry.doeff-commit))))


(defk roots-with [roots more]
  {:pre [(: roots (get tuple #(BootRoot ...))) (: more (get tuple #(BootRoot ...)))] :post [(: % (get tuple #(BootRoot ...)))]
   :tags {:context "doeff-cluster" :role "judgment"}}
  "模擬の保存先の root の列に more を足すため(同じ root は重ねない)。足すだけで消さない — 上げる前の版の root は戻し先として残る。"
  (+ roots (tuple (gfor root more :if (not-in root roots) root))))


(defk places-at [starts roots applied]
  {:pre [(: starts (get tuple #(UpgradeStart ...))) (: roots (get tuple #(BootRoot ...))) (: applied (get tuple #(DeployedEnv ...)))]
   :post [(: % (get tuple #(BootRootsAtStart ...)))] :tags {:context "doeff-cluster" :role "program"}}
  "入れ替えを始めた瞬間の記録ごとに、入れ替える対象の保存先のスナップショット(その瞬間に準備済みで在った root の版)を作るため(条 V5 の入力)。
   在る root = その瞬間に動いていた版の物と、先に準備した物(roots — 当てている間に保存先へ足す手順は無いので、当てる直前の物と同じ)。
   applied = 当てる前の宣言。"
  (var seen #())
  (for [start starts]
    (<- running (get tuple #(BootRoot ...)) (running-roots-at start applied))
    (<- held (get tuple #(BootRoot ...)) (roots-with running roots))
    (:= seen (+ seen #((BootRootsAtStart :start start
                                         :prepared (tuple (gfor root held :if (= root.target start.target) root.doeff-commit)))))))
  seen)


(defhandler flux-declarations [#^ tuple paths #^ Callable drain #^ float coordinator-seconds #^ (get tuple #(DeployedEnv ...)) initial]
  ;; 引数に残す理由: 置き場の path・drain(preStop の代わり)・coordinator の止まりの秒・初めの当てた物は筋書きごとに違う値(設定ではなく
  ;; 模擬の世界そのもの)。
  ;; 版上げの Program の宣言の effect に sim で答えるため: 公開は何もしない(記憶の中の置き場がそのまま main)・当てるは模擬の Flux の
  ;; 1 回の当て(前に当てた物 applied は session に持つ — 初めは initial = 筋書きが Desire の前に読んだ manifest-state)・名簿の読みは
  ;; upgrade-state。当てた瞬間の記録は session に積み、UpgradeStartsSeen で返す。空の機体の起動の確かめは、模擬の世界では通る
  ;; (落ちる版の筋書きは refused-clean-boots を内側に置く)。
  ;; 自己起動の root の準備(PrepareBootRoot)は、模擬の保存先に無ければ待たずに組み、在れば準備済みと答える(断られる筋書きは
  ;; refused-boot-roots を内側に置く)。保存先に在る root = 指定された対象が今 動いている版の物と、session に覚えた物(roots — 先に準備した
  ;; 物と、準備の時に動いていた版の物。足すだけで消さない)。当てた瞬間の保存先のスナップショットは session に積み、BootRootsAtStartsSeen で返す。
  ;; 名簿の問い合わせに答える coordinator の版は、答えた coordinator が GET /state で申告する版(coordinator-view — #3772)。静かな時間帯の
  ;; 待ちには、すぐ「静か」と答える。worker の当てる直前の drain には、すぐ「中の仕事は 0」と答え、その drain を外す頼みには何もせずに答える(頭の註)。
  (session var applied initial)
  (session var starts #())
  (session var roots #())
  (session var places #())
  (AwaitQuietWindow [target timeout-seconds]
    (resume (QuietWindowOpened :target target)))
  (AwaitWorkerDrained [launch timeout-seconds]
    (resume (WorkerDrained :target launch.name)))
  (ReleaseWorkerDrain [launch]
    (resume None))
  (ConfirmCleanBoot [launch]
    (<- target str (launch-target launch))
    (resume (CleanBootPassed :target target)))
  (PrepareBootRoot [launch]
    (<- target str (launch-target launch))
    (<- running (get tuple #(BootRoot ...)) (running-roots-of launch applied))
    (<- held (get tuple #(BootRoot ...)) (roots-with running roots))
    (<- answer (| BootRootAlreadyPrepared BootRootBuilt) (boot-root-answer launch target held running))
    (<- built (get tuple #(BootRoot ...)) (roots-with held #((BootRoot :target target :doeff-commit launch.doeff-commit))))
    (:= roots built)
    (resume answer))
  (PublishDeclarations []
    (resume None))
  (ApplyDeclarations []
    (<- pass FluxPass (reconcile-manifests paths applied drain coordinator-seconds))
    (<- seen (get tuple #(BootRootsAtStart ...)) (places-at pass.starts roots applied))
    (:= places (+ places seen))
    (:= applied pass.applied)
    (:= starts (+ starts pass.starts))
    (resume None))
  (ReadUpgradeState []
    (<- declared (get frozenset str) (declared-workers applied))
    (<- state (upgrade-state declared))
    (resume state))
  (UpgradeStartsSeen []
    (resume starts))
  (BootRootsAtStartsSeen []
    (resume places)))


(defk boot-root-answer [launch target held running]
  {:pre [(: launch (| WorkerLaunch CoordinatorLaunch)) (: target str) (: held (get tuple #(BootRoot ...)))
         (: running (get tuple #(BootRoot ...)))]
   :post [(: % (| BootRootAlreadyPrepared BootRootBuilt))] :tags {:context "doeff-cluster" :role "judgment"}}
  "模擬の保存先(held — 準備の前に指定された対象の保存先に在る root)から、入れ替え先の版の root の準備の答えを作るため: 在れば準備済み・
   無ければ組んだ(SIM-BUILD-SECONDS)。上げる前の版の root が在るか = その対象が今 動いている版を持つか(running — 動いている版の
   root は保存先に在る)。"
  (val kept (bool running))
  (if (in (BootRoot :target target :doeff-commit launch.doeff-commit) held)
      (BootRootAlreadyPrepared :target target :previous-root-present kept)
      (BootRootBuilt :target target :seconds SIM-BUILD-SECONDS :previous-root-present kept)))


(defk launch-target [launch]
  {:pre [(: launch (| WorkerLaunch CoordinatorLaunch))] :post [(: % str)] :tags {:context "doeff-cluster" :role "judgment"}}
  "入れ替え先の値が名指す物の名を、確かめの答えと止まりの訳に載せるため: worker なら名・coordinator なら \"coordinator\"。"
  (match launch
    (WorkerLaunch :name name) name
    (CoordinatorLaunch) "coordinator"))


(defhandler refused-clean-boots [#^ frozenset targets]
  ;; 引数に残す理由: どの入れ替え先の空の起動が落ちるかは筋書きごとに違う値(模擬の世界そのもの)。
  ;; 空の機体の起動が落ちる版の筋書きのため(2026-10-05 07:1x の形 — 起動の時に読む物が壊れていて、空の Pod が起動で落ちる): targets に
  ;; 名の在る入れ替え先の確かめだけを断る。ほかの確かめは外側(flux-declarations)へ渡す。
  (ConfirmCleanBoot [launch]
    :when (in (if (isinstance launch WorkerLaunch) launch.name "coordinator") targets)
    (resume (CleanBootRefused :target (if (isinstance launch WorkerLaunch) launch.name "coordinator")
                              :reason "筋書き: 空の機体の起動が、起動の時に読む物で落ちる"))))


(defhandler stale-coordinator-answers [#^ int reads]
  ;; 引数に残す理由: 止めの合図の後に古い coordinator が何回答えるかは筋書きごとに違う値(模擬の世界そのもの)。
  ;; coordinator を作り直す止めの合図の後も、古い process がしばらく GET /state に答える筋書きのため(本物の Recreate で、古い Pod が止めの
  ;; 合図を受けて退くまでの間 — #3772): 止めの合図(StopCoordinator)の後の GET /state の読み reads 回に、合図の前に最後に読めた答え(古い
  ;; coordinator が申告する版 coordinatorCommit を含む JSON のまま)を返す。flux-declarations の外側に置く(模擬の Flux の読みと止めは
  ;; flux-declarations の節から外へ出るので、ここで受ける)。模擬の世界の coordinator は止めの合図の後の最初の読みまでに退くので、
  ;; この形はこの handler を置いた時だけ現れる。
  (session var last None)
  (session var stale 0)
  (StopCoordinator [seconds]
    (<- (StopCoordinator seconds))
    (:= stale reads)
    (resume None))
  (ReadCoordinator [path]
    :when (= path "/state")
    (if (and (> stale 0) (isinstance last dict))
        (do (:= stale (- stale 1))
            (resume last))
        (do (<- answer dict (ReadCoordinator path))
            (:= last answer)
            (resume answer)))))


(defhandler refused-boot-roots [#^ (get frozenset str) targets #^ BootRootRefusal reason]
  ;; 引数に残す理由: どの入れ替え先の root の準備が、どの理由で断られるかは筋書きごとに違う値(模擬の世界そのもの)。
  ;; 自己起動の root を準備できない筋書きのため(入れ替え先の版の起動の script が準備の役を知らない・準備が落ちる など): targets に名の
  ;; 在る入れ替え先の準備だけを reason で断る(模擬の保存先には何も足さない)。ほかの準備は外側(flux-declarations)へ渡す。
  (PrepareBootRoot [launch]
    :when (in (if (isinstance launch WorkerLaunch) launch.name "coordinator") targets)
    (<- target str (launch-target launch))
    (resume (BootRootRefused :target target :reason reason))))
