;;; sim-cluster の行き止まりの見張り(#3078 — 設計の覚え書き event-waits の 5 節の 3)。
;;;
;;; 行き止まり = 生きている業務の task が全部 出来事(WaitForEvent)を待っていて、筋書きの本体も出来事か job の結末を期限なしで待ち、
;;; coordinator の task の行が全部 running か終わり(置き待ち・始まり待ち・置き直しの無い)の時。見張りは仮想の時計を進め続けずに、
;;; その場で sim-cluster を SimDeadlockError で終わらせる(待っている task の job と出来事の型を名指す)。sim の世界の予定の刻(#3094 の
;;; NextWorldDue — 網の切れが明ける刻など)が残る間は行き止まりにせず、明けた後に終わらせる。業務の timer(#3093 — doeff-events の
;;; ArmedTimers)が残る間も行き止まりにせず、見張りは最も早い timer の刻に読み直す。
(require doeff-hy.macros [deftest defk <- val])
(import pytest)
(import doeff_time [Delay])
(import doeff_events [ArmedTimer ArmTimerEffect DisarmTimerEffect Publish WaitForEventEffect PublishEffect event-handler timer-handler])
(import doeff_cluster.shared.core.clock [datetime-of-epoch-ms])
(import doeff_cluster.shared.intent.process_model [AwaitProcessEnded ProcessEnded ProcessWaitExpired])
(import doeff_cluster.sim.local [sim-cluster SimOutside SimDeadlockError SimDeadlock WaitSnapshot LiveProcess BusinessWait deadlock-of
                                 AwaitProcessStarted CutWorker SimProcess SIM-START-MS earliest-due])
(import tests.fixtures.envs [sim-foundation])
(import tests.fixtures.event_programs [ping-waiters Ping deadline-waiters])


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


;; --- 業務の timer(#3093)------------------------------------------------------------------------------------

(defk timers-outside []
  {:pre [] :post [(: % SimOutside)] :tags {:context "doeff-cluster-test" :role "foundation"}}
  "sim の外の世界: memory の出来事の答え手と、その内側に業務の timer の答え手(doeff-events の timer-handler — TimerFired を出来事の
   答え手へ発する)を置き、柵に出来事と timer の effect を通させる(走りごとに新しい答え手)。"
  (SimOutside :handlers [(event-handler) (timer-handler)]
              :effects #(WaitForEventEffect PublishEffect ArmTimerEffect DisarmTimerEffect)))


(deftest test-the-next-read-is-the-earlier-of-the-world-plan-and-the-first-timer
  ;; 見張りが次に読み直す刻: sim の世界の予定の刻と、最も早い業務の timer の刻の早い方(どちらも無ければ呼び鈴だけ)。
  (val first (ArmedTimer :tag "a" :at (datetime-of-epoch-ms 3000)))
  (val later (ArmedTimer :tag "b" :at (datetime-of-epoch-ms 9000)))
  (for [[world-due armed want] [[None #() None] [5000 #() 5000] [None #(first later) 3000] [5000 #(first later) 3000]
                                [2000 #(first later) 2000]]]
    (<- got (| int None) (earliest-due world-due armed))
    (assert (= got want) #(world-due armed got))))


(defk await-the-deadline-waiter []
  {:pre [] :post [(: % ProcessEnded)] :tags {:context "doeff-cluster-test" :role "program"}}
  "筋書き: waiter の process の終わりを期限なしで待つ(waiter は自分の期限の TimerFired を待つ — 誰も Publish しない)。"
  (<- ended (AwaitProcessEnded "waiter"))
  ended)


(deftest test-a-scenario-with-a-business-deadline-is-not-a-deadlock
  ;; 失敗ケース: waiter は 20 秒先の自分の期限(ArmTimer)の TimerFired を、筋書きは waiter の終わりを、どちらも期限なしで待つ。業務の
  ;; timer が残る間は行き止まりにしない — 期限が来て waiter が終わる。見張りが ArmedTimers を見なければ、待ちがそろった直後に
  ;; SimDeadlockError で終わる。
  (<- outside SimOutside (timers-outside))
  (<- ended ProcessEnded (sim-cluster (deadline-waiters sim-foundation) (await-the-deadline-waiter) :outside outside))
  (assert (= ended.job "waiter") ended))


(deftest test-without-a-deadline-the-timer-handler-still-ends-the-run-as-a-deadlock
  ;; timer の答え手を置いても、積まれた timer が無ければ今までどおり即 行き止まり(ArmedTimers の答えが空)。
  (<- outside SimOutside (timers-outside))
  (with [raised (pytest.raises SimDeadlockError)]
    (<- (sim-cluster (ping-waiters sim-foundation) (await-the-waiter-forever) :outside outside)))
  (assert (in "Ping" (str raised.value)) raised.value))
