;;; sim-cluster の行き止まりの見張り(#3078)が、業務の task の WaitForEvents(届いている合図を全部 1 回で受ける待ち)も出来事の待ちと
;;; して数えるかの検。数えなければ、誰も Ping を送らない筋書きで仮想の時計が進み続けて終わらない(test_sim_deadlock の
;;; test-a-wait-for-an-event-nobody-sends-ends-the-run-at-once-as-a-deadlock の WaitForEvents 版)。
(require doeff-hy.macros [deftest defk defsystem <- val])
(import doeff_events [MemoryBroker])
(import pytest)
(import collections.abc [Callable])
(import doeff_events [WaitForEvents WaitForEventsEffect PublishEffect event-handler])
(import doeff_cluster.shared.intent.process_model [AwaitProcessEnded ProcessEnded])
(import doeff_cluster.sim.local [sim-cluster SimOutside SimDeadlockError])
(import tests.fixtures.envs [sim-foundation])
(import tests.fixtures.event_programs [Ping])


(defk batch-events-outside []
  {:pre [] :post [(: % SimOutside)] :tags {:context "doeff-cluster-test" :role "foundation"}}
  "sim の外の世界: memory の出来事の答え手を置き、柵に WaitForEvents と Publish の effect を通させる(走りごとに新しい答え手)。"
  (SimOutside :handlers [(event-handler)] :effects #(WaitForEventsEffect PublishEffect)))


(defk wait-pings []
  {:pre [] :post [(: % tuple)] :tags {:context "doeff-cluster-test" :role "program"}}
  "合図 Ping を WaitForEvents の 1 回で待つ(待つ間は時計を進めない — 誰かが Publish するまで止まる)。"
  (<- pings tuple (WaitForEvents Ping))
  pings)


(defk batch-waiter-program [foundation]
  {:pre [(: foundation Callable)] :post [(: % None)] :tags {:context "doeff-cluster-test" :role "entry"}}
  "合図 Ping を WaitForEvents で受けたら終わる service。"
  (<- (foundation (wait-pings)))
  None)


(defsystem batch-waiters [#^ Callable foundation]
  "合図 Ping を WaitForEvents で待つ 1 つの service"
  (waiter (batch-waiter-program foundation) :replicas 1 :needs #{"cluster-net"}))


(defk await-the-waiter-forever []
  {:pre [] :post [(: % ProcessEnded)] :tags {:context "doeff-cluster-test" :role "program"}}
  "筋書き(失敗ケース): 誰も Ping を送らないまま、waiter の process の終わりを期限なしで待つ。"
  (<- ended (AwaitProcessEnded "waiter"))
  ended)


(deftest test-a-wait-for-events-nobody-sends-ends-the-run-at-once-as-a-deadlock
  (<- outside SimOutside (batch-events-outside))
  (with [raised (pytest.raises SimDeadlockError)]
    (<- (sim-cluster :notice-broker (MemoryBroker) (batch-waiters sim-foundation) (await-the-waiter-forever) :outside outside)))
  (assert (in "waiter" (str raised.value)) raised.value)
  (assert (in "Ping" (str raised.value)) raised.value))
