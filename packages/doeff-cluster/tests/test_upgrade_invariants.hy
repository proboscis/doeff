;; 入れ替えの順の条 V1〜V3(coordinator/core/upgrade_invariants.hy — #3366 の単位 2a)の検。記録は入れ替えを始めた瞬間の写しの合成の列で、
;; 2026-10-05 の版上げ(#3156)で通した順は緑、破る順は条の名で赤になる事を確かめる。模擬の Flux が当てた瞬間に同じ記録を写す筋書きは
;; 単位 2b。
(require doeff-hy.macros [deftest <- val])
(val MODULE-TAGS {:context "doeff-cluster-test" :role "test"})
(import doeff_cluster.coordinator.intent.cluster_model [WorkerInfo])
(import doeff_cluster.coordinator.core.upgrade_invariants [UpgradeKind PendingPhase RosterEntry PendingTask UpgradeStart
                                                            coordinator-after-every-worker worker-swap-leaves-a-taker
                                                            one-worker-at-a-time])

(val OLD "d563ab95a0000000000000000000000000000000")
(val NEW "90fd9a81d97ddf1cf5ae13a4036fa615108abbe7")


(val VERIFY-1 (WorkerInfo "verify-1" #("verify" "host-verify-1") 1 0 :task-reserve 0))
(val VERIFY-2 (WorkerInfo "verify-2" #("verify" "host-verify-2") 1 0 :task-reserve 0))
(val AGENT (WorkerInfo "agent-2" #("agent" "host-agent-2") 19 0 :task-reserve 3))


(val QUEUED-VERIFY (PendingTask :task "t1" :phase PendingPhase.QUEUED :needs #("verify")))
(val ASSIGNED-VERIFY (PendingTask :task "t2" :phase PendingPhase.ASSIGNED :needs #("verify")))


(deftest test-the-order-the-2026-10-05-upgrade-used-is-green
  ;; 1 台ずつ・前の 1 台が新しい版で live に戻ってから次・検証用の 2 台は片方を live に残して・coordinator は最後。
  (val starts
    #((UpgradeStart :at-ms 1 :kind UpgradeKind.WORKER :target "agent-2" :doeff-commit NEW :tasks #(QUEUED-VERIFY)
                    :roster #((RosterEntry :info AGENT :live True :doeff-commit OLD)
                              (RosterEntry :info VERIFY-1 :live True :doeff-commit OLD)
                              (RosterEntry :info VERIFY-2 :live True :doeff-commit OLD)))
      (UpgradeStart :at-ms 2 :kind UpgradeKind.WORKER :target "verify-1" :doeff-commit NEW :tasks #(QUEUED-VERIFY)
                    :roster #((RosterEntry :info AGENT :live True :doeff-commit NEW)
                              (RosterEntry :info VERIFY-1 :live True :doeff-commit OLD)
                              (RosterEntry :info VERIFY-2 :live True :doeff-commit OLD)))
      (UpgradeStart :at-ms 3 :kind UpgradeKind.WORKER :target "verify-2" :doeff-commit NEW :tasks #(ASSIGNED-VERIFY)
                    :roster #((RosterEntry :info AGENT :live True :doeff-commit NEW)
                              (RosterEntry :info VERIFY-1 :live True :doeff-commit NEW)
                              (RosterEntry :info VERIFY-2 :live True :doeff-commit OLD)))
      (UpgradeStart :at-ms 4 :kind UpgradeKind.COORDINATOR :target "coordinator" :doeff-commit NEW :tasks #()
                    :roster #((RosterEntry :info AGENT :live True :doeff-commit NEW)
                              (RosterEntry :info VERIFY-1 :live True :doeff-commit NEW)
                              (RosterEntry :info VERIFY-2 :live True :doeff-commit NEW)))))
  (for [judge #(coordinator-after-every-worker worker-swap-leaves-a-taker one-worker-at-a-time)]
    (<- found tuple (judge starts))
    (assert (= found #()) found)))


(deftest test-swapping-the-only-taker-of-a-queued-task-breaks-v2
  ;; 失敗ケース(cisco-c8 の条件 2): queued の task が 1 つ在り、合う worker が他に live でない 1 台を入れ替え始める。
  (val start (UpgradeStart :at-ms 5 :kind UpgradeKind.WORKER :target "verify-1" :doeff-commit NEW :tasks #(QUEUED-VERIFY)
                           :roster #((RosterEntry :info VERIFY-1 :live True :doeff-commit OLD)
                                     (RosterEntry :info VERIFY-2 :live False :doeff-commit OLD)
                                     (RosterEntry :info AGENT :live True :doeff-commit OLD))))
  (<- found tuple (worker-swap-leaves-a-taker #(start)))
  (assert (= (tuple (gfor b found #(b.rule b.target))) #(#("V2 worker-swap-leaves-a-taker" "verify-1"))) found)
  ;; assigned の task も「合う task」に数える(同じ母集団 = /resources/Task の queued と assigned)。
  (<- assigned tuple (worker-swap-leaves-a-taker #((UpgradeStart :at-ms 5 :kind UpgradeKind.WORKER :target "verify-1"
                                                                 :doeff-commit NEW :tasks #(ASSIGNED-VERIFY) :roster start.roster))))
  (assert (= (len assigned) 1) assigned)
  ;; 合わない task(agent)や、別の合う worker が live なら破りではない。
  (<- other tuple (worker-swap-leaves-a-taker
                    #((UpgradeStart :at-ms 5 :kind UpgradeKind.WORKER :target "verify-1" :doeff-commit NEW
                                    :tasks #((PendingTask :task "t3" :phase PendingPhase.QUEUED :needs #("agent")))
                                    :roster start.roster))))
  (assert (= other #()) other)
  (<- covered tuple (worker-swap-leaves-a-taker
                      #((UpgradeStart :at-ms 5 :kind UpgradeKind.WORKER :target "verify-1" :doeff-commit NEW :tasks #(QUEUED-VERIFY)
                                      :roster #((RosterEntry :info VERIFY-1 :live True :doeff-commit OLD)
                                                (RosterEntry :info VERIFY-2 :live True :doeff-commit OLD))))))
  (assert (= covered #()) covered))


(deftest test-starting-the-next-worker-before-the-previous-is-back-breaks-v3
  ;; 失敗ケース(cisco-c8 の条件 3): 前の 1 台が新しい版で live に戻ったのを読まずに次を始める — 戻りが来ない間は次へ進まない。
  (val first (UpgradeStart :at-ms 1 :kind UpgradeKind.WORKER :target "agent-2" :doeff-commit NEW :tasks #()
                           :roster #((RosterEntry :info AGENT :live True :doeff-commit OLD)
                                     (RosterEntry :info VERIFY-1 :live True :doeff-commit OLD))))
  (for [#(what entry) #(#("まだ live でない" (RosterEntry :info AGENT :live False :doeff-commit NEW))
                        #("live だが古い版のまま" (RosterEntry :info AGENT :live True :doeff-commit OLD)))]
    (val second (UpgradeStart :at-ms 2 :kind UpgradeKind.WORKER :target "verify-1" :doeff-commit NEW :tasks #()
                              :roster #(entry (RosterEntry :info VERIFY-1 :live True :doeff-commit OLD))))
    (<- found tuple (one-worker-at-a-time #(second first)))
    (assert (= (tuple (gfor b found #(b.rule b.target))) #(#("V3 one-worker-at-a-time" "verify-1"))) #(what found))))


(deftest test-starting-the-coordinator-before-every-worker-is-new-breaks-v1
  ;; 失敗ケース: worker が全部新しい版で live になる前に coordinator の入れ替えを始める。
  (val start (UpgradeStart :at-ms 9 :kind UpgradeKind.COORDINATOR :target "coordinator" :doeff-commit NEW :tasks #()
                           :roster #((RosterEntry :info AGENT :live True :doeff-commit NEW)
                                     (RosterEntry :info VERIFY-1 :live True :doeff-commit OLD)
                                     (RosterEntry :info VERIFY-2 :live False :doeff-commit NEW))))
  (<- found tuple (coordinator-after-every-worker #(start)))
  (assert (= (sorted (gfor b found b.rule)) ["V1 coordinator-after-every-worker" "V1 coordinator-after-every-worker"]) found)
  (assert (all (gfor b found (= b.target "coordinator")))))
