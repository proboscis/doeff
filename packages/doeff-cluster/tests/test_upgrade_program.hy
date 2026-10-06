;; 版上げの Program(shared/core/upgrade_program.hy — #3366 の単位 3)の検。sim-cluster の上で、宣言の effect に flux-declarations(公開 =
;; 何もしない・当てる = 模擬の Flux・root の準備 = 模擬の置き場)が答え、Program が条 V1〜V5 を自分で守る事(入れ替えの瞬間の記録と
;; 置き場の写しで破り 0)・走り中の task の終わりを待つ事・待ちの上限を越えると名指しで落ちる事・入れ替えの前の手(空の機体の確かめ →
;; root の準備)が断られたら何も書かずに名指しで止まる事を確かめる。
;;
;; DesireWorker / DesireCoordinator に答えるのは、検の道具 desire-by-manifest(tests/flux_fixtures.hy — 値から manifest を launch_rules の
;; 写しで作り直して置き場へ書く)— doeff は配備する側の repo の本番の handler を import できないため。本番の handler で一周する検は
;; 配備する側の repo に置く(その repo の一周の検を、この Program に替える)。
(require doeff-hy.macros [deftest defk defhandler defeffect <- val var])
(require doeff-hy.record [defrecord])
(val MODULE-TAGS {:context "doeff-cluster-test" :role "test"})
(import collections.abc [Callable])
(import dataclasses [dataclass replace])
(import pytest)
(import doeff [with-handlers Program])
(import doeff_core_effects.handlers [state])
(import doeff_time [Delay SimClock sim-time-handler])
(import doeff_cluster.shared.core.detached_rules [submit-detached-task])
(import doeff_cluster.shared.intent.detached_model [AwaitDetached DetachedSucceeded])
(import doeff_cluster.shared.intent.launch_model [WorkerLaunch CoordinatorLaunch DesireWorker DesireCoordinator])
(import doeff_cluster.shared.intent.upgrade_model [UpgradeLimits UpgradeStalled UpgradeState UpgradeStart RosterEntry PendingTask PendingPhase
                                                   ReadUpgradeState PublishDeclarations ApplyDeclarations ConfirmCleanBoot
                                                   CleanBootPassed CleanBootRefused UpgradeRefused PrepareBootRoot BootRootAlreadyPrepared
                                                   BootRootBuilt BootRootRefused BootRootRefusal])
(import doeff_cluster.shared.intent.detached_model [AwaitRunnersChange RunnersChange])
(import doeff_cluster.shared.core.upgrade_program [upgrade-cluster upgrade-workers])
(import doeff_cluster.sim.local [sim-cluster SimWorker SimOutside WorkerOf DrainWorker ReadCoordinator])
(import doeff_cluster.sim.flux [FluxPass manifest-state prestop-drain flux-declarations refused-clean-boots refused-boot-roots
                                launch-target UpgradeStartsSeen BootRootsAtStartsSeen])
(import tests.flux_fixtures [OLD NEW NO-JOBS PATHS ON-X A B COORDINATOR-SECONDS program-breaches-of flux-outside desire-by-manifest])
(import tests.detached_rig [slow-add])

;; 宣言の値(本番の DECLARED-WORKERS に当たる)— 版は NEW へ。
(val TARGET-A (WorkerLaunch :name "a" :provides #("x-tool" "host-a") :exclusive #() :capacity 1 :task-reserve 0 :doeff-commit NEW))
(val TARGET-B (WorkerLaunch :name "b" :provides #("y-tool" "host-b") :exclusive #() :capacity 1 :task-reserve 0 :doeff-commit NEW))
(val TARGET-COORDINATOR (CoordinatorLaunch :doeff-commit NEW))
(val LIMITS (UpgradeLimits :drain-seconds 120.0 :return-seconds 60.0 :queue-seconds 120.0))
;; 本番の Pod の猶予(terminationGracePeriodSeconds — agent-worker は 120 秒)が task より短い worker の代わりの猶予の秒。
(val GRACE-SECONDS 5.0)


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
  "筋書き: 宣言を読み、Program で a・b・coordinator を NEW へ上げ、入れ替えの瞬間の記録と置き場の写しを条 V1〜V5 で判じる。
   答え = #(破りの条の名 a の版 b の版 記録)。"
  (<- (Delay 3.0))
  (<- applied tuple (manifest-state PATHS))
  (<- run tuple (with-handlers [(flux-declarations PATHS drain COORDINATOR-SECONDS applied) desire-by-manifest]
                  (upgrade-then-starts limits)))
  (<- rules tuple (program-breaches-of (get run 0) (get run 1)))
  (<- a SimWorker (WorkerOf "a"))
  (<- b SimWorker (WorkerOf "b"))
  #(rules a.doeff-commit b.doeff-commit (get run 0)))


(defk upgrade-then-starts [limits]
  {:pre [(: limits UpgradeLimits)] :post [(: % tuple)] :tags {:context "doeff-cluster-test" :role "program"}}
  "Program を走らせてから、模擬の Flux が当てた入れ替えの瞬間の記録と、同じ瞬間の置き場の写しを読む。答え = #(記録 置き場の写し)。"
  (<- (upgrade-cluster #(TARGET-A TARGET-B) TARGET-COORDINATOR limits))
  (<- starts tuple (UpgradeStartsSeen))
  (<- places tuple (BootRootsAtStartsSeen))
  #(starts places))


(deftest test-the-upgrade-program-keeps-v1-to-v5
  ;; V5(#3725): どの入れ替えも、置き場に入れ替え先の版の root を先に準備してから始まる — Program から準備の手を外すと、a・b・
  ;; coordinator の入れ替えの瞬間の置き場に NEW の root が無く、V5 が赤(直す前の Program で見た)。
  (<- outside SimOutside (flux-outside))
  (<- seen tuple (sim-cluster NO-JOBS (upgrade-and-judge LIMITS prestop-drain) :workers #(A B) :outside outside))
  (val rules (get seen 0))
  (val a-commit (get seen 1))
  (val b-commit (get seen 2))
  (val starts (get seen 3))
  (assert (= rules #()) rules)
  (assert (= #(a-commit b-commit) #(NEW NEW)) seen)
  ;; 1 台ずつ: worker a・b・coordinator の順に 1 つずつ入れ替えた。
  (assert (= (tuple (gfor s starts s.target)) #("a" "b" "coordinator")) starts))


;; 模擬の世界で root の準備が断られる時の訳(どの訳でも同じ止まり方 — 訳ごとの名指しは下の台本の検が 4 つとも通す)。
(val SIM-REFUSAL BootRootRefusal.PREPARE-FAILED)


(defk upgrade-with-refused-boots [clean-boots boot-roots]
  {:pre [(: clean-boots frozenset) (: boot-roots frozenset)] :post [(: % tuple)] :tags {:context "doeff-cluster-test" :role "program"}}
  "筋書き: clean-boots に名の在る入れ替え先の空の起動が落ち(2026-10-05 07:1x の形 — 起動の時に読む物が壊れていて、空の Pod が起動で
   落ちる版)、boot-roots に名の在る入れ替え先の自己起動の root の準備が断られる(#3725)世界で Program を走らせる。
   答え = #(止まりの例外 止まった後の宣言 入れ替えの記録 a の版 b の版 置き場の写し)。"
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
  "Program を走らせて入れ替えの前の手の断りによる止まり(UpgradeRefused)を受け、模擬の Flux が当てた入れ替えの記録と置き場の写しを読む。
   答え = #(止まり 記録 置き場の写し)。"
  (var refused None)
  (try
    (<- (upgrade-cluster #(TARGET-A TARGET-B) TARGET-COORDINATOR LIMITS))
    (except [e UpgradeRefused]
      (:= refused e)))
  (<- starts tuple (UpgradeStartsSeen))
  (<- places tuple (BootRootsAtStartsSeen))
  #(refused starts places))


(deftest test-a-worker-whose-clean-boot-fails-stops-the-upgrade-before-anything-is-written
  ;; 失敗ケース(Mac の調整役の条件 3・今朝の形): 最初の worker a の入れ替え先の空の起動が落ちる — Program は a を名指して止まり、宣言を
  ;; 書かず(置き場は始めと同じ)、何も入れ替えず、a・b は元の版のまま。
  (<- outside SimOutside (flux-outside))
  (<- seen tuple (sim-cluster NO-JOBS (upgrade-with-refused-boots (frozenset ["a"]) (frozenset)) :workers #(A B) :outside outside))
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
  ;; 入れ替えもしない(入れ替えの記録は a・b だけ)。止まりは coordinator を名指す。
  (<- outside SimOutside (flux-outside))
  (<- seen tuple (sim-cluster NO-JOBS (upgrade-with-refused-boots (frozenset ["coordinator"]) (frozenset)) :workers #(A B)
                              :outside outside))
  (val refused (get seen 0))
  (assert (isinstance refused UpgradeRefused) seen)
  (assert (= refused.target "coordinator") refused)
  (assert (= (tuple (gfor s (get seen 2) s.target)) #("a" "b")) seen)
  (assert (= #((get seen 3) (get seen 4)) #(NEW NEW)) seen))


(deftest test-a-worker-whose-boot-root-cannot-be-prepared-stops-the-upgrade-before-anything-is-written
  ;; 失敗ケース(#3725): 最初の worker a の入れ替え先の版の自己起動の root を、a の置き場に準備できない — Program は a と断りの訳
  ;; (閉じた語)を名指して止まり、宣言を書かず(置き場は始めと同じ)、何も入れ替えず、a・b は元の版のまま。準備の手を Desire の後に
  ;; 置くと、宣言が書かれて置き場が始めと違い赤(準備の手の無い直す前の Program は、止まらずに全部入れ替えて赤)。
  (<- outside SimOutside (flux-outside))
  (<- seen tuple (sim-cluster NO-JOBS (upgrade-with-refused-boots (frozenset) (frozenset ["a"])) :workers #(A B) :outside outside))
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
  ;; 準備してから = V5 の破り 0)、coordinator の宣言は書かれず入れ替えもしない。止まりは coordinator と訳を名指す。
  (<- outside SimOutside (flux-outside))
  (<- seen tuple (sim-cluster NO-JOBS (upgrade-with-refused-boots (frozenset) (frozenset ["coordinator"])) :workers #(A B)
                              :outside outside))
  (val refused (get seen 0))
  (assert (isinstance refused UpgradeRefused) seen)
  (assert (= refused.target "coordinator") refused)
  (assert (= refused.refusal (BootRootRefused :target "coordinator" :reason SIM-REFUSAL)) refused.refusal)
  (assert (= (tuple (gfor s (get seen 2) s.target)) #("a" "b")) seen)
  (assert (= #((get seen 3) (get seen 4)) #(NEW NEW)) seen)
  (<- rules tuple (program-breaches-of (get seen 2) (get seen 5)))
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
  (<- seen tuple (sim-cluster NO-JOBS (upgrade-under-a-running-task LIMITS drain-within-grace) :workers #(A B) :outside outside))
  (assert (= (get seen 0) #()) seen)
  (assert (= (get seen 1) (DetachedSucceeded 104)) seen)
  (val a-start (next (gfor s (get seen 2) :if (= s.target "a") s)))
  (assert (not (any (gfor t a-start.tasks (= t.worker "a")))) a-start))


(deftest test-the-upgrade-program-stops-by-name-when-a-wait-passes-its-limit
  ;; 失敗ケース(cisco-c8 の条件 1): a の上の task が drain の上限(5 秒)のうちに終わらない — Program は黙って待ち続けず、どの待ちで
  ;; 止まったかを名指しで落ちる。
  (<- outside SimOutside (flux-outside))
  (with [caught (pytest.raises UpgradeStalled)]
    (<- _ (sim-cluster NO-JOBS (upgrade-under-a-running-task (replace LIMITS :drain-seconds 5.0) drain-within-grace) :workers #(A B) :outside outside)))
  (assert (= caught.value.step "worker a に置かれた task が終わる") caught.value.step)
  (assert (= caught.value.limit-seconds 5.0)))


;; --- 条 V4 の待ち: 名簿と task の読みを台本で返す(時刻の競りの無い形)---------------------------------------------------------
;; sim の上では、待ち行列が空でない瞬間に Program が coordinator の手前へ着く順を、時刻の競り無しには作れない。名簿と task の読み
;; (ReadUpgradeState — 外の世界の境界)だけを台本の handler で返し、Program が待ち行列の空を読むまで DesireCoordinator を出さない事を見る。

(defrecord ScriptLog
  "台本の handler が覚えた事: reads = 読みの回数・coordinator-at = DesireCoordinator を出した時の読みの回数(None = 出していない)。"
  (#^ int reads)
  (#^ (| int None) coordinator-at))

(defeffect ScriptLogSeen
  "検の effect: 台本の handler が覚えた事(ScriptLog)。"
  {:answer ScriptLog :tags {:context "doeff-cluster-test" :role "intent"}})


(val ALL-OLD (UpgradeState :roster #((RosterEntry :worker "a" :live True :doeff-commit OLD) (RosterEntry :worker "b" :live True :doeff-commit OLD))
                           :tasks #()))
(val A-NEW (UpgradeState :roster #((RosterEntry :worker "a" :live True :doeff-commit NEW) (RosterEntry :worker "b" :live True :doeff-commit OLD))
                         :tasks #()))
(val ALL-NEW (UpgradeState :roster #((RosterEntry :worker "a" :live True :doeff-commit NEW) (RosterEntry :worker "b" :live True :doeff-commit NEW))
                           :tasks #()))
(val ALL-NEW-QUEUED (UpgradeState :roster ALL-NEW.roster :tasks #((PendingTask :task "t9" :phase PendingPhase.QUEUED :worker None))))
;; 読みの台本(Program が読む順): a の drain → a の戻り → b の drain → b の戻り → 全部の戻り → 待ち行列(空でない 2 回 → 空)→ coordinator の戻り。
(val SCRIPT #(ALL-OLD A-NEW A-NEW ALL-NEW ALL-NEW ALL-NEW-QUEUED ALL-NEW-QUEUED ALL-NEW ALL-NEW))
;; 待ち行列が空と読める回(SCRIPT の 8 番目)— DesireCoordinator はこの後。
(val QUEUE-EMPTY-READ 8)


(defhandler scripted-upgrade [#^ tuple script]
  ;; 引数に残す理由: 読みの台本は検ごとに違う値(設定ではなく外の世界そのもの)。
  ;; 名簿と task の読みを台本の順に返し、版の変化の待ちはすぐ返し、宣言の effect は覚えるだけにするため(空の機体の確かめは通し、
  ;; root の準備は組んだと答える)。
  (session var reads 0)
  (session var coordinator-at None)
  (ReadUpgradeState []
    (val at (min reads (- (len script) 1)))
    (:= reads (+ reads 1))
    (resume (get script at)))
  (AwaitRunnersChange [after timeout-seconds]
    (resume (RunnersChange :revision (+ after 1) :changed True)))
  (DesireWorker [launch]
    (resume #()))
  (DesireCoordinator [launch]
    (:= coordinator-at reads)
    (resume #()))
  (ConfirmCleanBoot [launch]
    (resume (CleanBootPassed :target (if (isinstance launch WorkerLaunch) launch.name "coordinator"))))
  (PrepareBootRoot [launch]
    (<- target str (launch-target launch))
    (resume (BootRootBuilt :target target :seconds 0.0 :previous-root-present True)))
  (PublishDeclarations []
    (resume None))
  (ApplyDeclarations []
    (resume None))
  (ScriptLogSeen []
    (resume (ScriptLog :reads reads :coordinator-at coordinator-at))))


(defk scripted-run []
  {:pre [] :post [(: % ScriptLog)] :tags {:context "doeff-cluster-test" :role "program"}}
  "台本の読みで Program を 1 回走らせ、覚えた事を返す。"
  (<- (upgrade-cluster #(TARGET-A TARGET-B) TARGET-COORDINATOR LIMITS))
  (<- seen ScriptLog (ScriptLogSeen))
  seen)


(deftest test-the-upgrade-program-waits-for-an-empty-queue-before-the-coordinator
  ;; 条 V4 を Program が守る: 待ち行列に task が在る間(台本の 6・7 番目の読み)は coordinator を入れ替えず、空と読んだ(8 番目)後に
  ;; DesireCoordinator を出す。待ちを外すと 5 番目の読みの後に出る(赤)。
  (<- seen ScriptLog (with-handlers [(state) (sim-time-handler :clock (SimClock)) (scripted-upgrade SCRIPT)] (scripted-run)))
  (assert (= seen.coordinator-at QUEUE-EMPTY-READ) seen))


;; --- worker だけの回(upgrade-workers)と、版の読めない worker の在る名簿(#3366 — 本番の入口の前に要る 2 つ)--------------------

;; 読みの台本: a の drain → a の戻り → b の drain → b の戻り(coordinator の待ちは無い)。
(val WORKERS-ONLY-SCRIPT #(ALL-OLD A-NEW A-NEW ALL-NEW))


(defk workers-only-run []
  {:pre [] :post [(: % ScriptLog)] :tags {:context "doeff-cluster-test" :role "program"}}
  "台本の読みで worker だけの回を 1 回走らせ、覚えた事を返す。"
  (<- (upgrade-workers #(TARGET-A TARGET-B) LIMITS))
  (<- seen ScriptLog (ScriptLogSeen))
  seen)


(deftest test-a-workers-only-upgrade-never-touches-the-coordinator
  ;; worker だけの回は a・b を 1 台ずつ入れ替え(読み 4 回 = drain と戻りを 2 台分)、coordinator に Desire を出さない。
  (<- seen ScriptLog (with-handlers [(state) (sim-time-handler :clock (SimClock)) (scripted-upgrade WORKERS-ONLY-SCRIPT)] (workers-only-run)))
  (assert (is seen.coordinator-at None) seen)
  (assert (= seen.reads (len WORKERS-ONLY-SCRIPT)) seen))


;; a・b は新しい版で戻ったが、名簿には版の読めない worker c(配備する側が宣言を書けない worker — doeff-commit None)も居る。
(val WITH-UNREADABLE (UpgradeState :roster (+ ALL-NEW.roster #((RosterEntry :worker "c" :live True :doeff-commit None))) :tasks #()))


(defhandler unreadable-roster [#^ UpgradeState settled]
  ;; 引数に残す理由: a・b を入れ替えた後の名簿は検ごとに違う値(外の世界そのもの)。
  ;; a・b の drain と戻りまでは台本どおりに答え、その後は名簿 settled を返し続け、版の変化の待ちは上限まで時間を進めるため
  ;; (coordinator を入れ替えようとしたら、その場で検を落とす)。
  (session var reads 0)
  (ReadUpgradeState []
    (val at reads)
    (:= reads (+ reads 1))
    (resume (if (< at (len WORKERS-ONLY-SCRIPT)) (get WORKERS-ONLY-SCRIPT at) settled)))
  (AwaitRunnersChange [after timeout-seconds]
    (<- (Delay timeout-seconds))
    (resume (RunnersChange :revision (+ after 1) :changed True)))
  (DesireWorker [launch]
    (resume #()))
  (DesireCoordinator [launch]
    (raise (AssertionError "版の読めない worker c を新しい版と数え、coordinator を入れ替えた")))
  (ConfirmCleanBoot [launch]
    (resume (CleanBootPassed :target (if (isinstance launch WorkerLaunch) launch.name "coordinator"))))
  (PrepareBootRoot [launch]
    (<- target str (launch-target launch))
    (resume (BootRootBuilt :target target :seconds 0.0 :previous-root-present True)))
  (PublishDeclarations []
    (resume None))
  (ApplyDeclarations []
    (resume None)))


(deftest test-a-worker-whose-version-cannot-be-read-stops-the-coordinator-swap-by-name
  ;; 失敗ケース(条 V1): 名簿の worker c の版が読めない(None)— Program は c を新しい版と数えず、「worker が全部 版 NEW で live」の
  ;; 待ちで上限を越えて名指しで止まり、coordinator を入れ替えない。None を新しい版と数える形にすると DesireCoordinator で赤。
  (with [caught (pytest.raises UpgradeStalled)]
    (<- _ (with-handlers [(state) (sim-time-handler :clock (SimClock)) (unreadable-roster WITH-UNREADABLE)]
            (upgrade-cluster #(TARGET-A TARGET-B) TARGET-COORDINATOR LIMITS))))
  (assert (= caught.value.step (.format "worker が全部 版 {} で live" NEW)) caught.value.step))


;; a を入れ替えた後、a は live だが版を読めない(読み手が訳を書いた — 新しい世代が準備完了でない)まま戻らない。
(val A-STUCK-REASON "新しい世代が準備完了でない(準備完了の判定 = その上の job が答える事)")
(val A-STUCK (UpgradeState :roster #((RosterEntry :worker "a" :live True :doeff-commit None :unread-reason A-STUCK-REASON)
                                     (RosterEntry :worker "b" :live True :doeff-commit OLD))
                           :tasks #()))


(defhandler stuck-after-drain [#^ UpgradeState settled]
  ;; 引数に残す理由: 戻らない名簿は検ごとに違う値(外の世界そのもの)。
  ;; drain の読み(最初の 1 回)は旧い版の名簿で答え、その後は名簿 settled を返し続け、版の変化の待ちは上限まで時間を進めるため。
  (session var reads 0)
  (ReadUpgradeState []
    (val at reads)
    (:= reads (+ reads 1))
    (resume (if (= at 0) ALL-OLD settled)))
  (AwaitRunnersChange [after timeout-seconds]
    (<- (Delay timeout-seconds))
    (resume (RunnersChange :revision (+ after 1) :changed True)))
  (DesireWorker [launch]
    (resume #()))
  (ConfirmCleanBoot [launch]
    (resume (CleanBootPassed :target launch.name)))
  (PrepareBootRoot [launch]
    (<- target str (launch-target launch))
    (resume (BootRootBuilt :target target :seconds 0.0 :previous-root-present True)))
  (PublishDeclarations []
    (resume None))
  (ApplyDeclarations []
    (resume None)))


(deftest test-a-stalled-return-names-the-last-reading-of-the-worker
  ;; 失敗ケース(#3366 — 止まった時の文を分ける): a の戻りの待ちが上限で止まった時、文は待ちの名だけでなく、最後に読んだ a の行
  ;; (live=True・版を読めない訳)を載せる — 「worker は起きたが、上の job が答えない」と分かる。observe の文を載せない形にすると赤。
  (with [caught (pytest.raises UpgradeStalled)]
    (<- _ (with-handlers [(state) (sim-time-handler :clock (SimClock)) (stuck-after-drain A-STUCK)]
            (upgrade-workers #(TARGET-A) LIMITS))))
  (assert (= caught.value.step (.format "worker a が版 {} で live に戻る" NEW)) caught.value.step)
  (val text (str caught.value))
  (assert (in "live=True" text) text)
  (assert (in A-STUCK-REASON text) text)
  (assert (in A-STUCK-REASON caught.value.observed) caught.value.observed))


;; --- 入れ替えの前の root の準備(PrepareBootRoot — #3725): 手の順・断りで出なくなる手・答えの 3 つの枝 ---------------------------
;; Program が出した書きの手を、出した順に覚える道具(step-recorder)を一番内側に置き、手の順と「出なかった手」を見る。

(defrecord SwapStep
  "Program が出した書きの手 1 つ: name = effect の名・target = 何の入れ替えの手か(公開と当ては的を持たないので None)。"
  (#^ str name)
  (#^ (| str None) target))

(defrecord StepLog
  "step-recorder が覚えた事: steps = Program が出した書きの手(出した順)・answers = root の準備の答え(受けた順)。"
  (#^ (get tuple #(SwapStep ...)) steps)
  (#^ (get tuple #((| BootRootAlreadyPrepared BootRootBuilt BootRootRefused) ...)) answers))

(defeffect StepLogSeen
  "検の effect: step-recorder が覚えた事(StepLog)。"
  {:answer StepLog :tags {:context "doeff-cluster-test" :role "intent"}})


(defhandler step-recorder
  ;; 検の道具: 版上げの Program が出した書きの手(空の機体の確かめ・root の準備・Desire・公開・当て)を出した順に覚え、同じ手を外側の
  ;; 答え手へ出し直して、その答えをそのまま返すため(答えは変えない — 手の順と、断りの後に出なかった手を見る)。
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
  "入れ替え 1 つの書きの手の順(空の機体の確かめ → root の準備 → Desire → 公開 → 当て)を、検が比べる形で作るため。"
  #((SwapStep :name "ConfirmCleanBoot" :target target) (SwapStep :name "PrepareBootRoot" :target target)
    (SwapStep :name desire :target target) (SwapStep :name "PublishDeclarations" :target None)
    (SwapStep :name "ApplyDeclarations" :target None)))


(defk recorded-run [program]
  {:pre [(: program Program)] :post [(: % tuple)] :tags {:context "doeff-cluster-test" :role "program"}}
  "版上げの Program を走らせ(入れ替えの前の手の断りによる止まりは受ける)、step-recorder が覚えた事を読むため。答え = #(止まり 覚えた事)。"
  (var refused None)
  (try
    (<- program)
    (except [e UpgradeRefused]
      (:= refused e)))
  (<- log StepLog (StepLogSeen))
  #(refused log))


(deftest test-each-swap-prepares-the-boot-root-after-the-clean-boot-and-before-the-desire
  ;; 手の順(#3725): worker の入れ替えも coordinator の入れ替えも、空の機体の確かめ → root の準備 → Desire → 公開 → 当て。準備の手を
  ;; 外すと PrepareBootRoot が列に無く、Desire の後へ動かすと順が違って赤。
  (<- seen tuple (with-handlers [(state) (sim-time-handler :clock (SimClock)) (scripted-upgrade SCRIPT) step-recorder]
                   (recorded-run (upgrade-cluster #(TARGET-A TARGET-B) TARGET-COORDINATOR LIMITS))))
  (assert (is (get seen 0) None) seen)
  (<- a tuple (swap-steps "DesireWorker" "a"))
  (<- b tuple (swap-steps "DesireWorker" "b"))
  (<- coordinator tuple (swap-steps "DesireCoordinator" "coordinator"))
  (val log (get seen 1))
  (assert (= log.steps (+ a b coordinator)) log.steps)
  ;; 台本の世界の準備の答えは 3 つとも「組んだ」— Program はそのまま Desire へ進む。
  (assert (= (tuple (gfor answer log.answers (type answer))) #(BootRootBuilt BootRootBuilt BootRootBuilt)) log.answers))


(deftest test-a-refused-boot-root-stops-before-the-desire-and-names-the-target-and-the-reason
  ;; 失敗ケース(#3725): a の root の準備が断られる — どの訳(閉じた語の 4 つ)でも、Program は DesireWorker・PublishDeclarations・
  ;; ApplyDeclarations を 1 つも出さず(手は a の確かめと準備の 2 つだけ)、UpgradeRefused が a と、断りの答え(訳は閉じた語)を名指す。
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


(deftest test-a-refused-clean-boot-never-asks-for-the-boot-root
  ;; 失敗ケース: a の空の機体の確かめが断られた世界では、root の準備の手は出ない(手は a の確かめの 1 つだけ)— 準備は確かめの後。
  (<- seen tuple (with-handlers [(state) (sim-time-handler :clock (SimClock)) (scripted-upgrade WORKERS-ONLY-SCRIPT)
                                 (refused-clean-boots (frozenset ["a"])) step-recorder]
                   (recorded-run (upgrade-workers #(TARGET-A TARGET-B) LIMITS))))
  (val refused (get seen 0))
  (assert (isinstance refused UpgradeRefused) seen)
  (assert (isinstance refused.refusal CleanBootRefused) refused.refusal)
  (val log (get seen 1))
  (assert (= log.steps #((SwapStep :name "ConfirmCleanBoot" :target "a"))) log.steps)
  (assert (= log.answers #()) log.answers))


(deftest test-a-refused-coordinator-boot-root-stops-before-the-coordinator-desire
  ;; 失敗ケース(#3725): coordinator の root の準備が断られる — worker a・b の手は 5 つずつ全部出て、coordinator は確かめと準備の 2 つだけ
  ;; (DesireCoordinator・その公開・当ては出ない)。止まりは coordinator を名指す。
  (<- seen tuple (with-handlers [(state) (sim-time-handler :clock (SimClock)) (scripted-upgrade SCRIPT)
                                 (refused-boot-roots (frozenset ["coordinator"]) BootRootRefusal.PLACE-UNAVAILABLE) step-recorder]
                   (recorded-run (upgrade-cluster #(TARGET-A TARGET-B) TARGET-COORDINATOR LIMITS))))
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
  ;; 答えの枝「組んだ」と「準備済みだった」(#3725): 模擬の置き場に NEW の root が無い 1 度目は組み、a が NEW で動いている 2 度目は
  ;; 準備済みと答える(何もしない)。どちらも上げる前の版の root は置き場に在る(準備は足すだけで消さない)。入れ替えは 1 度だけ。
  (<- outside SimOutside (flux-outside))
  (<- seen tuple (sim-cluster NO-JOBS (upgrade-a-twice) :workers #(A B) :outside outside))
  (assert (= (get seen 0) #((BootRootBuilt :target "a" :seconds 0.0 :previous-root-present True)
                            (BootRootAlreadyPrepared :target "a" :previous-root-present True)))
          seen)
  (assert (= (tuple (gfor s (get seen 1) s.target)) #("a")) seen)
  (assert (= (get seen 2) NEW) seen))


(defhandler boot-roots-answered-outside-the-types
  ;; 壊した答え手(失敗ケース): root の準備に、3 つの答えの型のどれでもない値(None)で答える。
  (PrepareBootRoot [launch]
    (resume None)))


(deftest test-an-answer-outside-the-three-types-stops-before-the-desire
  ;; 失敗ケース(#3725): 答え手が 3 つの型の外の値で答える — Program はそれを「準備済み」と読んで先へ進まず、答えを受けた所で、待っていた
  ;; 型と受けた値の型を名指して落ちる(先へ進むと台本の世界は最後まで通り、落ちないので赤)。
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
  ;; 筋書きの答え手: どの物の root も「組んだ(12.5 秒)」と答え、上げる前の版の root は置き場に残っていない(戻し先が無い)と答える。
  (PrepareBootRoot [launch]
    (<- target str (launch-target launch))
    (resume (BootRootBuilt :target target :seconds 12.5 :previous-root-present False))))


(deftest test-the-workers-program-returns-each-boot-root-answer-in-swap-order
  ;; root の準備の答えは Program の結果に載る(#3725)— 戻し先の root が残っていない(previous-root-present = 偽)事と組んだ秒を、起こした
  ;; 側が台ごとに名指しで読める。答えを受けて捨てる Program では結果が None で、戻し先が無い事がどこにも出ない(直す前の形で赤)。
  (<- prepared tuple (with-handlers [(state) (sim-time-handler :clock (SimClock)) (scripted-upgrade WORKERS-ONLY-SCRIPT)
                                     boot-roots-built-without-a-previous-root]
                       (upgrade-workers #(TARGET-A TARGET-B) LIMITS)))
  (assert (= prepared #((BootRootBuilt :target "a" :seconds 12.5 :previous-root-present False)
                        (BootRootBuilt :target "b" :seconds 12.5 :previous-root-present False)))
          prepared))


(deftest test-the-cluster-program-returns-the-coordinator-boot-root-answer-last
  ;; coordinator まで上げる回の結果は、worker の分の後に coordinator の分が並ぶ(戻し先の有無を 3 つとも名指しで読める)。
  (<- prepared tuple (with-handlers [(state) (sim-time-handler :clock (SimClock)) (scripted-upgrade SCRIPT)
                                     boot-roots-built-without-a-previous-root]
                       (upgrade-cluster #(TARGET-A TARGET-B) TARGET-COORDINATOR LIMITS)))
  (assert (= (tuple (gfor p prepared #(p.target p.previous-root-present)))
             #(#("a" False) #("b" False) #("coordinator" False)))
          prepared))
