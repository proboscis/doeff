;; 入れ替えの順の条 V1〜V5(coordinator/core/upgrade_invariants.hy — #3366・V5 は #3725)の検。記録は入れ替えを始めた瞬間の写しの合成の
;; 列で、2026-10-05 の版上げ(#3156)で通した順は緑、破る順は条の名で赤になる事を確かめる。条の中身(何が落ちるか)は sim で測った物
;; (tests/test_upgrade_swaps.hy)。模擬の Flux が当てた瞬間に同じ記録を写す筋書きは単位 2b の続き。
;; V5 の失敗ケースは合成の列に加えて、模擬の世界(模擬の Flux と模擬の置き場 — sim/flux.hy の flux-declarations)で、準備の前に Desire を
;; 出す壊した Program と、組まずに「組んだ」と答える壊した答え手を走らせ、入れ替えの瞬間の置き場の写しで赤になる事を見る。
(require doeff-hy.macros [deftest defk defhandler <- val])
(val MODULE-TAGS {:context "doeff-cluster-test" :role "test"})
(import doeff [with-handlers Program])
(import doeff_time [Delay])
(import doeff_cluster.shared.intent.launch_model [WorkerLaunch DesireWorker])
(import doeff_cluster.shared.intent.upgrade_model [UpgradeKind PendingPhase RosterEntry PendingTask UpgradeStart BootRootsAtStart
                                                   UpgradeLimits PublishDeclarations ApplyDeclarations PrepareBootRoot BootRootBuilt])
(import doeff_cluster.shared.core.upgrade_program [upgrade-workers])
(import doeff_cluster.coordinator.core.upgrade_invariants [coordinator-after-every-worker worker-swap-waits-for-its-tasks
                                                            one-worker-at-a-time coordinator-swap-on-an-empty-queue
                                                            swap-after-boot-root-prepared])
(import doeff_cluster.sim.local [sim-cluster SimOutside])
(import doeff_cluster.sim.flux [manifest-state prestop-drain flux-declarations launch-target BootRootsAtStartsSeen])
(import tests.flux_fixtures [NO-JOBS PATHS A B COORDINATOR-SECONDS await-back flux-outside desire-by-manifest])

(val OLD "d563ab95a0000000000000000000000000000000")
(val NEW "90fd9a81d97ddf1cf5ae13a4036fa615108abbe7")
(val JUDGES #(coordinator-after-every-worker worker-swap-waits-for-its-tasks one-worker-at-a-time coordinator-swap-on-an-empty-queue))


(val QUEUED (PendingTask :task "t1" :phase PendingPhase.QUEUED :worker None))
(val ON-VERIFY-2 (PendingTask :task "t2" :phase PendingPhase.ASSIGNED :worker "verify-2"))
(val ON-VERIFY-1 (PendingTask :task "t3" :phase PendingPhase.ASSIGNED :worker "verify-1"))


(deftest test-the-order-the-2026-10-05-upgrade-used-is-green
  ;; 1 台ずつ・前の 1 台が新しい版で live に戻ってから次・走り中の task の在る worker は空いてから・queued が在っても worker は入れ替えて
  ;; よい(落ちない)・coordinator は待ち行列が空の時に最後。
  (val starts
    #((UpgradeStart :at-ms 1 :kind UpgradeKind.WORKER :target "agent-2" :doeff-commit NEW :tasks #(QUEUED ON-VERIFY-2)
                    :roster #((RosterEntry :worker "agent-2" :live True :doeff-commit OLD)
                              (RosterEntry :worker "verify-1" :live True :doeff-commit OLD)
                              (RosterEntry :worker "verify-2" :live True :doeff-commit OLD)))
      (UpgradeStart :at-ms 2 :kind UpgradeKind.WORKER :target "verify-1" :doeff-commit NEW :tasks #(QUEUED ON-VERIFY-2)
                    :roster #((RosterEntry :worker "agent-2" :live True :doeff-commit NEW)
                              (RosterEntry :worker "verify-1" :live True :doeff-commit OLD)
                              (RosterEntry :worker "verify-2" :live True :doeff-commit OLD)))
      (UpgradeStart :at-ms 3 :kind UpgradeKind.WORKER :target "verify-2" :doeff-commit NEW :tasks #(ON-VERIFY-1)
                    :roster #((RosterEntry :worker "agent-2" :live True :doeff-commit NEW)
                              (RosterEntry :worker "verify-1" :live True :doeff-commit NEW)
                              (RosterEntry :worker "verify-2" :live True :doeff-commit OLD)))
      (UpgradeStart :at-ms 4 :kind UpgradeKind.COORDINATOR :target "coordinator" :doeff-commit NEW :tasks #(ON-VERIFY-1)
                    :roster #((RosterEntry :worker "agent-2" :live True :doeff-commit NEW)
                              (RosterEntry :worker "verify-1" :live True :doeff-commit NEW)
                              (RosterEntry :worker "verify-2" :live True :doeff-commit NEW)))))
  (for [judge JUDGES]
    (<- found tuple (judge starts))
    (assert (= found #()) found)))


(deftest test-swapping-a-worker-with-a-running-task-breaks-v2
  ;; 失敗ケース(sim の測りの (b)): 入れ替える worker の上で task が走っている(drain の空くのを待っていない)。
  (val start (UpgradeStart :at-ms 5 :kind UpgradeKind.WORKER :target "verify-1" :doeff-commit NEW :tasks #(ON-VERIFY-1 QUEUED)
                           :roster #((RosterEntry :worker "verify-1" :live True :doeff-commit OLD)
                                     (RosterEntry :worker "verify-2" :live True :doeff-commit OLD))))
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
                           :roster #((RosterEntry :worker "agent-2" :live True :doeff-commit OLD)
                                     (RosterEntry :worker "verify-1" :live True :doeff-commit OLD))))
  (for [#(what entry) #(#("まだ live でない" (RosterEntry :worker "agent-2" :live False :doeff-commit NEW))
                        #("live だが古い版のまま" (RosterEntry :worker "agent-2" :live True :doeff-commit OLD)))]
    (val second (UpgradeStart :at-ms 2 :kind UpgradeKind.WORKER :target "verify-1" :doeff-commit NEW :tasks #()
                              :roster #(entry (RosterEntry :worker "verify-1" :live True :doeff-commit OLD))))
    (<- found tuple (one-worker-at-a-time #(second first)))
    (assert (= (tuple (gfor b found #(b.rule b.target))) #(#("V3 one-worker-at-a-time" "verify-1"))) #(what found))))


(deftest test-starting-the-coordinator-before-every-worker-is-new-breaks-v1
  ;; 失敗ケース: worker が全部新しい版で live になる前に coordinator の入れ替えを始める。
  (val start (UpgradeStart :at-ms 9 :kind UpgradeKind.COORDINATOR :target "coordinator" :doeff-commit NEW :tasks #()
                           :roster #((RosterEntry :worker "agent-2" :live True :doeff-commit NEW)
                                     (RosterEntry :worker "verify-1" :live True :doeff-commit OLD)
                                     (RosterEntry :worker "verify-2" :live False :doeff-commit NEW))))
  (<- found tuple (coordinator-after-every-worker #(start)))
  (assert (= (sorted (gfor b found b.rule)) ["V1 coordinator-after-every-worker" "V1 coordinator-after-every-worker"]) found)
  (assert (all (gfor b found (= b.target "coordinator")))))


(deftest test-starting-the-coordinator-with-a-queued-task-breaks-v4
  ;; 失敗ケース(sim の測りの (c)): 待ち行列に task が在る時に coordinator の入れ替えを始める。走り中の task は数えない(残る)。
  (val roster #((RosterEntry :worker "verify-1" :live True :doeff-commit NEW)))
  (<- found tuple (coordinator-swap-on-an-empty-queue
                    #((UpgradeStart :at-ms 9 :kind UpgradeKind.COORDINATOR :target "coordinator" :doeff-commit NEW
                                    :tasks #(QUEUED ON-VERIFY-1) :roster roster))))
  (assert (= (tuple (gfor b found #(b.rule b.detail))) #(#("V4 coordinator-swap-on-an-empty-queue" "task t1 が queued のまま"))) found))


;; --- 条 V5(#3725): 入れ替えを始めた瞬間の置き場に、入れ替え先の版の自己起動の root が準備済みで在る -------------------------------

(val AGENT-2-TO-NEW (UpgradeStart :at-ms 7 :kind UpgradeKind.WORKER :target "agent-2" :doeff-commit NEW :tasks #()
                                  :roster #((RosterEntry :worker "agent-2" :live True :doeff-commit OLD))))


(deftest test-a-swap-whose-boot-root-is-on-the-place-is-green-for-v5
  ;; 入れ替えの瞬間に、置き場に入れ替え先の版の root が在る(上げる前の版の root も残っている — 準備は足すだけ)。
  (<- found tuple (swap-after-boot-root-prepared #((BootRootsAtStart :start AGENT-2-TO-NEW :prepared #(OLD NEW)))))
  (assert (= found #()) found))


(deftest test-swapping-before-the-boot-root-is-prepared-breaks-v5
  ;; 失敗ケース(合成の列): 入れ替えの瞬間の置き場に在るのが上げる前の版の root だけ / root が 1 つも無い — 作り直した process が起動の
  ;; 中で root を準備する間、その上の service に届かない順。
  (for [#(what prepared) #(#("上げる前の版の root だけ" #(OLD)) #("root が無い" #()))]
    (<- found tuple (swap-after-boot-root-prepared #((BootRootsAtStart :start AGENT-2-TO-NEW :prepared prepared))))
    (assert (= (tuple (gfor b found #(b.rule b.target b.at-ms))) #(#("V5 swap-after-boot-root-prepared" "agent-2" 7))) #(what found))))


;; 模擬の世界の worker a(tests/flux_fixtures.hy の A — 旧い版で動く)を NEW へ上げる値と、待ちの上限。
(val TARGET-A (WorkerLaunch :name "a" :provides #("x-tool" "host-a") :exclusive #() :capacity 1 :task-reserve 0 :doeff-commit NEW))
(val LIMITS (UpgradeLimits :drain-seconds 120.0 :return-seconds 60.0 :queue-seconds 120.0))


(defk then-places [program]
  {:pre [(: program Program)] :post [(: % tuple)] :tags {:context "doeff-cluster-test" :role "program"}}
  "program を走らせてから、模擬の置き場が残した入れ替えの瞬間の写し(条 V5 の入力)を読むため。"
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
  "筋書き: 模擬の世界で、準備の前に Desire を出す壊した Program を走らせる。答え = 入れ替えの瞬間の置き場の写し。"
  (<- (Delay 3.0))
  (<- applied tuple (manifest-state PATHS))
  (<- places tuple (with-handlers [(flux-declarations PATHS prestop-drain COORDINATOR-SECONDS applied) desire-by-manifest]
                     (then-places (desire-before-the-boot-root))))
  places)


(deftest test-a-program-that-desires-before-the-boot-root-is-prepared-breaks-v5
  ;; 失敗ケース(壊した Program): 準備より先に宣言を書いて当てると、a の古い process が止まる瞬間の置き場に在るのは上げる前の版の root
  ;; だけ — V5 が a を名指す(後から準備しても、入れ替えの瞬間の写しは変わらない)。
  (<- outside SimOutside (flux-outside))
  (<- places tuple (sim-cluster NO-JOBS (broken-order-on-sim) :workers #(A B) :outside outside))
  (assert (= (tuple (gfor p places #(p.start.target p.prepared))) #(#("a" #(OLD)))) places)
  (<- found tuple (swap-after-boot-root-prepared places))
  (assert (= (tuple (gfor b found #(b.rule b.target))) #(#("V5 swap-after-boot-root-prepared" "a"))) found))


(defhandler boot-roots-claimed-without-building
  ;; 壊した答え手(失敗ケース): 置き場に root を組まず、完成の印も確かめずに「組んだ」と答える(模擬の置き場には何も足されない)。
  (PrepareBootRoot [launch]
    (<- target str (launch-target launch))
    (resume (BootRootBuilt :target target :seconds 0.0 :previous-root-present True))))


(defk broken-answer-on-sim []
  {:pre [] :post [(: % tuple)] :tags {:context "doeff-cluster-test" :role "program"}}
  "筋書き: 模擬の世界で、組まずに「組んだ」と答える壊した答え手の下で、版上げの Program(本物)に a を上げさせる。
   答え = 入れ替えの瞬間の置き場の写し。"
  (<- (Delay 3.0))
  (<- applied tuple (manifest-state PATHS))
  (<- places tuple (with-handlers [(flux-declarations PATHS prestop-drain COORDINATOR-SECONDS applied)
                                   boot-roots-claimed-without-building desire-by-manifest]
                     (then-places (upgrade-workers #(TARGET-A) LIMITS))))
  places)


(deftest test-an-answer-that-claims-a-boot-root-it-did-not-build-breaks-v5
  ;; 失敗ケース(壊した答え手): Program は準備の答えを受けてから宣言を書くが、答え手が組んでいないので、入れ替えの瞬間の置き場に
  ;; 入れ替え先の版の root が無い — V5 が a を名指す(Program の順だけでは守れず、答え手が完成の印まで確かめて答える事が要る)。
  (<- outside SimOutside (flux-outside))
  (<- places tuple (sim-cluster NO-JOBS (broken-answer-on-sim) :workers #(A B) :outside outside))
  (<- found tuple (swap-after-boot-root-prepared places))
  (assert (= (tuple (gfor b found #(b.rule b.target))) #(#("V5 swap-after-boot-root-prepared" "a"))) found))
