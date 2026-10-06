;; coordinator は、要求の無い間、次の期限か要求か停止の合図まで受付を 1 本で待つ(#3865 の単位 2b)。1 秒の格子で起きない。
;;
;; - 調停ループは、期限の無い静かな状態では受付を期限なし(timeout None)で待つ。
;; - 期限は、その刻ちょうどに起きる(worker の最後の連絡が 300 ms の時、沈黙の判断は 10301 ms — 1 秒の格子の 11000 ms ではない)。
;; - 壁の時計の模擬でも、要求の来ない間は受け口から取らない。
;; - 模擬の受付の列は、worker の代役が預けた仮の heartbeat を、その刻に普通の heartbeat の要求として渡す(調停ループは本番と同じ
;;   要求しか受けない)。
(require doeff-hy.macros [deftest defk defhandler <- val var])
(val MODULE-TAGS {:context "doeff-cluster-test" :role "test"})
(import datetime [timedelta])
(import doeff [run with-handlers])
(import doeff_time [Delay SimClock sim-time-handler])
(import doeff_events [MemoryBroker])
(import doeff_core_effects.scheduler [CreatePromise Promise])
(import doeff_cluster.shared.core.clock [now-epoch-ms])
(import doeff_cluster.shared.intent.protocol [ClusterTiming NextRequests Reply Request CoordinatorStopRequested])
(import doeff_cluster.shared.protocol.inbox [http-request])
(import doeff_cluster.coordinator.intent.cluster_model [ClusterState ClusterNaming ProvisionalBeat])
(import doeff_cluster.coordinator.intent.request_bodies [HeartbeatBody])
(import doeff_cluster.coordinator.core.program [run-coordinator])
(import doeff_cluster.coordinator.entry.handler_sets [memory-notices])
(import doeff_cluster.coordinator.protocol.request_bodies [request-bodies body-of])
(import doeff_cluster.coordinator.protocol.store [Persist durable-states])
(import doeff_cluster.coordinator.protocol.replies [reply-bodies])
(import doeff_cluster.coordinator.protocol.request_queue [RequestQueue RestBell queued-requests deposit-beats])
(import doeff_cluster.sim.local [wall-sim-cluster ClientLink SimLink SimWorker])
(import doeff_cluster.worker.intent.worker_model [WorkerPolicy])
(import tests.clock_fixtures [clock-at clock-ms])
(import tests.fixtures.envs [sim-foundation])
(import tests.fixtures.sim_programs [quitters])

(val VERSIONS {"python" "3.14.0" "doeff" "1"})
(val TAKE-LIMIT 400)    ; 取りの回数の上限(格子で起き続ける作りを、筋書きの終わりで止めるため)


(defk heartbeat-of [name]
  {:pre [(: name str)] :post [(: % Request)] :tags {:context "doeff-cluster-test" :role "judgment"}}
  "worker name の POST /heartbeat の要求を作るため(返事の札は無し)。"
  (! (http-request "POST" "/heartbeat" {} {"name" name "provides" ["net"] "capacity" 10 "taskReserve" 0 "versions" VERSIONS}
                   :actor name :peer name)))


(defclass TimedInbox []
  "刻つきの台本の受付: arrivals = #(刻 ms 要求の list) の列(刻の順)。取りは、渡された待ちの秒の刻と次の到着の刻の早い方へ仮想の時計を
   進め、その刻までに届いた要求を返す。waits = 取りごとの #(取りの刻 待ちの秒)。待ちの秒が期限なしで次の到着も無い・until-ms を
   越える・取りが TAKE-LIMIT 回に届いたら、止めの合図を立てて空で返す。"
  (defn #^ None __init__ [self #^ list arrivals #^ int until-ms]
    (setv self.arrivals (list arrivals) self.until-ms until-ms self.clock (SimClock) self.waits [] self.done False)
    None))


(defn #^ int inbox-now [#^ TimedInbox inbox]
  "台本の仮想の時計の今(epoch ms)を読むため。"
  (run (clock-ms inbox.clock)))


(defhandler timed-requests [#^ TimedInbox inbox]
  (NextRequests [timeout-seconds limit]
    (val now (inbox-now inbox))
    (setv inbox.waits (+ inbox.waits [#(now timeout-seconds)]))
    (val due (if (is timeout-seconds None) None (+ now (round (* 1000 timeout-seconds)))))
    (val arriving (if inbox.arrivals (get (get inbox.arrivals 0) 0) None))
    (val candidates (lfor at [due arriving] :if (is-not at None) at))
    (if (or (not candidates) (> (min candidates) inbox.until-ms) (>= (len inbox.waits) TAKE-LIMIT))
        (do (setv inbox.done True)
            (resume []))
        (do (val woke (max now (min candidates)))
            (.set-time inbox.clock (+ inbox.clock.current-time (timedelta :milliseconds (- woke now))))
            (val batch (lfor #(at requests) inbox.arrivals :if (<= at woke) request requests request))
            (setv inbox.arrivals (lfor entry inbox.arrivals :if (> (get entry 0) woke) entry))
            (resume batch))))
  (Reply [request status body] (resume None))
  (Persist [writes] (resume None))
  (CoordinatorStopRequested [] (resume inbox.done)))


(defk run-timed [inbox]
  {:pre [(: inbox TimedInbox)] :post [(: % ClusterState)] :tags {:context "doeff-cluster-test" :role "program"}}
  "新しい状態の調停ループを、刻つきの台本の受付の上で止めの合図まで回すため(worker の生死の出来事は誰も読まない memory の broker へ)。"
  (<- final ClusterState ((sim-time-handler :clock inbox.clock)
                           ((timed-requests inbox)
                             (request-bodies (durable-states (reply-bodies (with-handlers (memory-notices (MemoryBroker))
                                                                              (run-coordinator (ClusterState) (ClusterTiming) (ClusterNaming)))))))))
  final)


(deftest test-a-quiet-coordinator-waits-without-a-timeout
  ;; 新しい状態には期限が 1 つも無い(worker も task も Rollout も無い)。起動の後、落ち着くまでの今すぐの取り(待ち 0 秒)を数回して
  ;; から、受付を期限なしで待つ。直す前は 1 秒ごとに取る(待ち 1.0 秒が TAKE-LIMIT 回続く)。
  (val inbox (TimedInbox [] 600000))
  (<- (run-timed inbox))
  (val seconds (lfor #(_ s) inbox.waits s))
  (assert (is (get seconds -1) None) seconds)
  (assert (all (gfor s (cut seconds 0 -1) (= s 0.0))) seconds)
  (assert (<= (len seconds) 4) seconds))


(deftest test-the-coordinator-wakes-at-the-liveness-deadline-off-the-second-grid
  ;; worker の heartbeat が 300 ms に 1 つだけ届く。生死の窓 lease-ms 10 秒の期限の次の刻(10301 ms)に起きて沈黙を判じる。その間は
  ;; 起きない(1000 ms・2000 ms … の取りが無い)。直す前は 1 秒の格子で取る(10301 ms に起きず、1000 ms に起きる)。
  (val inbox (TimedInbox [#(300 [(! (heartbeat-of "w1"))])] 20000))
  (<- (run-timed inbox))
  (val instants (lfor #(at _) inbox.waits at))
  (assert (in 10301 instants) instants)
  (assert (not-in 1000 instants) instants)
  (assert (= (lfor at instants :if (< 300 at 10301) at) []) instants))


;; --- 模擬の環境(壁の時計)— 要求の無い間の起きの数 ----------------------------------------------------------------

(val QUIET-SECONDS 3.0)
(val RESTING-WORKERS #((SimWorker :name "w1" :provides (frozenset ["cluster-net"]) :task-reserve 0)))
(val RESTING-POLICY (WorkerPolicy :tick-seconds 10.0 :restart-backoff-ms 1000000000 :restart-backoff-max-ms 1000000000))


(defk takes-while-quiet [seconds]
  {:pre [(: seconds float)] :post [(: % int)] :tags {:context "doeff-cluster-test" :role "program"}}
  "筋書き: 起動の後 1 秒置き、要求の来ない seconds 秒の間に coordinator が受け口から取った回数(歩の数)を読むため。"
  (<- (Delay 1.0))
  (<- link SimLink (ClientLink))
  (val before link.queue.takes)
  (<- (Delay seconds))
  (- link.queue.takes before))


(deftest test-a-quiet-coordinator-does-not-wake-on-the-wall-clock
  ;; worker 1 台・拍 10 秒(この 3 秒の間に heartbeat は来ない)・要求の無い 3 秒: coordinator は起きない(歩 0)。
  ;; 直す前は 1 秒ごとに起きる(3 秒で 3 歩 前後)。
  (<- taken int (wall-sim-cluster (quitters sim-foundation) (takes-while-quiet QUIET-SECONDS)
                                  :workers RESTING-WORKERS :policy RESTING-POLICY))
  (assert (= taken 0) taken))


;; --- 模擬の受付の列 — 預けた仮の heartbeat を、その刻に要求として渡す ---------------------------------------------------

(defk hand-over-a-deposit [queue at]
  {:pre [(: queue RequestQueue) (: at int)] :post [(: % tuple)] :tags {:context "doeff-cluster-test" :role "program"}}
  "worker w1 の代役が刻 at の仮の heartbeat を列に預け、取り手が受付を期限なしで待つ。取りの起きた刻と、取った要求の path・送り手を
   返すため。"
  (val request (! (heartbeat-of "w1")))
  (<- body HeartbeatBody (body-of request))
  (<- promise Promise (CreatePromise))
  (<- (deposit-beats queue "w1" #((ProvisionalBeat :at at :request request :body body :name "w1")) (RestBell promise)))
  (<- batch list (NextRequests None))
  (<- woke int (now-epoch-ms))
  #(woke (lfor r batch #(r.path r.actor))))


(deftest test-the-sim-queue-hands-a-deposited-beat-over-at-its-instant
  ;; 期限なしで待つ取り手は、預けた仮の heartbeat の刻(2500 ms)に起き、その heartbeat を普通の要求として受ける。直す前の列は、期限なしの
  ;; 待ちを受けられない(待ちの秒を数として比べる)。
  (val queue (RequestQueue))
  (<- seen tuple ((sim-time-handler :clock (! (clock-at 0))) (with-handlers [(queued-requests queue)] (hand-over-a-deposit queue 2500))))
  (assert (= seen #(2500 [#("/heartbeat" "w1")])) seen))
