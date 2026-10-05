;; 版上げの Program(shared/core/upgrade_program.hy — #3366 の単位 3)の検。sim-cluster の上で、宣言の effect に flux-declarations(公開 =
;; 何もしない・当てる = 模擬の Flux)が答え、Program が条 V1〜V4 を自分で守る事(入れ替えの瞬間の記録で破り 0)・走り中の task の終わりを
;; 待つ事・待ちの上限を越えると名指しで落ちる事を確かめる。
;;
;; DesireWorker / DesireCoordinator に答えるのは、この検の道具 desire-by-manifest(値から manifest を launch_rules の写しで作り直して
;; 置き場へ書く)— doeff は配備する側の repo の本番の handler を import できないため。本番の handler で一周する検は
;; 配備する側の repo に置く(その repo の一周の検を、この Program に替える)。
(require doeff-hy.macros [deftest defk defhandler defeffect <- val var])
(require doeff-hy.record [defrecord])
(val MODULE-TAGS {:context "doeff-cluster-test" :role "test"})
(import collections.abc [Callable])
(import dataclasses [dataclass replace])
(import pytest)
(import doeff [with-handlers])
(import doeff_core_effects.handlers [state])
(import doeff_time [Delay SimClock sim-time-handler])
(import doeff_cluster.shared.core.detached_rules [submit-detached-task])
(import doeff_cluster.shared.intent.detached_model [AwaitDetached DetachedSucceeded])
(import doeff_cluster.shared.intent.launch_model [WorkerLaunch CoordinatorLaunch DesireWorker DesireCoordinator])
(import doeff_cluster.shared.intent.upgrade_model [UpgradeLimits UpgradeStalled UpgradeState UpgradeStart RosterEntry PendingTask PendingPhase
                                                   ReadUpgradeState PublishDeclarations ApplyDeclarations ConfirmCleanBoot
                                                   CleanBootPassed UpgradeRefused])
(import doeff_cluster.shared.intent.detached_model [AwaitRunnersChange RunnersChange])
(import doeff_cluster.shared.core.upgrade_program [upgrade-cluster upgrade-workers])
(import doeff_cluster.sim.local [sim-cluster SimWorker SimOutside WorkerOf DrainWorker ReadCoordinator])
(import doeff_cluster.sim.flux [FluxPass manifest-state prestop-drain flux-declarations refused-clean-boots UpgradeStartsSeen])
(import tests.flux_fixtures [OLD NEW NO-JOBS PATHS ON-X A B COORDINATOR-SECONDS write-manifest breaches-of flux-outside])
(import tests.detached_rig [slow-add])

;; 宣言の値(本番の DECLARED-WORKERS に当たる)— 版は NEW へ。
(val TARGET-A (WorkerLaunch :name "a" :provides #("x-tool" "host-a") :exclusive #() :capacity 1 :task-reserve 0 :doeff-commit NEW))
(val TARGET-B (WorkerLaunch :name "b" :provides #("y-tool" "host-b") :exclusive #() :capacity 1 :task-reserve 0 :doeff-commit NEW))
(val TARGET-COORDINATOR (CoordinatorLaunch :doeff-commit NEW))
(val LIMITS (UpgradeLimits :drain-seconds 120.0 :return-seconds 60.0 :queue-seconds 120.0))
;; 本番の Pod の猶予(terminationGracePeriodSeconds — agent-worker は 120 秒)が task より短い worker の代わりの猶予の秒。
(val GRACE-SECONDS 5.0)


(defhandler desire-by-manifest
  ;; 検の道具: Desire の値を覚え、worker a・b と coordinator の manifest を launch_rules の写しで作り直して置き場へ書くため(本番の
  ;; handler は行だけを書き換えるが、doeff はそれを import できない — 値 → 行の写しは同じ launch_rules)。
  (session var a-commit OLD)
  (session var b-commit OLD)
  (session var c-commit OLD)
  (DesireWorker [launch]
    (if (= launch.name "a") (:= a-commit launch.doeff-commit) (:= b-commit launch.doeff-commit))
    (<- (write-manifest a-commit b-commit c-commit))
    (resume #()))
  (DesireCoordinator [launch]
    (:= c-commit launch.doeff-commit)
    (<- (write-manifest a-commit b-commit c-commit))
    (resume #())))


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
  "筋書き: 宣言を読み、Program で a・b・coordinator を NEW へ上げ、入れ替えの瞬間の記録を条 V1〜V4 で判じる。答え = #(破りの条の名 a の版 b の版 記録)。"
  (<- (Delay 3.0))
  (<- applied tuple (manifest-state PATHS))
  (<- starts tuple (with-handlers [(flux-declarations PATHS drain COORDINATOR-SECONDS applied) desire-by-manifest]
                     (upgrade-then-starts limits)))
  (<- rules tuple (breaches-of starts))
  (<- a SimWorker (WorkerOf "a"))
  (<- b SimWorker (WorkerOf "b"))
  #(rules a.doeff-commit b.doeff-commit starts))


(defk upgrade-then-starts [limits]
  {:pre [(: limits UpgradeLimits)] :post [(: % tuple)] :tags {:context "doeff-cluster-test" :role "program"}}
  "Program を走らせてから、模擬の Flux が当てた入れ替えの瞬間の記録を読む。"
  (<- (upgrade-cluster #(TARGET-A TARGET-B) TARGET-COORDINATOR limits))
  (<- starts tuple (UpgradeStartsSeen))
  starts)


(deftest test-the-upgrade-program-keeps-v1-to-v4
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


(defk upgrade-with-refused-boots [targets]
  {:pre [(: targets frozenset)] :post [(: % tuple)] :tags {:context "doeff-cluster-test" :role "program"}}
  "筋書き(2026-10-05 07:1x の形 — 起動の時に読む物が壊れていて、空の Pod が起動で落ちる版): targets に名の在る入れ替え先の空の起動が
   落ちる世界で Program を走らせる。答え = #(止まりの例外 止まった後の宣言 入れ替えの記録 a の版 b の版)。"
  (<- (Delay 3.0))
  (<- applied tuple (manifest-state PATHS))
  (<- run tuple (with-handlers [(flux-declarations PATHS prestop-drain COORDINATOR-SECONDS applied) (refused-clean-boots targets)
                                desire-by-manifest]
                  (refusal-then-starts)))
  (<- after tuple (manifest-state PATHS))
  (<- a SimWorker (WorkerOf "a"))
  (<- b SimWorker (WorkerOf "b"))
  #((get run 0) #(applied after) (get run 1) a.doeff-commit b.doeff-commit))


(defk refusal-then-starts []
  {:pre [] :post [(: % tuple)] :tags {:context "doeff-cluster-test" :role "program"}}
  "Program を走らせて空の起動の断りによる止まり(UpgradeRefused)を受け、模擬の Flux が当てた入れ替えの記録を読む。答え = #(止まり 記録)。"
  (var refused None)
  (try
    (<- (upgrade-cluster #(TARGET-A TARGET-B) TARGET-COORDINATOR LIMITS))
    (except [e UpgradeRefused]
      (:= refused e)))
  (<- starts tuple (UpgradeStartsSeen))
  #(refused starts))


(deftest test-a-worker-whose-clean-boot-fails-stops-the-upgrade-before-anything-is-written
  ;; 失敗ケース(Mac の調整役の条件 3・今朝の形): 最初の worker a の入れ替え先の空の起動が落ちる — Program は a を名指して止まり、宣言を
  ;; 書かず(置き場は始めと同じ)、何も入れ替えず、a・b は元の版のまま。
  (<- outside SimOutside (flux-outside))
  (<- seen tuple (sim-cluster NO-JOBS (upgrade-with-refused-boots (frozenset ["a"])) :workers #(A B) :outside outside))
  (val refused (get seen 0))
  (assert (isinstance refused UpgradeRefused) seen)
  (assert (= refused.target "a") refused)
  (val manifests (get seen 1))
  (assert (= (get manifests 0) (get manifests 1)) seen)
  (assert (= (get seen 2) #()) seen)
  (assert (= #((get seen 3) (get seen 4)) #(OLD OLD)) seen))


(deftest test-a-coordinator-whose-clean-boot-fails-stops-after-the-workers-and-before-its-swap
  ;; 失敗ケース: coordinator の入れ替え先の空の起動が落ちる — worker a・b は入れ替わり(新しい版)、coordinator の宣言は書かれず
  ;; 入れ替えもしない(入れ替えの記録は a・b だけ)。止まりは coordinator を名指す。
  (<- outside SimOutside (flux-outside))
  (<- seen tuple (sim-cluster NO-JOBS (upgrade-with-refused-boots (frozenset ["coordinator"])) :workers #(A B) :outside outside))
  (val refused (get seen 0))
  (assert (isinstance refused UpgradeRefused) seen)
  (assert (= refused.target "coordinator") refused)
  (assert (= (tuple (gfor s (get seen 2) s.target)) #("a" "b")) seen)
  (assert (= #((get seen 3) (get seen 4)) #(NEW NEW)) seen))


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
  ;; 名簿と task の読みを台本の順に返し、版の変化の待ちはすぐ返し、宣言の effect は覚えるだけにするため。
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
