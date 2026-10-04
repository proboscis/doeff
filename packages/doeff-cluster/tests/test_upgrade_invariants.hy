;; 入れ替えの順の条 V1〜V4(coordinator/core/upgrade_invariants.hy — #3366)の検。記録は入れ替えを始めた瞬間の写しの合成の列で、
;; 2026-10-05 の版上げ(#3156)で通した順は緑、破る順は条の名で赤になる事を確かめる。条の中身(何が落ちるか)は sim で測った物
;; (tests/test_upgrade_swaps.hy)。模擬の Flux が当てた瞬間に同じ記録を写す筋書きは単位 2b の続き。
(require doeff-hy.macros [deftest <- val])
(val MODULE-TAGS {:context "doeff-cluster-test" :role "test"})
(import doeff_cluster.coordinator.core.upgrade_invariants [UpgradeKind PendingPhase RosterEntry PendingTask UpgradeStart
                                                            coordinator-after-every-worker worker-swap-waits-for-its-tasks
                                                            one-worker-at-a-time coordinator-swap-on-an-empty-queue])

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
