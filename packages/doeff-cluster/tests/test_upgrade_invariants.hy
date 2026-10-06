;; 入れ替えの順の条 V1〜V5(coordinator/core/upgrade_invariants.hy — #3366・V5 は #3725)のテスト。記録は入れ替えを始めた瞬間のスナップショットを合成した
;; 列で、2026-10-05 の版上げ(#3156)で通した順は緑、違反する順は条の名で赤になる事を確かめる。条の中身(何が落ちるか)は sim で測った物
;; (tests/test_upgrade_swaps.hy)。模擬の Flux が当てた瞬間に同じ記録を写す筋書きは単位 2b の続き。
;; V5 の失敗ケースは合成の列に加えて、模擬の世界(模擬の Flux と模擬の保存先 — sim/flux.hy の flux-declarations)で、準備の前に Desire を
;; 出す壊した Program と、組まずに「組んだ」と答える壊した handler を走らせ、入れ替えの瞬間の保存先のスナップショットで赤になる事を見る。
(require doeff-hy.macros [deftest defk defhandler <- val])
(val MODULE-TAGS {:context "doeff-cluster-test" :role "test"})
(import functools [partial])
(import doeff [with-handlers Program])
(import doeff_time [Delay])
(import doeff_cluster.shared.intent.launch_model [WorkerLaunch DesireWorker])
(import doeff_cluster.shared.intent.upgrade_model [UpgradeKind PendingPhase WorkerDeclaration RosterEntry PendingTask UpgradeStart
                                                   BootRootsAtStart VerifiedVersions UpgradeLimits PublishDeclarations ApplyDeclarations
                                                   PrepareBootRoot BootRootBuilt])
(import doeff_cluster.shared.core.upgrade_program [upgrade-workers])
(import doeff_cluster.coordinator.core.upgrade_invariants [coordinator-after-every-worker worker-swap-waits-for-its-tasks
                                                            one-worker-at-a-time coordinator-swap-on-an-empty-queue
                                                            swap-after-boot-root-prepared])
(import doeff_cluster.sim.local [sim-cluster SimOutside])
(import doeff_cluster.sim.flux [manifest-state prestop-drain flux-declarations launch-target BootRootsAtStartsSeen])
(import tests.flux_fixtures [NO-JOBS PATHS A B COORDINATOR-SECONDS await-back flux-outside desire-by-manifest])

(val OLD "d563ab95a0000000000000000000000000000000")
(val NEW "90fd9a81d97ddf1cf5ae13a4036fa615108abbe7")
(val IN WorkerDeclaration.DECLARED)
(val OUT WorkerDeclaration.UNDECLARED)
;; worker と coordinator を同じ版 NEW にそろえる時の確かめた版の組み合わせ(2026-10-05 の版上げの形)。
(val SAME-VERSION (VerifiedVersions :coordinator NEW :workers (frozenset [NEW])))
(val JUDGES #((partial coordinator-after-every-worker :verified SAME-VERSION) worker-swap-waits-for-its-tasks one-worker-at-a-time
              coordinator-swap-on-an-empty-queue))


(val QUEUED (PendingTask :task "t1" :phase PendingPhase.QUEUED :worker None))
(val ON-VERIFY-2 (PendingTask :task "t2" :phase PendingPhase.ASSIGNED :worker "verify-2"))
(val ON-VERIFY-1 (PendingTask :task "t3" :phase PendingPhase.ASSIGNED :worker "verify-1"))


(deftest test-the-order-the-2026-10-05-upgrade-used-is-green
  ;; 1 台ずつ・前の 1 台が新しい版で live に戻ってから次・走り中の task の在る worker は空いてから・queued が在っても worker は入れ替えて
  ;; よい(落ちない)・coordinator は待ち行列が空の時に最後。
  (val starts
    #((UpgradeStart :at-ms 1 :kind UpgradeKind.WORKER :target "agent-2" :doeff-commit NEW :tasks #(QUEUED ON-VERIFY-2)
                    :roster #((RosterEntry :worker "agent-2" :live True :doeff-commit OLD :declaration IN)
                              (RosterEntry :worker "verify-1" :live True :doeff-commit OLD :declaration IN)
                              (RosterEntry :worker "verify-2" :live True :doeff-commit OLD :declaration IN)))
      (UpgradeStart :at-ms 2 :kind UpgradeKind.WORKER :target "verify-1" :doeff-commit NEW :tasks #(QUEUED ON-VERIFY-2)
                    :roster #((RosterEntry :worker "agent-2" :live True :doeff-commit NEW :declaration IN)
                              (RosterEntry :worker "verify-1" :live True :doeff-commit OLD :declaration IN)
                              (RosterEntry :worker "verify-2" :live True :doeff-commit OLD :declaration IN)))
      (UpgradeStart :at-ms 3 :kind UpgradeKind.WORKER :target "verify-2" :doeff-commit NEW :tasks #(ON-VERIFY-1)
                    :roster #((RosterEntry :worker "agent-2" :live True :doeff-commit NEW :declaration IN)
                              (RosterEntry :worker "verify-1" :live True :doeff-commit NEW :declaration IN)
                              (RosterEntry :worker "verify-2" :live True :doeff-commit OLD :declaration IN)))
      (UpgradeStart :at-ms 4 :kind UpgradeKind.COORDINATOR :target "coordinator" :doeff-commit NEW :tasks #(ON-VERIFY-1)
                    :roster #((RosterEntry :worker "agent-2" :live True :doeff-commit NEW :declaration IN)
                              (RosterEntry :worker "verify-1" :live True :doeff-commit NEW :declaration IN)
                              (RosterEntry :worker "verify-2" :live True :doeff-commit NEW :declaration IN)))))
  (for [judge JUDGES]
    (<- found tuple (judge starts))
    (assert (= found #()) found)))


(deftest test-swapping-a-worker-with-a-running-task-breaks-v2
  ;; 失敗ケース(sim の測りの (b)): 入れ替える worker の上で task が走っている(drain の空くのを待っていない)。
  (val start (UpgradeStart :at-ms 5 :kind UpgradeKind.WORKER :target "verify-1" :doeff-commit NEW :tasks #(ON-VERIFY-1 QUEUED)
                           :roster #((RosterEntry :worker "verify-1" :live True :doeff-commit OLD :declaration IN)
                                     (RosterEntry :worker "verify-2" :live True :doeff-commit OLD :declaration IN))))
  (<- found tuple (worker-swap-waits-for-its-tasks #(start)))
  (assert (= (tuple (gfor b found #(b.rule b.target))) #(#("V2 worker-swap-waits-for-its-tasks" "verify-1"))) found)
  ;; queued と、別の worker に置かれた task は数えない(入れ替えで落ちない — sim の測りの (a))。
  (<- other tuple (worker-swap-waits-for-its-tasks
                    #((UpgradeStart :at-ms 5 :kind UpgradeKind.WORKER :target "verify-1" :doeff-commit NEW
                                    :tasks #(QUEUED ON-VERIFY-2) :roster start.roster))))
  (assert (= other #()) other))


(deftest test-starting-the-next-worker-before-the-previous-is-back-breaks-v3
  ;; 失敗ケース(cisco-c8 の条件 3): 前の 1 台が新しい版で live に戻ったのを読まずに次を始める — 戻りが来ない間は次へ進まない。
  (val first (UpgradeStart :at-ms 1 :kind UpgradeKind.WORKER :target "agent-2" :doeff-commit NEW :tasks #()
                           :roster #((RosterEntry :worker "agent-2" :live True :doeff-commit OLD :declaration IN)
                                     (RosterEntry :worker "verify-1" :live True :doeff-commit OLD :declaration IN))))
  (for [#(what entry) #(#("まだ live でない" (RosterEntry :worker "agent-2" :live False :doeff-commit NEW :declaration IN))
                        #("live だが古い版のまま" (RosterEntry :worker "agent-2" :live True :doeff-commit OLD :declaration IN)))]
    (val second (UpgradeStart :at-ms 2 :kind UpgradeKind.WORKER :target "verify-1" :doeff-commit NEW :tasks #()
                              :roster #(entry (RosterEntry :worker "verify-1" :live True :doeff-commit OLD :declaration IN))))
    (<- found tuple (one-worker-at-a-time #(second first)))
    (assert (= (tuple (gfor b found #(b.rule b.target))) #(#("V3 one-worker-at-a-time" "verify-1"))) #(what found))))


(deftest test-starting-the-coordinator-before-every-worker-is-new-breaks-v1
  ;; 失敗ケース: worker が全部新しい版で live になる前に coordinator の入れ替えを始める。
  (val start (UpgradeStart :at-ms 9 :kind UpgradeKind.COORDINATOR :target "coordinator" :doeff-commit NEW :tasks #()
                           :roster #((RosterEntry :worker "agent-2" :live True :doeff-commit NEW :declaration IN)
                                     (RosterEntry :worker "verify-1" :live True :doeff-commit OLD :declaration IN)
                                     (RosterEntry :worker "verify-2" :live False :doeff-commit NEW :declaration IN))))
  (<- found tuple (coordinator-after-every-worker #(start) SAME-VERSION))
  (assert (= (sorted (gfor b found b.rule)) ["V1 coordinator-after-every-worker" "V1 coordinator-after-every-worker"]) found)
  (assert (all (gfor b found (= b.target "coordinator")))))


;; --- 条 V1 は確かめた版の組み合わせで照らす(#3772)— 順は変更ごとに決まる ---------------------------------------------------------------

(val MID "5a1b2c3d4e5f60718293a4b5c6d7e8f901234567")
;; coordinator NEW と組めると確かめた worker の版 = NEW と MID(2026-10-06 の形 — worker の版が混ざったまま coordinator だけを上げる)。
(val VERIFIED (VerifiedVersions :coordinator NEW :workers (frozenset [NEW MID])))


(deftest test-mixed-worker-versions-inside-the-verified-pair-keep-v1
  ;; 宣言の内の worker の版が NEW と MID で混ざっていても、どちらも確かめた組み合わせに入っていれば V1 は緑。宣言の外の worker(古い版・live で
  ;; ない)は数えない。同じ記録を「同じ版」の組み合わせで照らすと、MID の worker と宣言の外の worker を挙げずに MID だけが赤。
  (val start (UpgradeStart :at-ms 9 :kind UpgradeKind.COORDINATOR :target "coordinator" :doeff-commit NEW :tasks #()
                           :roster #((RosterEntry :worker "agent-2" :live True :doeff-commit NEW :declaration IN)
                                     (RosterEntry :worker "verify-1" :live True :doeff-commit MID :declaration IN)
                                     (RosterEntry :worker "host-20" :live False :doeff-commit OLD :declaration OUT))))
  (<- found tuple (coordinator-after-every-worker #(start) VERIFIED))
  (assert (= found #()) found)
  (<- same tuple (coordinator-after-every-worker #(start) SAME-VERSION))
  (assert (= (len same) 1) same)
  (assert (in "verify-1" (. (get same 0) detail)) same))


(deftest test-a-worker-version-outside-the-verified-pair-breaks-v1
  ;; 失敗ケース: 宣言の内の worker が確かめた組み合わせに無い版(OLD)で動いている時に coordinator の入れ替えを始める。入れ替え先の版が組み合わせの
  ;; coordinator の版でない時も V1 の違反(組み合わせがその coordinator の物でない)。
  (val roster #((RosterEntry :worker "agent-2" :live True :doeff-commit NEW :declaration IN)
                (RosterEntry :worker "verify-1" :live True :doeff-commit OLD :declaration IN)))
  (<- found tuple (coordinator-after-every-worker
                    #((UpgradeStart :at-ms 9 :kind UpgradeKind.COORDINATOR :target "coordinator" :doeff-commit NEW :tasks #() :roster roster))
                    VERIFIED))
  (assert (= (tuple (gfor b found b.rule)) #("V1 coordinator-after-every-worker")) found)
  (assert (in "verify-1" (. (get found 0) detail)) found)
  (<- other tuple (coordinator-after-every-worker
                    #((UpgradeStart :at-ms 9 :kind UpgradeKind.COORDINATOR :target "coordinator" :doeff-commit MID :tasks #()
                                    :roster (cut roster 0 1)))
                    VERIFIED))
  (assert (= (len other) 1) other)
  (assert (in "確かめた版の組み合わせの coordinator の版" (. (get other 0) detail)) other))


(deftest test-starting-the-coordinator-with-a-queued-task-breaks-v4
  ;; 失敗ケース(sim の測りの (c)): 待ち行列に task が在る時に coordinator の入れ替えを始める。走り中の task は数えない(残る)。
  (val roster #((RosterEntry :worker "verify-1" :live True :doeff-commit NEW :declaration IN)))
  (<- found tuple (coordinator-swap-on-an-empty-queue
                    #((UpgradeStart :at-ms 9 :kind UpgradeKind.COORDINATOR :target "coordinator" :doeff-commit NEW
                                    :tasks #(QUEUED ON-VERIFY-1) :roster roster))))
  (assert (= (tuple (gfor b found #(b.rule b.detail))) #(#("V4 coordinator-swap-on-an-empty-queue" "task t1 が queued のまま"))) found))


;; --- 条 V5(#3725): 入れ替えを始めた瞬間の保存先に、入れ替え先の版の自己起動の root が準備済みで在る -------------------------------

(val AGENT-2-TO-NEW (UpgradeStart :at-ms 7 :kind UpgradeKind.WORKER :target "agent-2" :doeff-commit NEW :tasks #()
                                  :roster #((RosterEntry :worker "agent-2" :live True :doeff-commit OLD :declaration IN))))


(deftest test-a-swap-whose-boot-root-is-on-the-place-is-green-for-v5
  ;; 入れ替えの瞬間に、保存先に入れ替え先の版の root が在る(上げる前の版の root も残っている — 準備は足すだけ)。
  (<- found tuple (swap-after-boot-root-prepared #((BootRootsAtStart :start AGENT-2-TO-NEW :prepared #(OLD NEW)))))
  (assert (= found #()) found))


(deftest test-swapping-before-the-boot-root-is-prepared-breaks-v5
  ;; 失敗ケース(合成の列): 入れ替えの瞬間の保存先に在るのが上げる前の版の root だけ / root が 1 つも無い — 作り直した process が起動の
  ;; 中で root を準備する間、その上の service に届かない順。
  (for [#(what prepared) #(#("上げる前の版の root だけ" #(OLD)) #("root が無い" #()))]
    (<- found tuple (swap-after-boot-root-prepared #((BootRootsAtStart :start AGENT-2-TO-NEW :prepared prepared))))
    (assert (= (tuple (gfor b found #(b.rule b.target b.at-ms))) #(#("V5 swap-after-boot-root-prepared" "agent-2" 7))) #(what found))))


;; 模擬の世界の worker a(tests/flux_fixtures.hy の A — 旧い版で動く)を NEW へ上げる値と、待ちの上限。
(val TARGET-A (WorkerLaunch :name "a" :provides #("x-tool" "host-a") :exclusive #() :capacity 1 :task-reserve 0 :doeff-commit NEW))
(val LIMITS (UpgradeLimits :drain-seconds 120.0 :return-seconds 60.0 :queue-seconds 120.0 :quiet-seconds 60.0))


(defk then-places [program]
  {:pre [(: program Program)] :post [(: % tuple)] :tags {:context "doeff-cluster-test" :role "program"}}
  "program を走らせてから、模擬の保存先が残した入れ替えの瞬間のスナップショット(条 V5 の入力)を読むため。"
  (<- program)
  (<- places tuple (BootRootsAtStartsSeen))
  places)


(defk desire-before-the-boot-root []
  {:pre [] :post [(: % None)] :tags {:context "doeff-cluster-test" :role "program"}}
  "壊した Program(失敗ケース — 準備の前に Desire を出す順): a の宣言を書いて公開して当て、a が戻ってから root を準備する。"
  (<- (DesireWorker TARGET-A))
  (<- (PublishDeclarations))
  (<- (ApplyDeclarations))
  (<- (await-back "a" NEW))
  (<- (PrepareBootRoot TARGET-A))
  None)


(defk broken-order-on-sim []
  {:pre [] :post [(: % tuple)] :tags {:context "doeff-cluster-test" :role "program"}}
  "筋書き: 模擬の世界で、準備の前に Desire を出す壊した Program を走らせる。答え = 入れ替えの瞬間の保存先のスナップショット。"
  (<- (Delay 3.0))
  (<- applied tuple (manifest-state PATHS))
  (<- places tuple (with-handlers [(flux-declarations PATHS prestop-drain COORDINATOR-SECONDS applied) desire-by-manifest]
                     (then-places (desire-before-the-boot-root))))
  places)


(deftest test-a-program-that-desires-before-the-boot-root-is-prepared-breaks-v5
  ;; 失敗ケース(壊した Program): 準備より先に宣言を書いて当てると、a の古い process が止まる瞬間の保存先に在るのは上げる前の版の root
  ;; だけ — V5 が a を違反として挙げる(後から準備しても、入れ替えの瞬間のスナップショットは変わらない)。
  (<- outside SimOutside (flux-outside))
  (<- places tuple (sim-cluster NO-JOBS (broken-order-on-sim) :workers #(A B) :outside outside))
  (assert (= (tuple (gfor p places #(p.start.target p.prepared))) #(#("a" #(OLD)))) places)
  (<- found tuple (swap-after-boot-root-prepared places))
  (assert (= (tuple (gfor b found #(b.rule b.target))) #(#("V5 swap-after-boot-root-prepared" "a"))) found))


(defhandler boot-roots-claimed-without-building
  ;; 壊した handler(失敗ケース): 保存先に root を組まず、完成のマークも確かめずに「組んだ」と答える(模擬の保存先には何も足されない)。
  (PrepareBootRoot [launch]
    (<- target str (launch-target launch))
    (resume (BootRootBuilt :target target :seconds 0.0 :previous-root-present True))))


(defk broken-answer-on-sim []
  {:pre [] :post [(: % tuple)] :tags {:context "doeff-cluster-test" :role "program"}}
  "筋書き: 模擬の世界で、組まずに「組んだ」と答える壊した handler の下で、版上げの Program(本物)に a を上げさせる。
   答え = 入れ替えの瞬間の保存先のスナップショット。"
  (<- (Delay 3.0))
  (<- applied tuple (manifest-state PATHS))
  (<- places tuple (with-handlers [(flux-declarations PATHS prestop-drain COORDINATOR-SECONDS applied)
                                   boot-roots-claimed-without-building desire-by-manifest]
                     (then-places (upgrade-workers #(TARGET-A) LIMITS))))
  places)


(deftest test-an-answer-that-claims-a-boot-root-it-did-not-build-breaks-v5
  ;; 失敗ケース(壊した handler): Program は準備の答えを受けてから宣言を書くが、handler が組んでいないので、入れ替えの瞬間の保存先に
  ;; 入れ替え先の版の root が無い — V5 が a を違反として挙げる(Program の順だけでは守れず、handler が完成のマークまで確かめて答える事が要る)。
  (<- outside SimOutside (flux-outside))
  (<- places tuple (sim-cluster NO-JOBS (broken-answer-on-sim) :workers #(A B) :outside outside))
  (<- found tuple (swap-after-boot-root-prepared places))
  (assert (= (tuple (gfor b found #(b.rule b.target))) #(#("V5 swap-after-boot-root-prepared" "a"))) found))
