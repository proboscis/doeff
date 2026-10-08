;; 版上げの Program(shared/core/upgrade_program.hy — #3366 の単位 3)の検。sim-cluster の上で、宣言の effect に flux-declarations(公開 =
;; 何もしない・当てる = 模擬の Flux・root の準備 = 模擬の保存先)が答え、Program が条 V1〜V5 を自分で守る事(入れ替えの瞬間の記録と
;; 保存先のスナップショットで違反 0)・実行中の task の終わりを待つ事・待ちの上限を越えると対象を明示して落ちる事・入れ替えの前の手順(空の機体の確認 →
;; root の準備)が断られたら何も書かずに対象を明示して止まる事を確かめる。
;; coordinator だけを上げる入口(upgrade-coordinator — #3772)は、名簿と task を読む effect に台本で答える世界で、確かめた版の組み合わせ
;; (VerifiedVersions)・宣言の外の worker・戻し先の root・静かな時間帯・当てた直後に古い coordinator が答える形を確かめる。
;;
;; DesireWorker / DesireCoordinator に答えるのは、テストの道具 desire-by-manifest(tests/flux_fixtures.hy — 値から manifest を launch_rules の
;; 変換で作り直して保存先へ書く)— doeff は配備する側の repo の本番の handler を import できないため。本番の handler で一周するテストは
;; 配備する側の repo に置く(その repo の一周の検を、この Program に替える)。
(require doeff-hy.macros [deftest defk defhandler defeffect <- val var])
(require doeff-hy.record [defrecord])
(val MODULE-TAGS {:context "doeff-cluster-test" :role "test"})
(import doeff_events [MemoryBroker])
(import collections.abc [Callable])
(import dataclasses [dataclass replace])
(import pytest)
(import doeff [with-handlers Program])
(import doeff_core_effects.handlers [state])
(import doeff_time [Delay SimClock sim-time-handler])
(import doeff_cluster.shared.core.clock [now-epoch-ms])
(import doeff_cluster.shared.core.detached_rules [submit-detached-task])
(import doeff_cluster.shared.intent.detached_model [AwaitDetached DetachedSucceeded])
(import doeff_cluster.shared.intent.launch_model [WorkerLaunch CoordinatorLaunch DesireWorker DesireCoordinator])
(import doeff_cluster.shared.intent.upgrade_model [UpgradeLimits UpgradeStalled UpgradeState UpgradeStart RosterEntry PendingTask PendingPhase
                                                   WorkerDeclaration VerifiedVersions ReadUpgradeState PublishDeclarations
                                                   ApplyDeclarations ConfirmCleanBoot CleanBootPassed CleanBootRefused UpgradeRefused
                                                   PrepareBootRoot BootRootAlreadyPrepared BootRootBuilt BootRootRefused BootRootRefusal
                                                   AwaitQuietWindow QuietWindowOpened QuietWindowMissed UnverifiedWorkers
                                                   RollbackRootMissing QueuedTasksRemain RefusalPoint CoordinatorUpgraded ClusterUpgraded
                                                   AwaitWorkerDrained WorkerDrained WorkerDrainMissed ReleaseWorkerDrain])
(import doeff_cluster.shared.intent.detached_model [AwaitRunnersChange RunnersChange])
(import doeff_cluster.shared.core.upgrade_program [upgrade-cluster upgrade-coordinator upgrade-workers])
(import doeff_cluster.sim.local [sim-cluster SimWorker SimOutside WorkerOf DrainWorker ReadCoordinator CoordinatorRuns StopCoordinator
                                 ReplaceCoordinatorEnviron])
(import doeff_core_effects.process_effects [EnvEntry])
(import doeff_cluster.shared.core.launch_rules [coordinator-launch-env coordinator-commit-env-name])
(import doeff_cluster.shared.protocol.coordinator_reads [coordinator-commit-of-state])
(import doeff_cluster.sim.flux [FluxPass manifest-state prestop-drain flux-declarations refused-clean-boots refused-boot-roots
                                stale-coordinator-answers launch-target UpgradeStartsSeen BootRootsAtStartsSeen])
(import tests.flux_fixtures [OLD NEW NO-JOBS PATHS ON-X A B COORDINATOR-SECONDS SAME-VERSION program-breaches-of flux-outside
                             desire-by-manifest])
(import tests.detached_rig [slow-add])

;; 宣言の値(本番の DECLARED-WORKERS に当たる)— 版は NEW へ。
(val TARGET-A (WorkerLaunch :name "a" :provides #("x-tool" "host-a") :exclusive #() :capacity 1 :task-reserve 0 :doeff-commit NEW))
(val TARGET-B (WorkerLaunch :name "b" :provides #("y-tool" "host-b") :exclusive #() :capacity 1 :task-reserve 0 :doeff-commit NEW))
(val TARGET-COORDINATOR (CoordinatorLaunch :doeff-commit NEW))
(val LIMITS (UpgradeLimits :drain-seconds 120.0 :return-seconds 60.0 :queue-seconds 120.0 :quiet-seconds 60.0))
;; 本番の Pod の猶予(terminationGracePeriodSeconds — agent-worker は 120 秒)が task より短い worker の代わりの猶予の秒。
(val GRACE-SECONDS 5.0)
(val IN WorkerDeclaration.DECLARED)
(val OUT WorkerDeclaration.UNDECLARED)


(defk drain-within-grace [name]
  {:pre [(: name str)] :post [(: % None)] :tags {:context "doeff-cluster-test" :role "program"}}
  "本番の Pod の猶予(terminationGracePeriodSeconds)が task より短い worker の drain の代わり: drain を頼み、置かれた切り離した task を
   猶予の秒(GRACE-SECONDS)だけ待ってから止めへ進む(猶予を越えた task は止めで失われる)。"
  (<- (DrainWorker name))
  (<- state dict (ReadCoordinator "/state"))
  (for [t (get state "tasks")]
    (when (and (= (.get t "worker") name) (in (get t "phase") #("assigned" "preparing" "running")) (.get t "detached"))
      (<- (AwaitDetached (get t "key") :timeout-seconds GRACE-SECONDS))))
  None)


(defk upgrade-and-judge [limits drain]
  {:pre [(: limits UpgradeLimits) (: drain Callable)] :post [(: % tuple)] :tags {:context "doeff-cluster-test" :role "program"}}
  "筋書き: 宣言を読み、Program で a・b・coordinator を NEW へ上げ、入れ替えの瞬間の記録と保存先のスナップショットを条 V1〜V5 で判定する。
   答え = #(違反した条の名 a の版 b の版 記録)。"
  (<- (Delay 3.0))
  (<- applied tuple (manifest-state PATHS))
  (<- run tuple (with-handlers [(flux-declarations PATHS drain COORDINATOR-SECONDS applied) desire-by-manifest]
                  (upgrade-then-starts limits)))
  (<- rules tuple (program-breaches-of (get run 0) (get run 1) SAME-VERSION))
  (<- a SimWorker (WorkerOf "a"))
  (<- b SimWorker (WorkerOf "b"))
  #(rules a.doeff-commit b.doeff-commit (get run 0)))


(defk upgrade-then-starts [limits]
  {:pre [(: limits UpgradeLimits)] :post [(: % tuple)] :tags {:context "doeff-cluster-test" :role "program"}}
  "Program を走らせてから、模擬の Flux が当てた入れ替えの瞬間の記録と、同じ瞬間の保存先のスナップショットを読む。答え = #(記録 保存先のスナップショット)。"
  (<- (upgrade-cluster #(TARGET-A TARGET-B) TARGET-COORDINATOR SAME-VERSION limits))
  (<- starts tuple (UpgradeStartsSeen))
  (<- places tuple (BootRootsAtStartsSeen))
  #(starts places))


(deftest test-the-upgrade-program-keeps-v1-to-v5
  ;; V5(#3725): どの入れ替えも、保存先に入れ替え先の版の root を先に準備してから始まる — Program から準備の手順を外すと、a・b・
  ;; coordinator の入れ替えの瞬間の保存先に NEW の root が無く、V5 が赤(直す前の Program で見た)。
  (<- outside SimOutside (flux-outside))
  (<- seen tuple (sim-cluster :notice-broker (MemoryBroker) NO-JOBS (upgrade-and-judge LIMITS prestop-drain) :workers #(A B) :outside outside))
  (val rules (get seen 0))
  (val a-commit (get seen 1))
  (val b-commit (get seen 2))
  (val starts (get seen 3))
  (assert (= rules #()) rules)
  (assert (= #(a-commit b-commit) #(NEW NEW)) seen)
  ;; 1 つずつ: worker a・b・coordinator の順に 1 つずつ入れ替えた。
  (assert (= (tuple (gfor s starts s.target)) #("a" "b" "coordinator")) starts))


;; --- coordinator が自分の版を GET /state で申告する(#3772)----------------------------------------------------------------------------
;; 本番の coordinator は起動の時に 1 度、環境変数(boot.sh が渡す WORKER_DOEFF_COMMIT)から自分の走っている doeff の版を読み、GET /state
;; の欄 coordinatorCommit に載せる。模擬の coordinator の Pod は本番の入口と同じ読み(with-running-commit)を、模擬の Pod の環境で通る。

(defk restart-coordinator-on [environ]
  {:pre [(: environ (get tuple #(EnvEntry ...)))] :post [(: % None)] :tags {:context "doeff-cluster-test" :role "program"}}
  "筋書きの道具: coordinator の Pod の環境を environ に替えて作り直し、新しい一生が受け付けるまで待つため(本番の Deployment の env を
   変えて Pod を作り直す形 — 止めの合図の後 1 秒止まって作り直す)。"
  (<- (ReplaceCoordinatorEnviron environ))
  (<- (StopCoordinator 1.0))
  (<- (Delay 5.0))
  None)


(defk state-after-restart-on [commit]
  {:pre [(: commit str)] :post [(: % dict)] :tags {:context "doeff-cluster-test" :role "program"}}
  "筋書き: coordinator を版 commit の Deployment の env(本番が宣言に書くのと同じ launch_rules の行)で作り直し、GET /state を読む。
   答え = /state の答え(JSON の object)。"
  (<- (Delay 3.0))
  (<- environ (get tuple #(EnvEntry ...)) (coordinator-launch-env (CoordinatorLaunch :doeff-commit commit)))
  (<- (restart-coordinator-on environ))
  (<- state dict (ReadCoordinator "/state"))
  state)


(deftest test-a-coordinator-started-on-a-version-names-it-in-the-state-answer
  ;; 失敗ケース a(#3772): coordinator を版 NEW の env で起こすと、GET /state の答えの欄 coordinatorCommit に NEW が出て、共有の読み
  ;; (coordinator-commit-of-state — 配備する側の名簿の読みも通る)が NEW と読む。欄を書かない coordinator では欄が無く赤。
  (<- state dict (sim-cluster :notice-broker (MemoryBroker) NO-JOBS (state-after-restart-on NEW) :workers #(A B)))
  (assert (= (.get state "coordinatorCommit") NEW) (sorted state))
  (<- read (| str None) (coordinator-commit-of-state state))
  (assert (= read NEW) read))


(defk states-without-the-version []
  {:pre [] :post [(: % tuple)] :tags {:context "doeff-cluster-test" :role "program"}}
  "筋書き: 環境変数の無い coordinator(模擬の Pod の初め)と、同じ名の環境変数を空の値で渡して作り直した coordinator の GET /state を読む。
   答え = #(初めの答え 空の値の答え)。"
  (<- (Delay 3.0))
  (<- first dict (ReadCoordinator "/state"))
  (<- name str (coordinator-commit-env-name))
  (<- (restart-coordinator-on #((EnvEntry :name name :value ""))))
  (<- emptied dict (ReadCoordinator "/state"))
  #(first emptied))


(deftest test-a-coordinator-without-the-version-variable-writes-no-version-field
  ;; 失敗ケース b(#3772): 環境変数が無い・空の値の coordinator は、欄 coordinatorCommit を書かない(空の文字や null を版として書かない)。
  ;; 共有の読みはどちらも None と読む。読めない時に空の文字を書く形は赤。
  (<- seen tuple (sim-cluster :notice-broker (MemoryBroker) NO-JOBS (states-without-the-version) :workers #(A B)))
  (for [state seen]
    (assert (not-in "coordinatorCommit" state) (sorted state))
    (<- read (| str None) (coordinator-commit-of-state state))
    (assert (is read None) read)))


(deftest test-the-state-reader-takes-the-version-only-when-it-is-named
  ;; 共有の読み(#3772): 欄が文字で空でない時だけ版と読む — 欄が無い・空の文字・null・文字でない値は None(版を推さない)。
  (for [#(state expected) #(#({"coordinatorCommit" NEW} NEW) #({} None) #({"coordinatorCommit" ""} None)
                            #({"coordinatorCommit" None} None) #({"coordinatorCommit" 7} None))]
    (<- read (| str None) (coordinator-commit-of-state state))
    (assert (= read expected) #(state read))))


(defrecord CommitRead
  "Program が読んだ名簿 1 つ: commit = 答えた coordinator の版(UpgradeState の coordinator-commit)・after-apply = coordinator の宣言を
   当てた後の読みか。"
  (#^ (| str None) commit)
  (#^ bool after-apply))

(defeffect CommitReadsSeen
  "テスト用の effect: commit-recorder が覚えた名簿の読み(CommitRead の tuple・読んだ順)。"
  {:answer tuple :tags {:context "doeff-cluster-test" :role "intent"}})


(defhandler commit-recorder
  ;; テストの道具: Program が読んだ名簿ごとに、答えた coordinator の版と、coordinator の宣言を当てた後の読みかを覚えるため(答えは
  ;; 変えない)。
  (session var reads #())
  (session var swapping False)
  (session var applied False)
  (ReadUpgradeState []
    (<- state (ReadUpgradeState))
    (when (isinstance state UpgradeState)
      (:= reads (+ reads #((CommitRead :commit state.coordinator-commit :after-apply applied)))))
    (resume state))
  (DesireCoordinator [launch]
    (:= swapping True)
    (<- changes (DesireCoordinator launch))
    (resume changes))
  (ApplyDeclarations []
    (<- (ApplyDeclarations))
    (when swapping
      (:= applied True))
    (resume None))
  (CommitReadsSeen []
    (resume reads)))


(defk upgrade-then-coordinator-runs []
  {:pre [] :post [(: % tuple)] :tags {:context "doeff-cluster-test" :role "program"}}
  "筋書き: coordinator を版 OLD の env で起こし直してから、模擬の世界で Program に a・b・coordinator を NEW へ上げさせ、Program が答えを
   返した時刻・coordinator の Pod の一生の列・入れ替えの記録・Program が読んだ coordinator の版を読む。coordinator の止めの合図の後の
   GET /state の読み 2 回には、古い coordinator が版 OLD を申告したまま答える(stale-coordinator-answers — 模擬の Flux の外側)。
   答え = #(返した時刻 一生の列 記録 読んだ版)。"
  (<- (Delay 3.0))
  (<- environ (get tuple #(EnvEntry ...)) (coordinator-launch-env (CoordinatorLaunch :doeff-commit OLD)))
  (<- (restart-coordinator-on environ))
  (<- applied tuple (manifest-state PATHS))
  (<- run tuple (with-handlers [(stale-coordinator-answers 2) (flux-declarations PATHS prestop-drain COORDINATOR-SECONDS applied)
                                desire-by-manifest commit-recorder]
                  (returned-then-runs)))
  run)


(defk returned-then-runs []
  {:pre [] :post [(: % tuple)] :tags {:context "doeff-cluster-test" :role "program"}}
  "Program を走らせ、答えを返した時刻を取ってから、coordinator の Pod の一生の列・入れ替えの記録・読んだ版を読む。
   答え = #(時刻 一生の列 記録 読んだ版)。"
  (<- (upgrade-cluster #(TARGET-A TARGET-B) TARGET-COORDINATOR SAME-VERSION LIMITS))
  (<- returned int (now-epoch-ms))
  (<- runs tuple (CoordinatorRuns))
  (<- starts tuple (UpgradeStartsSeen))
  (<- reads tuple (CommitReadsSeen))
  #(returned runs starts reads))


(deftest test-the-program-returns-only-after-the-new-coordinator-answers-in-the-emulated-world
  ;; 失敗ケース c / 4 の模擬の世界の形(#3772): coordinator を当てた直後に、古い coordinator が欄 coordinatorCommit で版 OLD を申告して
  ;; 2 回答える(全部の worker は live)。Program はその欄の値で古い版と読み(当てた後の読みに OLD が在る)、終わりにしない。答えを返すのは、
  ;; 入れ替えを始めた後に起動した新しい coordinator が欄で NEW を申告した後。worker が live なら終わりとする形・宣言の版を答えた版と
  ;; みなす形は、新しい coordinator の起動より前に答えを返して赤。欄を書かない coordinator では NEW を読めず上限で止まり赤。
  (<- outside SimOutside (flux-outside))
  (<- seen tuple (sim-cluster :notice-broker (MemoryBroker) NO-JOBS (upgrade-then-coordinator-runs) :workers #(A B) :outside outside))
  (val returned (get seen 0))
  (val runs (get seen 1))
  (val swap (next (gfor s (get seen 2) :if (= s.target "coordinator") s)))
  (val after-apply (tuple (gfor r (get seen 3) :if r.after-apply r.commit)))
  (assert (in OLD after-apply) (get seen 3))
  (assert (= (get after-apply -1) NEW) (get seen 3))
  (val after (tuple (gfor r runs :if (> r.started-ms swap.at-ms) r)))
  (assert after #(swap runs))
  (assert (<= (. (get after 0) started-ms) returned) #(returned after)))


;; 模擬の世界で root の準備が断られる時の理由(どの理由でも同じ止まり方 — 理由ごとに明示されるかは、下の台本のテストが 5 つとも確かめる)。
(val SIM-REFUSAL BootRootRefusal.PREPARE-FAILED)


(defk upgrade-with-refused-boots [clean-boots boot-roots]
  {:pre [(: clean-boots frozenset) (: boot-roots frozenset)] :post [(: % tuple)] :tags {:context "doeff-cluster-test" :role "program"}}
  "筋書き: clean-boots に名の在る入れ替え先の空の起動が落ち(2026-10-05 07:1x の形 — 起動の時に読む物が壊れていて、空の Pod が起動で
   落ちる版)、boot-roots に名の在る入れ替え先の自己起動の root の準備が断られる(#3725)世界で Program を走らせる。
   答え = #(停止の例外 止まった後の宣言 入れ替えの記録 a の版 b の版 保存先のスナップショット)。"
  (<- (Delay 3.0))
  (<- applied tuple (manifest-state PATHS))
  (<- run tuple (with-handlers [(flux-declarations PATHS prestop-drain COORDINATOR-SECONDS applied) (refused-clean-boots clean-boots)
                                (refused-boot-roots boot-roots SIM-REFUSAL) desire-by-manifest]
                  (refusal-then-starts)))
  (<- after tuple (manifest-state PATHS))
  (<- a SimWorker (WorkerOf "a"))
  (<- b SimWorker (WorkerOf "b"))
  #((get run 0) #(applied after) (get run 1) a.doeff-commit b.doeff-commit (get run 2)))


(defk refusal-then-starts []
  {:pre [] :post [(: % tuple)] :tags {:context "doeff-cluster-test" :role "program"}}
  "Program を走らせて入れ替えの前の手順の拒否による停止(UpgradeRefused)を受け、模擬の Flux が当てた入れ替えの記録と保存先のスナップショットを読む。
   答え = #(停止 記録 保存先のスナップショット)。"
  (var refused None)
  (try
    (<- (upgrade-cluster #(TARGET-A TARGET-B) TARGET-COORDINATOR SAME-VERSION LIMITS))
    (except [e UpgradeRefused]
      (:= refused e)))
  (<- starts tuple (UpgradeStartsSeen))
  (<- places tuple (BootRootsAtStartsSeen))
  #(refused starts places))


(deftest test-a-worker-whose-clean-boot-fails-stops-the-upgrade-before-anything-is-written
  ;; 失敗ケース(Mac の調整役の条件 3・今朝の形): 最初の worker a の入れ替え先の空の起動が落ちる — Program は a を明示して止まり、宣言を
  ;; 書かず(保存先は始めと同じ)、何も入れ替えず、a・b は元の版のまま。
  (<- outside SimOutside (flux-outside))
  (<- seen tuple (sim-cluster :notice-broker (MemoryBroker) NO-JOBS (upgrade-with-refused-boots (frozenset ["a"]) (frozenset)) :workers #(A B) :outside outside))
  (val refused (get seen 0))
  (assert (isinstance refused UpgradeRefused) seen)
  (assert (= refused.target "a") refused)
  (assert (isinstance refused.refusal CleanBootRefused) refused.refusal)
  (val manifests (get seen 1))
  (assert (= (get manifests 0) (get manifests 1)) seen)
  (assert (= (get seen 2) #()) seen)
  (assert (= #((get seen 3) (get seen 4)) #(OLD OLD)) seen))


(deftest test-a-coordinator-whose-clean-boot-fails-stops-after-the-workers-and-before-its-swap
  ;; 失敗ケース: coordinator の入れ替え先の空の起動が落ちる — worker a・b は入れ替わり(新しい版)、coordinator の宣言は書かれず
  ;; 入れ替えもしない(入れ替えの記録は a・b だけ)。停止は coordinator を明示する。
  (<- outside SimOutside (flux-outside))
  (<- seen tuple (sim-cluster :notice-broker (MemoryBroker) NO-JOBS (upgrade-with-refused-boots (frozenset ["coordinator"]) (frozenset)) :workers #(A B)
                              :outside outside))
  (val refused (get seen 0))
  (assert (isinstance refused UpgradeRefused) seen)
  (assert (= refused.target "coordinator") refused)
  (assert (= (tuple (gfor s (get seen 2) s.target)) #("a" "b")) seen)
  (assert (= #((get seen 3) (get seen 4)) #(NEW NEW)) seen))


(deftest test-a-worker-whose-boot-root-cannot-be-prepared-stops-the-upgrade-before-anything-is-written
  ;; 失敗ケース(#3725): 最初の worker a の入れ替え先の版の自己起動の root を、a の保存先に準備できない — Program は a と拒否の理由
  ;; (閉じた語)を明示して止まり、宣言を書かず(宣言の保存先は始めと同じ)、何も入れ替えず、a・b は元の版のまま。準備の手順を Desire の後に
  ;; 置くと、宣言が書かれて宣言の保存先が始めと違い赤(準備の手順の無い直す前の Program は、止まらずに全部入れ替えて赤)。
  (<- outside SimOutside (flux-outside))
  (<- seen tuple (sim-cluster :notice-broker (MemoryBroker) NO-JOBS (upgrade-with-refused-boots (frozenset) (frozenset ["a"])) :workers #(A B) :outside outside))
  (val refused (get seen 0))
  (assert (isinstance refused UpgradeRefused) seen)
  (assert (= refused.target "a") refused)
  (assert (= refused.refusal (BootRootRefused :target "a" :reason SIM-REFUSAL)) refused.refusal)
  (assert (in "prepare-failed" (str refused)) (str refused))
  (val manifests (get seen 1))
  (assert (= (get manifests 0) (get manifests 1)) seen)
  (assert (= (get seen 2) #()) seen)
  (assert (= #((get seen 3) (get seen 4)) #(OLD OLD)) seen))


(deftest test-a-coordinator-whose-boot-root-cannot-be-prepared-stops-after-the-workers-and-before-its-swap
  ;; 失敗ケース(#3725): coordinator の入れ替え先の版の root を準備できない — worker a・b は入れ替わり(新しい版・どちらも root を先に
  ;; 準備してから = V5 の違反 0)、coordinator の宣言は書かれず入れ替えもしない。停止は coordinator と理由を明示する。
  (<- outside SimOutside (flux-outside))
  (<- seen tuple (sim-cluster :notice-broker (MemoryBroker) NO-JOBS (upgrade-with-refused-boots (frozenset) (frozenset ["coordinator"])) :workers #(A B)
                              :outside outside))
  (val refused (get seen 0))
  (assert (isinstance refused UpgradeRefused) seen)
  (assert (= refused.target "coordinator") refused)
  (assert (= refused.refusal (BootRootRefused :target "coordinator" :reason SIM-REFUSAL)) refused.refusal)
  (assert (= (tuple (gfor s (get seen 2) s.target)) #("a" "b")) seen)
  (assert (= #((get seen 3) (get seen 4)) #(NEW NEW)) seen)
  (<- rules tuple (program-breaches-of (get seen 2) (get seen 5) SAME-VERSION))
  (assert (= rules #()) rules))


(defk upgrade-under-a-running-task [limits drain]
  {:pre [(: limits UpgradeLimits) (: drain Callable)] :post [(: % tuple)] :tags {:context "doeff-cluster-test" :role "program"}}
  "筋書き: a に 20 秒の task を出し、走り出した後で Program を始める。答え = #(破りの条の名 task の結末 a の入れ替えの時刻 task が終わった時刻)。"
  (<- (Delay 3.0))
  (<- (submit-detached-task (slow-add 20.0 4) :key "k-run" :needs ON-X :lease-seconds 60.0))
  (<- (Delay 3.0))
  (<- seen tuple (upgrade-and-judge limits drain))
  (<- outcome (AwaitDetached "k-run"))
  #((get seen 0) outcome (get seen 3)))


(deftest test-the-upgrade-program-waits-for-a-running-task-before-swapping-its-worker
  ;; 条 V2 を Program が守る: a の上で走る task が終わるのを待ってから a を入れ替える — task は走り切り、破りは無い。worker の drain は
  ;; 猶予 5 秒で止めへ進む形(drain-within-grace — 本番の Pod の猶予が task より短い時)なので、task を救うのは Program の待ちだけ。
  (<- outside SimOutside (flux-outside))
  (<- seen tuple (sim-cluster :notice-broker (MemoryBroker) NO-JOBS (upgrade-under-a-running-task LIMITS drain-within-grace) :workers #(A B) :outside outside))
  (assert (= (get seen 0) #()) seen)
  (assert (= (get seen 1) (DetachedSucceeded 104)) seen)
  (val a-start (next (gfor s (get seen 2) :if (= s.target "a") s)))
  (assert (not (any (gfor t a-start.tasks (= t.worker "a")))) a-start))


(deftest test-the-upgrade-program-stops-by-name-when-a-wait-passes-its-limit
  ;; 失敗ケース(cisco-c8 の条件 1): a の上の task が drain の上限(5 秒)のうちに終わらない — Program は黙って待ち続けず、どの待ちで
  ;; 止まったかを名指しで落ちる。
  (<- outside SimOutside (flux-outside))
  (with [caught (pytest.raises UpgradeStalled)]
    (<- _ (sim-cluster :notice-broker (MemoryBroker) NO-JOBS (upgrade-under-a-running-task (replace LIMITS :drain-seconds 5.0) drain-within-grace) :workers #(A B) :outside outside)))
  (assert (= caught.value.step "worker a に置かれた task が終わる") caught.value.step)
  (assert (= caught.value.limit-seconds 5.0)))


;; --- 名簿と task を読む effect に台本で答える形(時刻の競りの無い形)--------------------------------------------------------------
;; sim の上では、待ち行列が空でない瞬間に Program が coordinator の手前へ着く順を、時刻の競り無しには作れない。名簿と task を読む effect
;; (ReadUpgradeState — 外の世界の境界)だけを台本の handler で答え、Program が待ち行列の空を読むまで DesireCoordinator を出さない事などを見る。

(defrecord ScriptLog
  "台本の handler が覚えた事: reads = 名簿を読んだ回数・coordinator-at = DesireCoordinator を出した時に名簿を読んでいた回数(None = 出していない)・
   released-at = drain を外す頼み(ReleaseWorkerDrain)ごとの #(worker の名 その時に名簿を読んでいた回数)(受けた順)。"
  (#^ int reads)
  (#^ (| int None) coordinator-at)
  (#^ (get tuple #((get tuple #(str int)) ...)) released-at))

(defeffect ScriptLogSeen
  "テスト用の effect: 台本の handler が覚えた事(ScriptLog)。"
  {:answer ScriptLog :tags {:context "doeff-cluster-test" :role "intent"}})


(val ALL-OLD (UpgradeState :roster #((RosterEntry :worker "a" :live True :doeff-commit OLD :declaration IN)
                                     (RosterEntry :worker "b" :live True :doeff-commit OLD :declaration IN))
                           :tasks #() :coordinator-commit OLD :known-tasks #()))
(val A-NEW (replace ALL-OLD :roster #((RosterEntry :worker "a" :live True :doeff-commit NEW :declaration IN)
                                      (RosterEntry :worker "b" :live True :doeff-commit OLD :declaration IN))))
(val ALL-NEW (replace ALL-OLD :roster #((RosterEntry :worker "a" :live True :doeff-commit NEW :declaration IN)
                                        (RosterEntry :worker "b" :live True :doeff-commit NEW :declaration IN))))
(val ALL-NEW-QUEUED (replace ALL-NEW :tasks #((PendingTask :task "t9" :phase PendingPhase.QUEUED :worker None)) :known-tasks #("t9")))
;; coordinator を NEW へ入れ替えた後の名簿(新しい coordinator が NEW で答える)。
(val ALL-NEW-SWAPPED (replace ALL-NEW :coordinator-commit NEW))
;; 台本(Program が読む順): a の drain → a の戻り → b の drain → b の戻り → 待ち行列(空でない 2 回 → 空)→ 条 V1 →
;; coordinator が NEW で答える → 宣言の内の worker が live → 待っていた task が在る。
(val SCRIPT #(ALL-OLD A-NEW A-NEW ALL-NEW ALL-NEW-QUEUED ALL-NEW-QUEUED ALL-NEW ALL-NEW ALL-NEW-SWAPPED))
;; 待ち行列が空と読める 7 番目の後、条 V1 を照らす 8 番目の読みの後に DesireCoordinator を出す。
(val V1-READ 8)


(defhandler scripted-upgrade [#^ tuple script]
  ;; 引数に残す理由: 台本はテストごとに違う値(設定ではなく外の世界そのもの)。
  ;; 名簿と task を読む effect に台本の順で答え(台本の最後は繰り返す)、版の変化の待ちは仮想の 1 秒の後に返し、宣言の effect は覚える
  ;; だけにするため(空の機体の確認は通し、root の準備は組んだと答え、静かな時間帯はすぐ来たと答え、drain を外す頼みは覚えて答える)。
  (session var reads 0)
  (session var coordinator-at None)
  (session var released-at #())
  (ReadUpgradeState []
    (val at (min reads (- (len script) 1)))
    (:= reads (+ reads 1))
    (resume (get script at)))
  (AwaitRunnersChange [after timeout-seconds]
    (<- (Delay 1.0))
    (resume (RunnersChange :revision (+ after 1) :changed True)))
  (DesireWorker [launch]
    (resume #()))
  (DesireCoordinator [launch]
    (:= coordinator-at reads)
    (resume #()))
  (ConfirmCleanBoot [launch]
    (<- target str (launch-target launch))
    (resume (CleanBootPassed :target target)))
  (PrepareBootRoot [launch]
    (<- target str (launch-target launch))
    (resume (BootRootBuilt :target target :seconds 0.0 :previous-root-present True)))
  (AwaitQuietWindow [target timeout-seconds]
    (resume (QuietWindowOpened :target target)))
  (AwaitWorkerDrained [launch timeout-seconds]
    (resume (WorkerDrained :target launch.name)))
  (ReleaseWorkerDrain [launch]
    (:= released-at (+ released-at #(#(launch.name reads))))
    (resume None))
  (PublishDeclarations []
    (resume None))
  (ApplyDeclarations []
    (resume None))
  (ScriptLogSeen []
    (resume (ScriptLog :reads reads :coordinator-at coordinator-at :released-at released-at))))


(defk scripted-run []
  {:pre [] :post [(: % ScriptLog)] :tags {:context "doeff-cluster-test" :role "program"}}
  "台本の世界で Program を 1 回走らせ、覚えた事を返す。"
  (<- (upgrade-cluster #(TARGET-A TARGET-B) TARGET-COORDINATOR SAME-VERSION LIMITS))
  (<- seen ScriptLog (ScriptLogSeen))
  seen)


(deftest test-the-upgrade-program-waits-for-an-empty-queue-before-the-coordinator
  ;; 条 V4 を Program が守る: 待ち行列に task が在る間(台本の 5・6 番目)は coordinator を入れ替えず、空と読んだ(7 番目)後の条 V1 の
  ;; 照らし(8 番目)の後に DesireCoordinator を出す。待ち行列の待ちを外すと 5 番目の後に出る(赤)。
  (<- seen ScriptLog (with-handlers [(state) (sim-time-handler :clock (SimClock)) (scripted-upgrade SCRIPT)] (scripted-run)))
  (assert (= seen.coordinator-at V1-READ) seen))


;; --- worker だけを上げる場合(upgrade-workers)と、戻らない worker の最後の状態(#3366)-------------------------------------------

;; 台本: a の drain → a の戻り → b の drain → b の戻り(coordinator の待ちは無い)。
(val WORKERS-ONLY-SCRIPT #(ALL-OLD A-NEW A-NEW ALL-NEW))


(defk workers-only-run []
  {:pre [] :post [(: % ScriptLog)] :tags {:context "doeff-cluster-test" :role "program"}}
  "台本の世界で worker だけを 1 回上げ、覚えた事を返す。"
  (<- (upgrade-workers #(TARGET-A TARGET-B) LIMITS))
  (<- seen ScriptLog (ScriptLogSeen))
  seen)


(deftest test-a-workers-only-upgrade-never-touches-the-coordinator
  ;; worker だけを上げる時は a・b を 1 つずつ入れ替え(名簿を 4 回読む = drain と戻りを 2 つ分)、coordinator に Desire を出さない。
  (<- seen ScriptLog (with-handlers [(state) (sim-time-handler :clock (SimClock)) (scripted-upgrade WORKERS-ONLY-SCRIPT)] (workers-only-run)))
  (assert (is seen.coordinator-at None) seen)
  (assert (= seen.reads (len WORKERS-ONLY-SCRIPT)) seen))


;; a を入れ替えた後、a は live だが版を読めない(読み手が理由を書いた — 新しい世代が準備完了でない)まま戻らない。
(val A-STUCK-REASON "新しい世代が準備完了でない(準備完了の判定 = その上の job が答える事)")
(val A-STUCK (replace ALL-OLD :roster #((RosterEntry :worker "a" :live True :doeff-commit None :declaration IN :unread-reason A-STUCK-REASON)
                                        (RosterEntry :worker "b" :live True :doeff-commit OLD :declaration IN))))


(deftest test-a-stalled-return-names-the-last-reading-of-the-worker
  ;; 失敗ケース(#3366 — 止まった時の文を分ける): a の戻りの待ちが上限で止まった時、文は待ちの名だけでなく、最後に読んだ a の行
  ;; (live=True・版を読めない理由)を載せる — 「worker は起動したが、上の job が答えない」と分かる。observe の文を載せない形にすると赤。
  (with [caught (pytest.raises UpgradeStalled)]
    (<- _ (with-handlers [(state) (sim-time-handler :clock (SimClock)) (scripted-upgrade #(ALL-OLD A-STUCK))]
            (upgrade-workers #(TARGET-A) LIMITS))))
  (assert (= caught.value.step (.format "worker a が版 {} で live に戻る" NEW)) caught.value.step)
  (val text (str caught.value))
  (assert (in "live=True" text) text)
  (assert (in A-STUCK-REASON text) text)
  (assert (in A-STUCK-REASON caught.value.observed) caught.value.observed))


;; --- 入れ替えの前の root の準備(PrepareBootRoot — #3725): effect の順・拒否の後に出なくなる effect・答えの 3 つの枝 ---------------------------
;; Program が出した書き込みの effect を、出した順に覚える道具(step-recorder)を一番内側に置き、effect の順と「出なかった effect」を見る。

(defrecord SwapStep
  "Program が出した書き込みの effect 1 つ: name = effect の名・target = 何の入れ替えの effect か(公開と当ては対象を持たないので None)。"
  (#^ str name)
  (#^ (| str None) target))

(defrecord StepLog
  "step-recorder が覚えた事: steps = Program が出した書き込みの effect(出した順)・answers = root の準備の答え(受けた順)。"
  (#^ (get tuple #(SwapStep ...)) steps)
  (#^ (get tuple #((| BootRootAlreadyPrepared BootRootBuilt BootRootRefused) ...)) answers))

(defeffect StepLogSeen
  "テスト用の effect: step-recorder が覚えた事(StepLog)。"
  {:answer StepLog :tags {:context "doeff-cluster-test" :role "intent"}})


(defhandler step-recorder
  ;; テストの道具: 版上げの Program が出した書き込みの effect(空の機体の確認・root の準備・静かな時間帯の待ち・drain とその外し・Desire・公開・当て)を出した
  ;; 順に覚え、同じ effect を外側の handler へ出し直して、その答えをそのまま返すため(答えは変えない — effect の順と、拒否の後に出なかった
  ;; effect を見る)。
  (session var steps #())
  (session var answers #())
  (ConfirmCleanBoot [launch]
    (<- target str (launch-target launch))
    (:= steps (+ steps #((SwapStep :name "ConfirmCleanBoot" :target target))))
    (<- verdict (ConfirmCleanBoot launch))
    (resume verdict))
  (PrepareBootRoot [launch]
    (<- target str (launch-target launch))
    (:= steps (+ steps #((SwapStep :name "PrepareBootRoot" :target target))))
    (<- answer (PrepareBootRoot launch))
    (:= answers (+ answers #(answer)))
    (resume answer))
  (AwaitQuietWindow [target timeout-seconds]
    (:= steps (+ steps #((SwapStep :name "AwaitQuietWindow" :target target))))
    (<- answer (AwaitQuietWindow :target target :timeout-seconds timeout-seconds))
    (resume answer))
  (AwaitWorkerDrained [launch timeout-seconds]
    (:= steps (+ steps #((SwapStep :name "AwaitWorkerDrained" :target launch.name))))
    (<- answer (AwaitWorkerDrained :launch launch :timeout-seconds timeout-seconds))
    (resume answer))
  (ReleaseWorkerDrain [launch]
    (:= steps (+ steps #((SwapStep :name "ReleaseWorkerDrain" :target launch.name))))
    (<- (ReleaseWorkerDrain :launch launch))
    (resume None))
  (DesireWorker [launch]
    (:= steps (+ steps #((SwapStep :name "DesireWorker" :target launch.name))))
    (<- changes (DesireWorker launch))
    (resume changes))
  (DesireCoordinator [launch]
    (:= steps (+ steps #((SwapStep :name "DesireCoordinator" :target "coordinator"))))
    (<- changes (DesireCoordinator launch))
    (resume changes))
  (PublishDeclarations []
    (:= steps (+ steps #((SwapStep :name "PublishDeclarations" :target None))))
    (<- (PublishDeclarations))
    (resume None))
  (ApplyDeclarations []
    (:= steps (+ steps #((SwapStep :name "ApplyDeclarations" :target None))))
    (<- (ApplyDeclarations))
    (resume None))
  (StepLogSeen []
    (resume (StepLog :steps steps :answers answers))))


(defk swap-steps [desire target]
  {:pre [(: desire str) (: target str)] :post [(: % (get tuple #(SwapStep ...)))] :tags {:context "doeff-cluster-test" :role "program"}}
  "worker の入れ替え 1 つの書き込みの effect の順(空の機体の確認 → root の準備 → Desire → 公開 → drain して中の仕事 0 → 当て →
   drain を外す)を、テストが比べる形で作るため(drain は当てる直前 — #3968・外すのは新しい版で live に戻った後 — #4177)。"
  #((SwapStep :name "ConfirmCleanBoot" :target target) (SwapStep :name "PrepareBootRoot" :target target)
    (SwapStep :name desire :target target) (SwapStep :name "PublishDeclarations" :target None)
    (SwapStep :name "AwaitWorkerDrained" :target target) (SwapStep :name "ApplyDeclarations" :target None)
    (SwapStep :name "ReleaseWorkerDrain" :target target)))


(defk releases-in [steps]
  {:pre [(: steps (get tuple #(SwapStep ...)))] :post [(: % (get tuple #(str ...)))] :tags {:context "doeff-cluster-test" :role "program"}}
  "Program が drain を外す頼み(ReleaseWorkerDrain)を出した worker の名を、出した順に並べるため(worker ごとの回数を比べる)。"
  (tuple (gfor s steps :if (= s.name "ReleaseWorkerDrain") s.target)))


;; coordinator の入れ替え 1 つの書き込みの effect の順(空の機体の確認 → root の準備 → Desire → 公開 → 静かな時間帯の待ち → 当て — #3772。
;; 公開は数分かかるので、静かな時間帯は当てる直前に待つ)。
(val COORDINATOR-STEPS #((SwapStep :name "ConfirmCleanBoot" :target "coordinator") (SwapStep :name "PrepareBootRoot" :target "coordinator")
                         (SwapStep :name "DesireCoordinator" :target "coordinator") (SwapStep :name "PublishDeclarations" :target None)
                         (SwapStep :name "AwaitQuietWindow" :target "coordinator") (SwapStep :name "ApplyDeclarations" :target None)))


(defk recorded-run [program]
  {:pre [(: program Program)] :post [(: % tuple)] :tags {:context "doeff-cluster-test" :role "program"}}
  "版上げの Program を走らせ(入れ替えの前の手順の拒否による停止は受ける)、step-recorder が覚えた事を読むため。答え = #(停止 覚えた事)。"
  (var refused None)
  (try
    (<- program)
    (except [e UpgradeRefused]
      (:= refused e)))
  (<- log StepLog (StepLogSeen))
  #(refused log))


(deftest test-each-swap-prepares-the-boot-root-after-the-clean-boot-and-before-the-desire
  ;; effect の順(#3725): worker の入れ替えも coordinator の入れ替えも、空の機体の確認 → root の準備 → Desire → 公開 → 当て。準備の effect を
  ;; 外すと PrepareBootRoot が列に無く、Desire の後へ動かすと順が違って赤。coordinator は Desire の前に静かな時間帯を待つ(#3772)。
  (<- seen tuple (with-handlers [(state) (sim-time-handler :clock (SimClock)) (scripted-upgrade SCRIPT) step-recorder]
                   (recorded-run (upgrade-cluster #(TARGET-A TARGET-B) TARGET-COORDINATOR SAME-VERSION LIMITS))))
  (assert (is (get seen 0) None) seen)
  (<- a tuple (swap-steps "DesireWorker" "a"))
  (<- b tuple (swap-steps "DesireWorker" "b"))
  (val log (get seen 1))
  (assert (= log.steps (+ a b COORDINATOR-STEPS)) log.steps)
  ;; 台本の世界の準備の答えは 3 つとも「組んだ」— Program はそのまま Desire へ進む。
  (assert (= (tuple (gfor answer log.answers (type answer))) #(BootRootBuilt BootRootBuilt BootRootBuilt)) log.answers))


(deftest test-a-refused-boot-root-stops-before-the-desire-and-names-the-target-and-the-reason
  ;; 失敗ケース(#3725): a の root の準備が断られる — どの理由(閉じた語の 5 つ)でも、Program は DesireWorker・PublishDeclarations・
  ;; ApplyDeclarations を 1 つも出さず(effect は a の確認と準備の 2 つだけ)、UpgradeRefused が a と、拒否の答え(理由は閉じた語)を明示する。
  (for [reason BootRootRefusal]
    (<- seen tuple (with-handlers [(state) (sim-time-handler :clock (SimClock)) (scripted-upgrade WORKERS-ONLY-SCRIPT)
                                   (refused-boot-roots (frozenset ["a"]) reason) step-recorder]
                     (recorded-run (upgrade-workers #(TARGET-A TARGET-B) LIMITS))))
    (val refused (get seen 0))
    (assert (isinstance refused UpgradeRefused) #(reason seen))
    (assert (= refused.target "a") refused)
    (assert (= refused.refusal (BootRootRefused :target "a" :reason reason)) refused.refusal)
    (assert (in reason.value (str refused)) (str refused))
    (val log (get seen 1))
    (assert (= log.steps #((SwapStep :name "ConfirmCleanBoot" :target "a") (SwapStep :name "PrepareBootRoot" :target "a"))) log.steps)))


(defhandler busy-workers [#^ frozenset busy]
  ;; 引数に残す理由: 中の仕事が終わらない worker の名の集合はテストごとに違う値(外の世界そのもの)。
  ;; 当てる直前の drain(AwaitWorkerDrained)に、busy に名のある worker では「上限の内に 0 にならない」と答え、ほかは外側へ渡すため。
  (AwaitWorkerDrained [launch timeout-seconds]
    (if (in launch.name busy)
        (resume (WorkerDrainMissed :target launch.name :reason (.format "{} の中で走っている仕事 turn-1" launch.name)))
        (do (<- answer (AwaitWorkerDrained :launch launch :timeout-seconds timeout-seconds))
            (resume answer)))))


(deftest test-a-worker-whose-inner-work-does-not-end-is-never-applied
  ;; 失敗ケース(#3968): a の中で走っている仕事が drain の上限の内に 0 にならない世界では、Program は a の宣言を書いて公開し、
  ;; 当てる直前に drain を頼み、当てずに(ApplyDeclarations を出さず)UpgradeRefused で a と残った仕事を名指して止まる。drain-worker を
  ;; upgrade-workers から外すと、ApplyDeclarations が出て赤。
  (<- seen tuple (with-handlers [(state) (sim-time-handler :clock (SimClock)) (scripted-upgrade WORKERS-ONLY-SCRIPT)
                                 (busy-workers (frozenset ["a"])) step-recorder]
                   (recorded-run (upgrade-workers #(TARGET-A TARGET-B) LIMITS))))
  (val refused (get seen 0))
  (assert (isinstance refused UpgradeRefused) seen)
  (assert (= refused.target "a") refused)
  (assert (= refused.point RefusalPoint.BEFORE-APPLY) refused)
  (assert (isinstance refused.refusal WorkerDrainMissed) refused.refusal)
  (assert (in "turn-1" (str refused)) (str refused))
  (val log (get seen 1))
  (<- a tuple (swap-steps "DesireWorker" "a"))
  ;; 当て(ApplyDeclarations)だけが出ず、a に置いた drain は止まる前に 1 回外す(#4177 — 外さないと a は新しい仕事を受けないまま残る)。
  (assert (= log.steps (+ (cut a 0 -2) (cut a -1 None))) log.steps)
  (<- released tuple (releases-in log.steps))
  (assert (= released #("a")) log.steps))


(deftest test-a-refused-clean-boot-never-asks-for-the-boot-root
  ;; 失敗ケース: a の空の機体の確認が断られた世界では、root の準備の effect は出ない(effect は a の確認の 1 つだけ)— 準備は確認の後。
  (<- seen tuple (with-handlers [(state) (sim-time-handler :clock (SimClock)) (scripted-upgrade WORKERS-ONLY-SCRIPT)
                                 (refused-clean-boots (frozenset ["a"])) step-recorder]
                   (recorded-run (upgrade-workers #(TARGET-A TARGET-B) LIMITS))))
  (val refused (get seen 0))
  (assert (isinstance refused UpgradeRefused) seen)
  (assert (isinstance refused.refusal CleanBootRefused) refused.refusal)
  (val log (get seen 1))
  (assert (= log.steps #((SwapStep :name "ConfirmCleanBoot" :target "a"))) log.steps)
  (assert (= log.answers #()) log.answers)
  ;; drain を頼む前に止まったので、drain を外す頼みは 1 回も出ない(#4177)。
  (<- released tuple (releases-in log.steps))
  (assert (= released #()) log.steps))


;; --- drain を頼んだ後は、必ず 1 回外す(ReleaseWorkerDrain — #4177)--------------------------------------------------------------------
;; drain は worker を作り直しても、頼んだ側が外すまで残る。Program は当てた worker が新しい版で live に戻った後に外し、drain の後に止まる時
;; (中の仕事が 0 にならない・当てが落ちた・live に戻らない)も、外してから同じ例外で止まる。

(defrecord ReleaseRun
  "drain の外しを見る 1 回の実行で覚えた事: stopped = Program を止めた例外(None = 最後まで通った)・script = 台本の handler が覚えた事・
   log = step-recorder が覚えた事。"
  (#^ (| Exception None) stopped)
  (#^ ScriptLog script)
  (#^ StepLog log))


(defclass ApplyBroken [RuntimeError]
  "テスト用の例外: 当て(ApplyDeclarations)が落ちた。")


(defhandler apply-breaks
  ;; 壊した handler(失敗ケース): 公開した宣言を当てる所で落ちる。
  (ApplyDeclarations []
    (raise (ApplyBroken "当てが落ちた"))))


(defk release-run [program]
  {:pre [(: program Program)] :post [(: % ReleaseRun)] :tags {:context "doeff-cluster-test" :role "program"}}
  "版上げの Program を走らせ(待ちの上限と当ての失敗による停止は受けて返す)、台本の handler と step-recorder が覚えた事を読むため。"
  (var stopped None)
  (try
    (<- program)
    (except [e [UpgradeStalled ApplyBroken]]
      (:= stopped e)))
  (<- script ScriptLog (ScriptLogSeen))
  (<- log StepLog (StepLogSeen))
  (ReleaseRun :stopped stopped :script script :log log))


(deftest test-each-worker-drain-is-released-once-after-the-worker-is-back
  ;; 正常の道(#4177): worker ごとに drain → 当て → 新しい版で live に戻る → drain を外す、の順で、外しは worker ごとにちょうど 1 回、
  ;; 次の worker の drain より前。外しを出さない Program では列に ReleaseWorkerDrain が無く、当ての直後(戻りを読む前)に外す Program では
  ;; 外した時に名簿を読んでいた回数が 1 つずつ少なくて赤(台本: a の task の待ち = 1 回目・a の戻り = 2 回目・b は 3・4 回目)。
  (<- run ReleaseRun (with-handlers [(state) (sim-time-handler :clock (SimClock)) (scripted-upgrade WORKERS-ONLY-SCRIPT) step-recorder]
                       (release-run (upgrade-workers #(TARGET-A TARGET-B) LIMITS))))
  (assert (is run.stopped None) run)
  (<- a tuple (swap-steps "DesireWorker" "a"))
  (<- b tuple (swap-steps "DesireWorker" "b"))
  (assert (= run.log.steps (+ a b)) run.log.steps)
  (<- released tuple (releases-in run.log.steps))
  (assert (= released #("a" "b")) run.log.steps)
  (assert (= run.script.released-at #(#("a" 2) #("b" 4))) run.script))


(deftest test-a-worker-that-never-comes-back-is-released-before-the-stall
  ;; 失敗ケース(#4177): a を当てた後、a が新しい版で live に戻らない — Program は a に置いた drain を 1 回外してから UpgradeStalled で
  ;; 止まる(外さずに止まると、a は戻っても新しい仕事を受けない)。b の入れ替えは始まらない。
  (<- run ReleaseRun (with-handlers [(state) (sim-time-handler :clock (SimClock)) (scripted-upgrade #(ALL-OLD A-STUCK)) step-recorder]
                       (release-run (upgrade-workers #(TARGET-A TARGET-B) LIMITS))))
  (assert (isinstance run.stopped UpgradeStalled) run)
  (assert (= run.stopped.step (.format "worker a が版 {} で live に戻る" NEW)) run.stopped.step)
  (<- a tuple (swap-steps "DesireWorker" "a"))
  (assert (= run.log.steps a) run.log.steps)
  (<- released tuple (releases-in run.log.steps))
  (assert (= released #("a")) run.log.steps))


(deftest test-a-broken-apply-is-released-before-the-same-error
  ;; 失敗ケース(#4177): a の当てが例外で落ちる — Program は a に置いた drain を 1 回外してから、同じ例外で止まる。
  (<- run ReleaseRun (with-handlers [(state) (sim-time-handler :clock (SimClock)) (scripted-upgrade WORKERS-ONLY-SCRIPT) apply-breaks
                                     step-recorder]
                       (release-run (upgrade-workers #(TARGET-A TARGET-B) LIMITS))))
  (assert (isinstance run.stopped ApplyBroken) run)
  (<- a tuple (swap-steps "DesireWorker" "a"))
  (assert (= run.log.steps a) run.log.steps)
  (<- released tuple (releases-in run.log.steps))
  (assert (= released #("a")) run.log.steps))


(deftest test-a-refused-coordinator-boot-root-stops-before-the-coordinator-desire
  ;; 失敗ケース(#3725): coordinator の root の準備が断られる — worker a・b の effect は 5 つずつ全部出て、coordinator は確認と準備の 2 つだけ
  ;; (静かな時間帯の待ち・DesireCoordinator・その公開・当ては出ない)。停止は coordinator を明示する。
  (<- seen tuple (with-handlers [(state) (sim-time-handler :clock (SimClock)) (scripted-upgrade SCRIPT)
                                 (refused-boot-roots (frozenset ["coordinator"]) BootRootRefusal.PLACE-UNAVAILABLE) step-recorder]
                   (recorded-run (upgrade-cluster #(TARGET-A TARGET-B) TARGET-COORDINATOR SAME-VERSION LIMITS))))
  (val refused (get seen 0))
  (assert (isinstance refused UpgradeRefused) seen)
  (assert (= refused.target "coordinator") refused)
  (assert (= refused.refusal (BootRootRefused :target "coordinator" :reason BootRootRefusal.PLACE-UNAVAILABLE)) refused.refusal)
  (<- a tuple (swap-steps "DesireWorker" "a"))
  (<- b tuple (swap-steps "DesireWorker" "b"))
  (val log (get seen 1))
  (assert (= log.steps (+ a b #((SwapStep :name "ConfirmCleanBoot" :target "coordinator")
                                (SwapStep :name "PrepareBootRoot" :target "coordinator"))))
          log.steps))


(defk upgrade-a-twice []
  {:pre [] :post [(: % tuple)] :tags {:context "doeff-cluster-test" :role "program"}}
  "筋書き: 模擬の世界で a を NEW へ上げ、同じ値でもう 1 度上げる(上げ直し)。答え = #(root の準備の答え 入れ替えの記録 a の版)。"
  (<- (Delay 3.0))
  (<- applied tuple (manifest-state PATHS))
  (<- run tuple (with-handlers [(flux-declarations PATHS prestop-drain COORDINATOR-SECONDS applied) desire-by-manifest step-recorder]
                  (twice-then-log)))
  (<- a SimWorker (WorkerOf "a"))
  #((get run 0) (get run 1) a.doeff-commit))


(defk twice-then-log []
  {:pre [] :post [(: % tuple)] :tags {:context "doeff-cluster-test" :role "program"}}
  "a を NEW へ 2 度上げてから、root の準備の答えと入れ替えの記録を読む。答え = #(答え 記録)。"
  (<- (upgrade-workers #(TARGET-A) LIMITS))
  (<- (upgrade-workers #(TARGET-A) LIMITS))
  (<- log StepLog (StepLogSeen))
  (<- starts tuple (UpgradeStartsSeen))
  #(log.answers starts))


(deftest test-the-boot-root-is-built-once-and-found-prepared-on-the-next-run
  ;; 答えの枝「組んだ」と「準備済みだった」(#3725): 模擬の保存先に NEW の root が無い 1 度目は組み、a が NEW で動いている 2 度目は
  ;; 準備済みと答える(何もしない)。どちらも上げる前の版の root は保存先に在る(準備は足すだけで消さない)。入れ替えは 1 度だけ。
  (<- outside SimOutside (flux-outside))
  (<- seen tuple (sim-cluster :notice-broker (MemoryBroker) NO-JOBS (upgrade-a-twice) :workers #(A B) :outside outside))
  (assert (= (get seen 0) #((BootRootBuilt :target "a" :seconds 0.0 :previous-root-present True)
                            (BootRootAlreadyPrepared :target "a" :previous-root-present True)))
          seen)
  (assert (= (tuple (gfor s (get seen 1) s.target)) #("a")) seen)
  (assert (= (get seen 2) NEW) seen))


(defhandler boot-roots-answered-outside-the-types
  ;; 壊した handler(失敗ケース): root の準備に、3 つの答えの型のどれでもない値(None)で答える。
  (PrepareBootRoot [launch]
    (resume None)))


(deftest test-an-answer-outside-the-three-types-stops-before-the-desire
  ;; 失敗ケース(#3725): handler が 3 つの型の外の値で答える — Program はそれを「準備済み」とみなして先へ進まず、答えを受けた所で、待っていた
  ;; 型と受けた値の型を明示して落ちる(先へ進むと台本の世界は最後まで通り、落ちないので赤)。
  (with [caught (pytest.raises AssertionError)]
    (<- _ (with-handlers [(state) (sim-time-handler :clock (SimClock)) (scripted-upgrade WORKERS-ONLY-SCRIPT)
                          boot-roots-answered-outside-the-types]
            (upgrade-workers #(TARGET-A TARGET-B) LIMITS))))
  (val text (str caught.value))
  (assert (in "BootRootAlreadyPrepared" text) text)
  (assert (in "BootRootBuilt" text) text)
  (assert (in "BootRootRefused" text) text)
  (assert (in "NoneType" text) text))


(defhandler boot-roots-built-without-a-previous-root
  ;; 筋書きの handler: どの対象の root も「組んだ(12.5 秒)」と答え、上げる前の版の root は保存先に残っていない(戻し先が無い)と答える。
  (PrepareBootRoot [launch]
    (<- target str (launch-target launch))
    (resume (BootRootBuilt :target target :seconds 12.5 :previous-root-present False))))


(deftest test-the-workers-program-returns-each-boot-root-answer-in-swap-order
  ;; root の準備の答えは Program の結果に載る(#3725)— 戻し先の root が残っていない(previous-root-present = 偽)事と組んだ秒を、実行した
  ;; 側が worker ごとに読める。答えを受けて捨てる Program では結果が None で、戻し先が無い事がどこにも出ない(直す前の形で赤)。
  (<- prepared tuple (with-handlers [(state) (sim-time-handler :clock (SimClock)) (scripted-upgrade WORKERS-ONLY-SCRIPT)
                                     boot-roots-built-without-a-previous-root]
                       (upgrade-workers #(TARGET-A TARGET-B) LIMITS)))
  (assert (= prepared #((BootRootBuilt :target "a" :seconds 12.5 :previous-root-present False)
                        (BootRootBuilt :target "b" :seconds 12.5 :previous-root-present False)))
          prepared))


(defhandler boot-roots-kept-only-for-the-coordinator
  ;; 筋書きの handler: どの対象の root も「組んだ(12.5 秒)」と答え、上げる前の版の root は coordinator の保存先にだけ残っていると答える
  ;; (worker の戻し先は無い — worker の入れ替えは戻し先の有無を結果に載せて進む・coordinator は戻し先が無いと始めない #3772)。
  (PrepareBootRoot [launch]
    (<- target str (launch-target launch))
    (resume (BootRootBuilt :target target :seconds 12.5 :previous-root-present (= target "coordinator")))))


(deftest test-the-cluster-program-returns-the-coordinator-boot-root-answer-last
  ;; coordinator まで上げる場合の結果は、worker の分(入れ替えた順)と coordinator の分を分けて載せる(戻し先の有無を 3 つとも対象ごとに読める)。
  (<- upgraded ClusterUpgraded (with-handlers [(state) (sim-time-handler :clock (SimClock)) (scripted-upgrade SCRIPT)
                                               boot-roots-kept-only-for-the-coordinator]
                                 (upgrade-cluster #(TARGET-A TARGET-B) TARGET-COORDINATOR SAME-VERSION LIMITS)))
  (assert (= (tuple (gfor p upgraded.workers #(p.target p.previous-root-present))) #(#("a" False) #("b" False))) upgraded)
  (val root upgraded.coordinator.root)
  (assert (= #(root.target root.previous-root-present) #("coordinator" True)) upgraded))


;; --- coordinator だけを上げる入口(upgrade-coordinator — #3772)---------------------------------------------------------------------
;; 今の cluster の形: 宣言の内の worker の版が混ざっている(a = NEW・b = MID)。coordinator NEW と組めると手元で確かめた worker の版は
;; NEW と MID。名簿と task を読む effect に台本で答え、書き込みの effect の順を step-recorder で覚える。

(val MID "5a1b2c3d4e5f60718293a4b5c6d7e8f901234567")
(val VERIFIED (VerifiedVersions :coordinator NEW :workers (frozenset [NEW MID])))
;; a の上で動いている task(coordinator を入れ替えた後も coordinator に在るか確かめる)と、終わった task。
(val RUNNING (PendingTask :task "t-run" :phase PendingPhase.ASSIGNED :worker "a"))
(val MIXED (UpgradeState :roster #((RosterEntry :worker "a" :live True :doeff-commit NEW :declaration IN)
                                   (RosterEntry :worker "b" :live True :doeff-commit MID :declaration IN))
                         :tasks #(RUNNING) :coordinator-commit OLD :known-tasks #("t-run" "t-done")))
(val MIXED-SWAPPED (replace MIXED :coordinator-commit NEW))
;; 台本: 待ち行列(空)→ 条 V1 → coordinator が NEW で答える → 宣言の内の worker が live → 待っていた task が在る。
(val MIXED-SCRIPT #(MIXED MIXED MIXED-SWAPPED))
;; 条 V1 を照らす読み(2 番目)の後に DesireCoordinator を出す。
(val COORDINATOR-V1-READ 2)


(defrecord CoordinatorRun
  "台本の世界で upgrade-coordinator を 1 回走らせた結果: outcome = 答え(CoordinatorUpgraded)か停止(UpgradeRefused・UpgradeStalled)・
   script = 台本の handler が覚えた事・log = step-recorder が覚えた事。"
  (#^ (| CoordinatorUpgraded UpgradeRefused UpgradeStalled) outcome)
  (#^ ScriptLog script)
  (#^ StepLog log))


(defk coordinator-run []
  {:pre [] :post [(: % CoordinatorRun)] :tags {:context "doeff-cluster-test" :role "program"}}
  "coordinator を NEW へ上げる入口を VERIFIED で 1 回走らせ(拒否と上限の停止は受ける)、台本と書き込みの effect の記録を読むため。"
  (var outcome None)
  (try
    (<- upgraded CoordinatorUpgraded (upgrade-coordinator TARGET-COORDINATOR VERIFIED LIMITS))
    (:= outcome upgraded)
    (except [e [UpgradeRefused UpgradeStalled]]
      (:= outcome e)))
  (<- script ScriptLog (ScriptLogSeen))
  (<- log StepLog (StepLogSeen))
  (CoordinatorRun :outcome outcome :script script :log log))


(deftest test-mixed-worker-versions-inside-the-verified-pair-are-applied
  ;; 失敗ケース 6(#3772): 宣言の内の worker の版が NEW と MID で混ざっていても、どちらも確かめた組み合わせに入っていれば coordinator を
  ;; 当てる(条 V1 の照らしの後に DesireCoordinator を出し、全部の手順を通って答える)。「全部 coordinator と同じ版」で待つ形では、
  ;; 今の cluster(版が混ざったまま)で必ず上限まで待って止まり赤。
  (<- run CoordinatorRun (with-handlers [(state) (sim-time-handler :clock (SimClock)) (scripted-upgrade MIXED-SCRIPT) step-recorder]
                           (coordinator-run)))
  (assert (isinstance run.outcome CoordinatorUpgraded) run.outcome)
  (assert (= run.script.coordinator-at COORDINATOR-V1-READ) run.script)
  (assert (= run.log.steps COORDINATOR-STEPS) run.log.steps))


;; 確かめた組み合わせに無い版(OLD)で動く、宣言の内の worker b が居る。
(val UNVERIFIED (replace MIXED :roster #((RosterEntry :worker "a" :live True :doeff-commit NEW :declaration IN)
                                         (RosterEntry :worker "b" :live True :doeff-commit OLD :declaration IN))))


(deftest test-a-worker-on-a-version-outside-the-verified-pair-is-refused-before-the-desire
  ;; 失敗ケース 5(#3772): 確かめた組み合わせに無い版の worker が 1 つ在ると、宣言を書く前に、その worker と版を明示して断る — 書き込みの
  ;; effect は 1 つも出ない(空の機体の確認より前)。待って上限で止まる形・照らさずに当てる形はどちらも赤。
  (<- run CoordinatorRun (with-handlers [(state) (sim-time-handler :clock (SimClock)) (scripted-upgrade #(UNVERIFIED)) step-recorder]
                           (coordinator-run)))
  (val refused run.outcome)
  (assert (isinstance refused UpgradeRefused) refused)
  (assert (= refused.target "coordinator") refused)
  (assert (isinstance refused.refusal UnverifiedWorkers) refused.refusal)
  (assert (= (tuple (gfor e refused.refusal.workers #(e.worker e.doeff-commit))) #(#("b" OLD))) refused.refusal)
  (assert (in "b(版 " (str refused)) (str refused))
  (assert (= run.log.steps #()) run.log.steps)
  (assert (is run.script.coordinator-at None) run.script))


;; 宣言の外の worker 2 つ: z は live で古い版(組み合わせに無い)・y は live でなく版を読めない。
(val OUTSIDE-REASON "宣言の外(配備する側が宣言を書けない worker)")
(val OUTSIDERS #((RosterEntry :worker "z" :live True :doeff-commit OLD :declaration OUT :unread-reason None)
                 (RosterEntry :worker "y" :live False :doeff-commit None :declaration OUT :unread-reason OUTSIDE-REASON)))
(val WITH-OUTSIDERS (replace MIXED :roster (+ MIXED.roster OUTSIDERS)))
(val WITH-OUTSIDERS-SWAPPED (replace WITH-OUTSIDERS :coordinator-commit NEW))


(deftest test-a-worker-outside-the-declaration-is-neither-waited-for-nor-hidden
  ;; 失敗ケース 1(#3772): 宣言の外の worker(z = 組み合わせに無い版・y = live でなく版を読めない)は、組み合わせの照らしからも待ちからも
  ;; 外して coordinator を当て、結果に名と版を必ず出す。外の worker を待つ形は上限で止まって赤、黙って外す形は結果に出ず赤。
  (<- run CoordinatorRun (with-handlers [(state) (sim-time-handler :clock (SimClock))
                                         (scripted-upgrade #(WITH-OUTSIDERS WITH-OUTSIDERS WITH-OUTSIDERS-SWAPPED)) step-recorder]
                           (coordinator-run)))
  (assert (isinstance run.outcome CoordinatorUpgraded) run.outcome)
  (assert (= (sorted (gfor e run.outcome.undeclared #(e.worker e.live (or e.doeff-commit "版を読めない"))))
             [#("y" False "版を読めない") #("z" True OLD)])
          run.outcome.undeclared)
  (assert (= run.log.steps COORDINATOR-STEPS) run.log.steps))


;; 待ち行列に task が残る(queued のまま)。
(val QUEUED-TASK (PendingTask :task "t-queued" :phase PendingPhase.QUEUED :worker None))
(val MIXED-QUEUED (replace MIXED :tasks #(RUNNING QUEUED-TASK) :known-tasks #("t-run" "t-done" "t-queued")))


(deftest test-a-queued-task-keeps-the-coordinator-from-being-applied
  ;; 失敗ケース 2(#3772): 待ち行列が空でなければ当てない — 上限まで待って「待ち行列が空」の待ちで止まり、残った task を明示する。
  ;; 書き込みの effect は 1 つも出ない。版が混ざった cluster で、条 V1 の待ちで先に止まる形(待ち行列を見る前に止まる)も赤。
  (<- run CoordinatorRun (with-handlers [(state) (sim-time-handler :clock (SimClock)) (scripted-upgrade #(MIXED-QUEUED)) step-recorder]
                           (coordinator-run)))
  (val stalled run.outcome)
  (assert (isinstance stalled UpgradeStalled) stalled)
  (assert (= stalled.step "待ち行列が空") stalled.step)
  (assert (in "t-queued" stalled.observed) stalled.observed)
  (assert (= run.log.steps #()) run.log.steps)
  (assert (is run.script.coordinator-at None) run.script))


(deftest test-a-missing-rollback-root-stops-before-the-coordinator-desire
  ;; 失敗ケース 3(#3772): coordinator の保存先に上げる前の版の root が無い(戻し先が無い)なら始めない — 拒否は戻し先が無い事を
  ;; 型(RollbackRootMissing)で明示し、書き込みの effect は確認と準備の 2 つだけ(静かな時間帯の待ち・Desire・公開・当ては出ない)。
  (<- run CoordinatorRun (with-handlers [(state) (sim-time-handler :clock (SimClock)) (scripted-upgrade MIXED-SCRIPT)
                                         boot-roots-built-without-a-previous-root step-recorder]
                           (coordinator-run)))
  (val refused run.outcome)
  (assert (isinstance refused UpgradeRefused) refused)
  (assert (= refused.target "coordinator") refused)
  (assert (isinstance refused.refusal RollbackRootMissing) refused.refusal)
  (assert (= refused.refusal.root (BootRootBuilt :target "coordinator" :seconds 12.5 :previous-root-present False)) refused.refusal)
  (assert (= run.log.steps (cut COORDINATOR-STEPS 0 2)) run.log.steps)
  (assert (is run.script.coordinator-at None) run.script))


;; 宣言の内の worker の版が全部 NEW(条 V1 はすぐ通る)— 当てた後の coordinator の答えだけを見る台本の元。
(val UNIFORM (replace ALL-NEW :tasks #(RUNNING) :known-tasks #("t-run")))
(val UNIFORM-SWAPPED (replace UNIFORM :coordinator-commit NEW))
;; 台本: 待ち行列 → 条 V1 → 当てる直前の読み直しと待ち行列 → 当てた直後は古い coordinator(OLD)が 2 回答える → 新しい coordinator が
;; NEW で答える。
(val LATE-SCRIPT #(UNIFORM UNIFORM UNIFORM UNIFORM UNIFORM UNIFORM UNIFORM-SWAPPED))
;; 新しい coordinator が初めて NEW で答える読み(7 番目)。
(val FIRST-NEW-READ 7)


(deftest test-an-old-coordinator-answering-after-the-apply-is-not-the-end
  ;; 失敗ケース 4(#3772): 当てた直後に古い coordinator(版 OLD)が答えても終わりにしない — coordinator が版 NEW で答える(7 番目の読み)
  ;; まで待ってから、宣言の内の worker と待っていた task を確かめて答える。worker が live なら終わりとする形は 5 番目の読みで答えて赤。
  (<- run CoordinatorRun (with-handlers [(state) (sim-time-handler :clock (SimClock)) (scripted-upgrade LATE-SCRIPT) step-recorder]
                           (coordinator-run)))
  (assert (isinstance run.outcome CoordinatorUpgraded) run.outcome)
  (assert (>= run.script.reads FIRST-NEW-READ) run.script))


(deftest test-a-coordinator-that-never-answers-on-the-new-version-stops-by-name
  ;; 失敗ケース 4 の止まり方: 新しい版の coordinator がいつまでも答えない(古い版が答え続ける)なら、上限で「coordinator が版 NEW で
  ;; 答える」の待ちで止まり、最後に答えた版を明示する(黙って待ち続けない・終わりにもしない)。
  (<- run CoordinatorRun (with-handlers [(state) (sim-time-handler :clock (SimClock)) (scripted-upgrade #(UNIFORM)) step-recorder]
                           (coordinator-run)))
  (val stalled run.outcome)
  (assert (isinstance stalled UpgradeStalled) stalled)
  (assert (= stalled.step (.format "coordinator が版 {} で答える" NEW)) stalled.step)
  (assert (in (cut OLD 0 10) stalled.observed) stalled.observed))


;; 宣言の内の worker c が live だが版を読めない(入れ替えの途中 — 新しい世代が準備完了でない)。
(val C-REASON "新しい世代が準備完了でない")
(val WITH-UNREADABLE (replace MIXED :roster (+ MIXED.roster #((RosterEntry :worker "c" :live True :doeff-commit None :declaration IN
                                                                            :unread-reason C-REASON)))))
;; 同じ c が宣言の外の時。
(val WITH-UNREADABLE-OUTSIDE (replace MIXED :roster (+ MIXED.roster #((RosterEntry :worker "c" :live True :doeff-commit None :declaration OUT
                                                                                    :unread-reason C-REASON)))))


(deftest test-a-worker-whose-version-cannot-be-read-stops-the-coordinator-swap-by-name
  ;; 失敗ケース 7(条 V1・#3366 のテストを #3772 の決定に合わせて書き直した): 宣言の内で版を読めない worker c は、組み合わせに入っていると
  ;; 数えずに待ち、上限で「宣言の内の worker が全部 live で版を読める」の待ちで c と理由を明示して止まる(coordinator は当てない)。
  ;; 同じ c が宣言の外なら待たずに当て、結果に c を載せる — 入れ替え中で版を読めない(待つ)と宣言の外(待たない)を型で分ける。
  (<- inside CoordinatorRun (with-handlers [(state) (sim-time-handler :clock (SimClock)) (scripted-upgrade #(WITH-UNREADABLE)) step-recorder]
                              (coordinator-run)))
  (val stalled inside.outcome)
  (assert (isinstance stalled UpgradeStalled) stalled)
  (assert (= stalled.step "宣言の内の worker が全部 live で版を読める") stalled.step)
  (assert (in "c(live=True・版を読めない" stalled.observed) stalled.observed)
  (assert (in C-REASON stalled.observed) stalled.observed)
  (assert (= inside.log.steps #()) inside.log.steps)
  (<- outside CoordinatorRun (with-handlers [(state) (sim-time-handler :clock (SimClock))
                                             (scripted-upgrade #(WITH-UNREADABLE-OUTSIDE WITH-UNREADABLE-OUTSIDE
                                                                 (replace WITH-UNREADABLE-OUTSIDE :coordinator-commit NEW)))
                                             step-recorder]
                               (coordinator-run)))
  (assert (isinstance outside.outcome CoordinatorUpgraded) outside.outcome)
  (assert (= (tuple (gfor e outside.outcome.undeclared e.worker)) #("c")) outside.outcome.undeclared))


(defhandler quiet-window-never-comes
  ;; 筋書きの handler: 入れ替えで切れて困る仕事が走り続け、上限の内に静かな時間帯が来ないと答える。
  (AwaitQuietWindow [target timeout-seconds]
    (resume (QuietWindowMissed :target target :reason "筋書き: 入れ替えで切れて困る仕事が走り続けている"))))


(deftest test-the-quiet-window-is-awaited-after-the-publish-and-before-the-apply
  ;; 失敗ケース 8(#3772・cisco-c8 の決定で書き直した): 静かな時間帯の効果は、公開(PublishDeclarations)の後・当てる(ApplyDeclarations)の
  ;; 直前に出す — 公開(マージの列・main 入り)は数分かかり、その間に入れ替えで切れて困る仕事が始まりうるので、当てる直前にもう 1 度
  ;; 読む。(a) 通る時の順は 確認 → 準備 → Desire → 公開 → 静かな時間帯 → 当て。Desire の前に待つ形は順が違って赤。
  (<- run CoordinatorRun (with-handlers [(state) (sim-time-handler :clock (SimClock)) (scripted-upgrade #(UNIFORM UNIFORM UNIFORM-SWAPPED))
                                         step-recorder]
                           (coordinator-run)))
  (assert (isinstance run.outcome CoordinatorUpgraded) run.outcome)
  (assert (= run.log.steps COORDINATOR-STEPS) run.log.steps))


(deftest test-a-quiet-window-that-never-comes-refuses-the-apply-after-the-publish
  ;; 失敗ケース 8 の止まり方(#3772): 上限の内に静かな時間帯が来なければ、ApplyDeclarations を出さずに UpgradeRefused で止まり、
  ;; 断りの理由は静かな時間帯が来なかった事(QuietWindowMissed — 閉じた型)。それまでに DesireCoordinator と PublishDeclarations は
  ;; 出ている — 宣言と公開は済み・当てていない状態で止まった事を、型と文で分かる(自動では戻さない)。UpgradeStalled で止まる形・
  ;; 宣言の前に止まる形は赤。
  (<- missed CoordinatorRun (with-handlers [(state) (sim-time-handler :clock (SimClock)) (scripted-upgrade #(UNIFORM)) quiet-window-never-comes
                                            step-recorder]
                              (coordinator-run)))
  (val refused missed.outcome)
  (assert (isinstance refused UpgradeRefused) refused)
  (assert (= refused.target "coordinator") refused)
  (assert (= refused.refusal (QuietWindowMissed :target "coordinator" :reason "筋書き: 入れ替えで切れて困る仕事が走り続けている"))
          refused.refusal)
  (assert (= refused.point RefusalPoint.BEFORE-APPLY) refused.point)
  (assert (in "当てていない" (str refused)) (str refused))
  (assert (= missed.log.steps (cut COORDINATOR-STEPS 0 5)) missed.log.steps)
  (assert (= missed.script.coordinator-at 2) missed.script))


;; 公開の間に変わる世界: 宣言の前の 2 回の読み(待ち行列・条 V1)は UNIFORM のまま、公開の後の読みから変わる。
;; queued の task t-late が積まれた(作り直した coordinator が落としうる — #2440)。
(val LATE-QUEUED (PendingTask :task "t-late" :phase PendingPhase.QUEUED :worker None))
(val UNIFORM-LATE-QUEUED (replace UNIFORM :tasks #(RUNNING LATE-QUEUED) :known-tasks #("t-run" "t-late")))
;; 別の worker の入れ替えが入り、宣言の内の worker b が確かめた組み合わせに無い版 OLD で動いている。
(val UNIFORM-LATE-UNVERIFIED (replace UNIFORM :roster #((RosterEntry :worker "a" :live True :doeff-commit NEW :declaration IN)
                                                        (RosterEntry :worker "b" :live True :doeff-commit OLD :declaration IN))))


(deftest test-a-task-queued-during-the-publish-refuses-the-apply
  ;; 失敗ケース(#3772・doeff-cluster の持ち主の読み 1): 宣言の前は待ち行列が空でも、公開の間に queued の task が積まれたら、当てる直前の
  ;; 確かめで上限まで待ち、空にならなければ当てずに UpgradeRefused(QueuedTasksRemain — 残った task の id)で止まる。宣言と公開は
  ;; 済んでいる(断った所 = 当てる前)。待ち行列を入口の最初にしか見ない形は当てて赤(作り直しで queued を落とす形)。
  (<- run CoordinatorRun (with-handlers [(state) (sim-time-handler :clock (SimClock)) (scripted-upgrade #(UNIFORM UNIFORM UNIFORM-LATE-QUEUED))
                                         step-recorder]
                           (coordinator-run)))
  (val refused run.outcome)
  (assert (isinstance refused UpgradeRefused) refused)
  (assert (= refused.refusal (QueuedTasksRemain :target "coordinator" :tasks #("t-late"))) refused.refusal)
  (assert (= refused.point RefusalPoint.BEFORE-APPLY) refused.point)
  (assert (in "t-late" (str refused)) (str refused))
  (assert (= run.log.steps (cut COORDINATOR-STEPS 0 4)) run.log.steps))


(deftest test-a-worker-version-changed-during-the-publish-refuses-the-apply
  ;; 失敗ケース(#3772・doeff-cluster の持ち主の読み 2): 公開の間に、宣言の内の worker b の版が確かめた組み合わせに無い版(OLD)へ
  ;; 替わったら、当てる直前の確かめで状態を読み直し、同じ条 V1 の照らしで当てずに UpgradeRefused(UnverifiedWorkers — b と版)で止まる
  ;; (断った所 = 当てる前)。V1 を宣言の前にしか照らさない形は当てて赤。
  (<- run CoordinatorRun (with-handlers [(state) (sim-time-handler :clock (SimClock)) (scripted-upgrade #(UNIFORM UNIFORM UNIFORM-LATE-UNVERIFIED))
                                         step-recorder]
                           (coordinator-run)))
  (val refused run.outcome)
  (assert (isinstance refused UpgradeRefused) refused)
  (assert (isinstance refused.refusal UnverifiedWorkers) refused.refusal)
  (assert (= (tuple (gfor e refused.refusal.workers #(e.worker e.doeff-commit))) #(#("b" OLD))) refused.refusal)
  (assert (= refused.point RefusalPoint.BEFORE-APPLY) refused.point)
  (assert (in "当てていない" (str refused)) (str refused))
  (assert (= run.log.steps (cut COORDINATOR-STEPS 0 4)) run.log.steps))


(deftest test-a-refused-coordinator-clean-boot-writes-no-declaration-through-the-coordinator-entry
  ;; 失敗ケース 9(#3772・upgrade-cluster の同じテストを coordinator だけの入口でも断言する): coordinator の空の機体の起動が断られたら、
  ;; 宣言を書かない — 書き込みの effect は確認の 1 つだけ。版が混ざった cluster で条 V1 の待ちで止まる形(確認まで着かない)も赤。
  (<- run CoordinatorRun (with-handlers [(state) (sim-time-handler :clock (SimClock)) (scripted-upgrade MIXED-SCRIPT)
                                         (refused-clean-boots (frozenset ["coordinator"])) step-recorder]
                           (coordinator-run)))
  (val refused run.outcome)
  (assert (isinstance refused UpgradeRefused) refused)
  (assert (= refused.target "coordinator") refused)
  (assert (isinstance refused.refusal CleanBootRefused) refused.refusal)
  (assert (= run.log.steps (cut COORDINATOR-STEPS 0 1)) run.log.steps)
  (assert (is run.script.coordinator-at None) run.script))


;; 入れ替えの後の coordinator が、前に待っていた task t-run を知らない(作り直しで記録を失った)。
(val LOST-TASK (replace MIXED-SWAPPED :known-tasks #("t-done")))


(deftest test-a-task-the-new-coordinator-does-not-know-stops-by-name
  ;; 入れ替えの前に待っていた task(t-run)が、入れ替えの後の coordinator に無ければ、上限で「待っていた task が coordinator に在る」の
  ;; 待ちで止まり、無い task を明示する(coordinator が記録を失った事を黙って終わりにしない)。
  (<- run CoordinatorRun (with-handlers [(state) (sim-time-handler :clock (SimClock)) (scripted-upgrade #(MIXED MIXED LOST-TASK)) step-recorder]
                           (coordinator-run)))
  (val stalled run.outcome)
  (assert (isinstance stalled UpgradeStalled) stalled)
  (assert (= stalled.step "待っていた task が coordinator に在る") stalled.step)
  (assert (in "t-run" stalled.observed) stalled.observed))
