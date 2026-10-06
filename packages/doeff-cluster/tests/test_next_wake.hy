;; coordinator が次に起きる刻の純粋な判断と、落ち着くまでの約束(#3865 の単位 1b — まだ調停ループに繋がない)。
;;
;; - 次に起きる刻(next-wake)= 要求の無い歩の期限(tick-due)・Rollout の期限(rollout-due)・返事を待たせている待ち(GET /watch)の期限の
;;   いちばん早い答え。待ちは、落ち着いていれば期限 deadline-ms ちょうどに「変わっていない」と答える(watch-deadline と同じ比べ)。
;;   落ち着いていない待ち(版が進んだ・見え方をまだ覚えていない)は今すぐ。
;; - 歩の後の約束(after-step): 要求を受けずに状態を変えた歩の後は今すぐもう 1 歩。それ以外は次に起きる刻まで待つ。
;; - 待つ秒(wait-seconds): 刻までの秒(過ぎていれば 0)・今すぐは 0・無しは期限なし(None)。
;; - 落ち着かない時の止め(count-unsettled): 今すぐが UNSETTLED-STEP-LIMIT 歩を越えて続いたら、変わり続けた欄を名指して落ちる
;;   (黙って回り続けない)。
(require doeff-hy.macros [deftest defk <- val])
(import dataclasses [replace])
(import pytest)
(import doeff_cluster.shared.intent.protocol [ClusterTiming])
(import doeff_cluster.coordinator.intent.cluster_model [ClusterState ClusterNaming BoardRow Watcher WatchStep])
(import doeff_cluster.shared.intent.due_model [DueAt DueNow DueNever])
(import doeff_cluster.coordinator.core.watch_policy [watch-deadline])
(import doeff_cluster.coordinator.intent.due_model [CoordinatorUnsettled])
(import doeff_cluster.coordinator.core.wake_policy [next-wake watchers-due after-step wait-seconds count-unsettled
                                                    UNSETTLED-STEP-LIMIT])
(import doeff_cluster.shared.intent.protocol [Request])


(val TIMING (ClusterTiming))
(val NAMING (ClusterNaming))


(defk reader [after deadline]
  {:pre [(: after int) (: deadline int)] :post [(: % Watcher)] :tags {:context "doeff-cluster-test" :role "judgment"}}
  "版 after から先の変化を期限 deadline(epoch ms)まで待つ、worker を名指さない待ち(GET /watch)を作るため。"
  (Watcher :request (Request "GET" "/watch" {} None #("watch")) :after after :deadline-ms deadline))


(deftest test-next-wake-is-never-for-a-state-without-deadlines-or-waiters
  (<- due (| DueAt DueNow DueNever) (next-wake (ClusterState) 1000 TIMING NAMING #()))
  (assert (= due (DueNever)) due))


(deftest test-next-wake-is-the-earliest-of-the-step-deadlines-and-the-waiters
  ;; 盤の行の期限 3000 と、待ちの期限 5000: 早い方の 3000。待ちの期限だけなら 5000 ちょうど。
  (val state (ClusterState :board {"k" (BoardRow :value 1 :version 1 :expires-ms 3000 :size 1)}))
  (<- waiter Watcher (reader state.revision 5000))
  (<- both (| DueAt DueNow DueNever) (next-wake state 1000 TIMING NAMING #(waiter)))
  (assert (= both (DueAt :at 3000)) both)
  (<- only (| DueAt DueNow DueNever) (next-wake (ClusterState) 1000 TIMING NAMING #(waiter)))
  (assert (= only (DueAt :at 5000)) only))


(deftest test-a-waiter-answers-exactly-at-its-deadline
  ;; 待ちの期限の 1 ms 前は答えず、期限ちょうどで「変わっていない」と答える — 次に起きる刻と同じ値。
  (val state (ClusterState))
  (<- waiter Watcher (reader state.revision 5000))
  (<- due (| DueAt DueNow DueNever) (watchers-due #(waiter) state 1000))
  (assert (= due (DueAt :at 5000)) due)
  (<- before WatchStep (watch-deadline waiter state 4999))
  (<- at WatchStep (watch-deadline waiter state 5000))
  (assert (is before.answer None) before)
  (assert (is-not at.answer None) at))


(deftest test-an-unsettled-waiter-is-due-now
  ;; 版が進んだ待ちは、settle-watch が今の刻で答える — 今すぐ。
  (val state (replace (ClusterState) :revision 7))
  (<- waiter Watcher (reader 3 5000))
  (<- due (| DueAt DueNow DueNever) (watchers-due #(waiter) state 1000))
  (assert (= due (DueNow)) due))


(deftest test-a-step-that-changed-without-requests-is-followed-at-once
  ;; 要求を受けずに状態を変えた歩の後は今すぐ。それ以外(要求を受けた歩も)は次に起きる刻のまま(#3865 の直し A)。
  (val later (DueAt :at 9000))
  (<- changed (| DueAt DueNow DueNever) (after-step later True))
  (<- quiet (| DueAt DueNow DueNever) (after-step later False))
  (assert (= #(changed quiet) #((DueNow) later)) #(changed quiet)))


(deftest test-the-wait-is-the-seconds-to-the-due-instant
  (<- ahead (| float None) (wait-seconds (DueAt :at 2500) 1000))
  (<- past (| float None) (wait-seconds (DueAt :at 900) 1000))
  (<- now (| float None) (wait-seconds (DueNow) 1000))
  (<- never (| float None) (wait-seconds (DueNever) 1000))
  (assert (= #(ahead past now never) #(1.5 0.0 0.0 None)) #(ahead past now never)))


(deftest test-a-coordinator-that-never-settles-fails-naming-the-moving-field
  ;; 今すぐが続く数を数え、UNSETTLED-STEP-LIMIT を越えたら、前後の状態で違う欄を名指して落ちる。落ち着いた歩(今すぐでない)で 0 に戻る。
  (val before (ClusterState))
  (val after (replace before :revision 1))
  (<- one int (count-unsettled 0 (DueNow) before after))
  (<- reset int (count-unsettled one (DueAt :at 9000) before after))
  (assert (= #(one reset) #(1 0)) #(one reset))
  (<- near int (count-unsettled (- UNSETTLED-STEP-LIMIT 1) (DueNow) before after))
  (assert (= near UNSETTLED-STEP-LIMIT) near)
  (with [caught (pytest.raises CoordinatorUnsettled)]
    (<- (count-unsettled UNSETTLED-STEP-LIMIT (DueNow) before after)))
  (assert (in "revision" (str caught.value)) (str caught.value)))
