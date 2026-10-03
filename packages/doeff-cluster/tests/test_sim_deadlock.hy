;;; sim-cluster の行き止まりの見張り(#3078 — 設計の覚え書き event-waits の 5 節の 3)。
;;;
;;; 行き止まり = 生きている業務の task が全部 出来事(WaitForEvent)を待っていて、筋書きの本体も出来事か job の結末を期限なしで待ち、
;;; coordinator の task の行が全部 running か終わり(置き待ち・始まり待ち・置き直しの無い)の時。見張りは仮想の時計を進め続けずに、
;;; その場で sim-cluster を SimDeadlockError で終わらせる(待っている task の job と出来事の型を名指す)。sim の世界の予定の刻(#3094 の
;;; NextWorldDue — 網の切れが明ける刻など)が残る間は行き止まりにせず、明けた後に終わらせる。業務の timer の条件(#3093)は、判じ
;;; (deadlock-of)の欄 armed-timers に既定値で置き、子 2 が値を入れる。
(require doeff-hy.macros [deftest defk <- val])
(import pytest)
(import doeff_time [Delay])
(import doeff_events [ArmedTimer Publish WaitForEventEffect PublishEffect event-handler])
(import doeff_cluster.shared.core.clock [datetime-of-epoch-ms])
(import doeff_cluster.shared.intent.process_model [AwaitProcessEnded ProcessEnded ProcessWaitExpired])
(import doeff_cluster.sim.local [sim-cluster SimOutside SimDeadlockError SimDeadlock WaitSnapshot LiveProcess BusinessWait deadlock-of
                                 AwaitProcessStarted CutWorker SimProcess SIM-START-MS])
(import tests.fixtures.envs [sim-foundation])
(import tests.fixtures.event_programs [ping-waiters Ping])


(defk events-outside []
  {:pre [] :post [(: % SimOutside)] :tags {:context "doeff-cluster-test" :role "foundation"}}
  "sim の外の世界: memory の出来事の答え手を置き、柵に出来事の 2 つの effect を通させる(走りごとに新しい答え手)。"
  (SimOutside :handlers [(event-handler)] :effects #(WaitForEventEffect PublishEffect)))


;; --- 判じ(effect を出さない関数)--------------------------------------------------------------------------

(val WAITER (LiveProcess :pid 1 :job "waiter" :tasks 1))
(val WAITING (BusinessWait :pid 1 :job "waiter" :events "Ping"))


(deftest test-the-judgment-names-the-waiting-tasks-only-when-every-condition-holds
  (val closed (WaitSnapshot :live #(WAITER) :waits #(WAITING) :scenario-waiting True :rows-settled True))
  (<- found (| SimDeadlock None) (deadlock-of closed))
  (assert (= found (SimDeadlock :waits #(WAITING))) found)
  ;; 1 つでも欠ければ行き止まりではない: 業務の task が 1 つも無い・task の 1 つが出来事を待っていない(主 + 子 1 の 2 つに待ち 1 つ)・
  ;; 筋書きの本体が止まっていない・coordinator に落ち着かない行が在る・業務の timer が在る・sim の世界の予定の刻が在る。
  (for [open [(WaitSnapshot :live #() :waits #() :scenario-waiting True :rows-settled True)
              (WaitSnapshot :live #((LiveProcess :pid 1 :job "waiter" :tasks 2)) :waits #(WAITING) :scenario-waiting True :rows-settled True)
              (WaitSnapshot :live #(WAITER) :waits #(WAITING) :scenario-waiting False :rows-settled True)
              (WaitSnapshot :live #(WAITER) :waits #(WAITING) :scenario-waiting True :rows-settled False)
              (WaitSnapshot :live #(WAITER) :waits #(WAITING) :scenario-waiting True :rows-settled True :armed-timers #((ArmedTimer :tag "deadline" :at (datetime-of-epoch-ms 1000))))
              (WaitSnapshot :live #(WAITER) :waits #(WAITING) :scenario-waiting True :rows-settled True :world-due 1000)]]
    (<- verdict (| SimDeadlock None) (deadlock-of open))
    (assert (is verdict None) #(open verdict))))


;; --- sim-cluster の上の筋書き -------------------------------------------------------------------------------

(defk await-the-waiter-forever []
  {:pre [] :post [(: % ProcessEnded)] :tags {:context "doeff-cluster-test" :role "program"}}
  "筋書き(失敗ケース): 誰も Ping を送らないまま、waiter の process の終わりを期限なしで待つ。"
  (<- ended (AwaitProcessEnded "waiter"))
  ended)


(deftest test-a-wait-for-an-event-nobody-sends-ends-the-run-at-once-as-a-deadlock
  ;; waiter は Ping を、筋書きは waiter の終わりを、どちらも期限なしで待つ — 誰も Ping を送らない。見張りが無ければ、worker の拍の
  ;; timer がいつも時計の列に在るので、仮想の時計が進み続けて終わらない(検の上限で落ちる)。
  (<- outside SimOutside (events-outside))
  (with [raised (pytest.raises SimDeadlockError)]
    (<- (sim-cluster (ping-waiters sim-foundation) (await-the-waiter-forever) :outside outside)))
  (assert (in "waiter" (str raised.value)) raised.value)
  (assert (in "Ping" (str raised.value)) raised.value))


(defk ping-after-a-while []
  {:pre [] :post [(: % ProcessEnded)] :tags {:context "doeff-cluster-test" :role "program"}}
  "筋書き: 20 秒待ってから Ping を送り(waiter はその間ずっと出来事を待つ — 筋書きは時計を待つので行き止まりではない)、
   waiter の process の終わりを期限なしで待つ。"
  (<- (Delay 20.0))
  (<- (Publish (Ping :note "go")))
  (<- ended (AwaitProcessEnded "waiter"))
  ended)


(deftest test-an-event-that-arrives-is-not-a-deadlock
  (<- outside SimOutside (events-outside))
  (<- ended ProcessEnded (sim-cluster (ping-waiters sim-foundation) (ping-after-a-while) :outside outside))
  (assert (= ended.job "waiter") ended))


(defk await-the-waiter-for [seconds]
  {:pre [(: seconds float)] :post [(: % (| ProcessEnded ProcessWaitExpired))] :tags {:context "doeff-cluster-test" :role "program"}}
  "筋書き: waiter の process の終わりを seconds 秒まで待つ(期限つきの待ち)。"
  (<- answer (| ProcessEnded ProcessWaitExpired) (AwaitProcessEnded "waiter" :timeout-seconds seconds))
  answer)


(deftest test-a-bounded-wait-for-a-job-is-not-a-deadlock
  ;; 筋書きの待ちに期限が在れば、業務の task が全部 出来事を待っていても行き止まりにしない — 期限で答えが返る。
  (<- outside SimOutside (events-outside))
  (<- answer (| ProcessEnded ProcessWaitExpired) (sim-cluster (ping-waiters sim-foundation) (await-the-waiter-for 30.0)
                                                              :outside outside))
  (assert (= answer (ProcessWaitExpired :job "waiter" :waited-seconds 30.0)) answer))


(defk cut-then-await-the-waiter []
  {:pre [] :post [(: % ProcessEnded)] :tags {:context "doeff-cluster-test" :role "program"}}
  "筋書き(失敗ケース): waiter の process が起きたら、その worker の網を 30 秒切り(30 秒後に明ける予定が sim の世界に残る)、waiter の
   終わりを期限なしで待つ — 誰も Ping を送らない。"
  (<- started SimProcess (AwaitProcessStarted "waiter"))
  (<- (CutWorker started.worker 30.0))
  (<- ended (AwaitProcessEnded "waiter"))
  ended)


(deftest test-a-pending-world-plan-holds-off-the-deadlock-until-it-passes
  ;; 網の切れが明ける予定(NextWorldDue)が残る間は行き止まりにしない — 明けた後に、待ちがそのままなら行き止まりで終わる。見張りが
  ;; 予定の刻を見なければ、網を切った直後(起点から 30 秒より前)に終わる。
  (<- outside SimOutside (events-outside))
  (with [raised (pytest.raises SimDeadlockError)]
    (<- (sim-cluster (ping-waiters sim-foundation) (cut-then-await-the-waiter) :outside outside)))
  (val found (get raised.value.args 1))
  (assert (isinstance found SimDeadlock) found)
  (assert (>= (- found.at-ms SIM-START-MS) 30000) found))
