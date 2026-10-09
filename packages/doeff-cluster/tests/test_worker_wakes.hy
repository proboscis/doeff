;; worker の周の間を 1 本の待ちにする(#3871 の単位 4)— 失敗ケース。
;;
;; 前の形は周ごとに tick-seconds(0.5 秒)を眠り、何も変わらない間も 1 秒に 2 回、宣言の読み・観測・判断を回した。今は周の後に、
;; 起きる物の組(WakeSet — 期限・呼び鈴・待つ子)を集め、その早い 1 つまで待つ。組は状態を持つ handler が外側の答えに自分の分を足して
;; 返す(WorkerWakes — 一番外は空の組)。
;;   1 何も変わらない 60 秒に、周は heartbeat の期限の数だけ回る(前は 120 回 → 24 回)。
;;   7 状態を変えた周の後は待たずにもう 1 周。今すぐが続けば、続いた action の名を示して落ちる。
;;   8 宣言の変化の呼び鈴は、期限より前に待ちを起こす(守り)。
;;   9 待ちの口を使えない時も、送り手の口は heartbeat の期限を組に足す(heartbeat の間隔で起きる — 守り)。
;;   組み立て: 送り手の口の handler を外すと、その期限が組から消える(並べ忘れが見える)。
(require doeff-hy.macros [deftest defk deff defhandler <- val var])
(val MODULE-TAGS {:context "doeff-cluster-test" :role "test"})
(import pytest)
(import doeff [run Program])
(import doeff_core_effects.scheduler [scheduled])
(import time)
(import doeff_core_effects.handlers [await-handler])
(import doeff_core_effects.os_process [subprocess-handler])
(import doeff_core_effects.pidfd_exit [pidfd-exit-handler])
(import doeff_core_effects.process_effects [StartProcess ProcessStarted AwaitProcessExit])
(import doeff_time [async-time-handler])
(import doeff_cluster.shared.core.clock [now-epoch-ms])
(import doeff [with-handlers])
(import doeff_time [Delay SimClock sim-time-handler])
(import doeff_core_effects.scheduler [CreatePromise CompletePromise Promise Spawn])
(import doeff_core_effects.stop_signal_effects [StopRequested])
(import tests.stop_fixtures [stop-signal-never-comes])
(import tests.clock_fixtures [clock-ms])
(import tests.link_rig [cell-of LINK-ROUTE])
(import doeff_cluster.shared.intent.due_model [DueAt DueNow DueNever])
(import doeff_cluster.worker.intent.worker_model [WorkerPolicy WorkerState WorldView DesiredJobs ReadDesired ObserveWorld PublishStatus EnvReport AwaitNextTick
                                                  WakeSet WorkerWakes WorkerUnsettled])
(import doeff_cluster.worker.core.program [run-worker next-tick-due count-unsettled-ticks UNSETTLED-TICK-LIMIT])
(import doeff_cluster.worker.protocol.tick_pauses [tick-pauses])
(import doeff_cluster.worker.protocol.worker_wakes [no-wakes])
(import doeff_cluster.worker.protocol.coordinator_link [LinkState coordinator-link RESEND-AFTER-MS])

(val POLICY (WorkerPolicy))
(val BEAT-MS 2500)
(val READ-LIMIT 400)


(defclass TickLog []
  "偽の宿の観測: reads = 宣言の読み(周)の仮想の時刻(epoch ms)の列。"
  (defn #^ None __init__ [self #^ SimClock clock]
    (setv self.clock clock self.reads #())))


(defhandler quiet-host [#^ TickLog log #^ (| Promise None) bell #^ int stop-ms]
  ;; 引数に残す理由: 検ごとに別の時計・呼び鈴・止める刻で並べる(Ask で区別できない)。
  ;; 宣言は空のまま。起きる物の組は、最後の読みから BEAT-MS 後の期限(送り手の口が足す heartbeat の期限の代わり)と呼び鈴。
  (ReadDesired [env-report stopping]
    (setv log.reads (+ log.reads #((! (clock-ms log.clock)))))
    (resume (DesiredJobs #() :changed None)))
  (WorkerWakes []
    (resume (WakeSet :due (DueAt :at (+ (get log.reads -1) BEAT-MS)) :bells (if (is bell None) #() #(bell.future)) :exits #())))
  (StopRequested []
    (resume (if (or (>= (! (clock-ms log.clock)) stop-ms) (>= (len log.reads) READ-LIMIT)) "signal 15" None)))
  (EnvReport [] (resume None))
  (ObserveWorld [] (resume (WorldView #() #())))
  (PublishStatus [statuses note] (resume None)))


(defk ring-at [bell seconds]
  {:pre [(: bell Promise) (: seconds float)] :post [(: % None)] :tags {:context "doeff-cluster-test" :role "program"}}
  "seconds 後に呼び鈴を鳴らすため(宣言の変化の知らせの代わり)。"
  (<- (Delay seconds))
  (<- (CompletePromise bell True))
  None)


(defk quiet-ticks [stop-ms ring-seconds]
  {:pre [(: stop-ms int) (: ring-seconds (| float None))] :post [(: % tuple)] :tags {:context "doeff-cluster-test" :role "program"}}
  "本物の run-worker と本番の待ちの答え手を偽の宿の上で stop-ms まで回し、周の仮想の時刻の列を返すため。ring-seconds = 呼び鈴を鳴らす秒
   (None = 呼び鈴なし)。"
  (val log (TickLog (SimClock)))
  (var bell None)
  (when (is-not ring-seconds None)
    (<- made Promise (CreatePromise))
    (:= bell made))
  (<- ((sim-time-handler :clock log.clock)
       ((quiet-host log bell stop-ms)
        (stop-signal-never-comes
          (tick-pauses
            (do-ticks bell ring-seconds))))))
  log.reads)


(defk do-ticks [bell ring-seconds]
  {:pre [(: bell (| Promise None)) (: ring-seconds (| float None))] :post [(: % WorkerState)] :tags {:context "doeff-cluster-test" :role "program"}}
  "呼び鈴を鳴らす task を立ててから、本物の run-worker を回すため。"
  (when (is-not bell None)
    (<- (Spawn (ring-at bell ring-seconds) :daemon True)))
  (<- state (run-worker POLICY))
  state)


(deftest test-a-quiet-minute-steps-only-at-the-heartbeat-deadlines
  ;; 1: 何も変わらない 60 秒の周は、heartbeat の期限(2.5 秒)の数だけ — 0・2500・…・57500 の 24 回(前は 0.5 秒ごとの 120 回)。
  (<- reads tuple (quiet-ticks 60000 None))
  (val in-minute (lfor r reads :if (< r 60000) r))
  (assert (= (len in-minute) 24) reads)
  (assert (all (gfor #(a b) (zip in-minute (cut in-minute 1 None)) (= (- b a) BEAT-MS))) reads))


(deftest test-a-desired-change-wakes-the-wait-before-the-deadline
  ;; 8: 呼び鈴(宣言の変化の知らせ)が 0.7 秒に鳴ると、期限(2.5 秒)を待たずに周が回る。
  (<- reads tuple (quiet-ticks 3000 0.7))
  (assert (>= (len reads) 2) reads)
  (assert (<= 700 (get reads 1) 800) reads))


(deftest test-a-step-that-changed-the-state-steps-again-at-once
  ;; 7: 状態を変えた周(action を撃った周)の後は、期限に関わらず今すぐ。変えなければ期限のまま。
  (<- acted (| DueAt DueNow DueNever) (next-tick-due (DueAt :at 5000) True))
  (<- quiet (| DueAt DueNow DueNever) (next-tick-due (DueAt :at 5000) False))
  (assert (= acted (DueNow)) acted)
  (assert (= quiet (DueAt :at 5000)) quiet))


(deftest test-a-worker-that-never-settles-names-the-actions-and-stops
  ;; 7: 今すぐが UNSETTLED-TICK-LIMIT を越えて続いたら、続いた action の名を示して WorkerUnsettled で落ちる。期限に戻れば数え直す。
  (<- back int (count-unsettled-ticks 5 (DueAt :at 1) #()))
  (assert (= back 0) back)
  (with [caught (pytest.raises WorkerUnsettled)]
    (<- (count-unsettled-ticks UNSETTLED-TICK-LIMIT (DueNow) #("StartProcess"))))
  (assert (in "StartProcess" (str caught.value)) caught.value))


;; --- 送り手の口が組に足す期限(9・組み立て)---------------------------------------------------------------------

(defk link-wakes [with-link delivered]
  {:pre [(: with-link bool) (: delivered bool)] :post [(: % WakeSet)] :tags {:context "doeff-cluster-test" :role "program"}}
  "待ちの口を使わない送り手の口(最後に届いた返事 1000・間隔 BEAT-MS・fence 20 秒)の下で、今 1500 の起きる物の組を問うため。with-link =
   送り手の口の handler を並べるか(偽 = 一番外の空の組だけ)・delivered = 前の heartbeat が届いたか。"
  (val state (LinkState "w" #("cpu") 1 0 20000 "tasks" "boot" 0 1000 :watch False))
  (setv state.beat-interval-ms BEAT-MS)
  (when delivered
    (setv state.last-desired (DesiredJobs #())))
  (<- cell (cell-of "http://127.0.0.1:9"))
  (<- watch-cell (cell-of "http://127.0.0.1:9"))
  (val clock (SimClock))
  (<- ((sim-time-handler :clock clock) (Delay 1.5)))
  ;; tick の頭の時刻も 1500(tick に時間がかからない)。
  (<- wakes WakeSet (with-handlers (+ [(sim-time-handler :clock clock) no-wakes]
                                      (if with-link [(coordinator-link state cell LINK-ROUTE watch-cell)] []))
                      (WorkerWakes :began 1500)))
  wakes)


(deftest test-the-link-adds-the-heartbeat-deadline-even-without-the-watch
  ;; 9: 待ちの口を使わない時も、送り手の口は heartbeat の期限(最後に届いた返事 + 間隔 = 3500)を組に足す。呼び鈴は足さない。
  (<- wakes WakeSet (link-wakes True True))
  (assert (= wakes.due (DueAt :at 3500)) wakes)
  (assert (= wakes.bells #()) wakes)
  ;; 前の heartbeat が届いていなければ、送り直しの刻(今 + RESEND-AFTER-MS)が先に来る。
  (<- undelivered WakeSet (link-wakes True False))
  (assert (= undelivered.due (DueAt :at (+ 1500 RESEND-AFTER-MS))) undelivered))


(deftest test-without-the-link-handler-its-deadline-is-missing
  ;; 組み立て: 送り手の口の handler を並べ忘れると、その期限が組から消える(一番外の空の組のまま)。
  (<- wakes WakeSet (link-wakes False True))
  (assert (= wakes.due (DueNever)) wakes))


;; --- 子の終わりで起きる(本物の子 process・実時間 — 前後の数の 2 つ目)-------------------------------------------

(val CHILD-SECONDS 0.3)
;; 子の終わりから周が回るまでの遅れの上限(秒)— 前の形は周期 0.5 秒の位相しだいで最大 0.5 秒遅れた。
(val EXIT-PROMPT 0.1)


(defclass ChildLog []
  "子の終わりを待つ偽の宿の観測: pid = 待つ子(None = 待たない)・reads = 周の頭の単調の時計の秒の列。"
  (defn #^ None __init__ [self]
    (setv self.pid None self.reads #())))


(defhandler child-host [#^ ChildLog log]
  ;; 引数に残す理由: 検ごとの観測の入れ物。宣言は空。起きる物の組は、60 秒の期限と、最初の周の後だけ子の終わりの待ち。2 周目で止まる。
  (ReadDesired [env-report stopping]
    (setv log.reads (+ log.reads #((time.monotonic))))
    (resume (DesiredJobs #() :changed None)))
  (WorkerWakes []
    (<- now int (now-epoch-ms))
    (val exits (if (and (is-not log.pid None) (= (len log.reads) 1)) #((AwaitProcessExit log.pid)) #()))
    ;; 2 周目の後は、止める周まで待たない(期限を今の直後にする)。
    (resume (WakeSet :due (DueAt :at (+ now (if (= (len log.reads) 1) 60000 1))) :bells #() :exits exits)))
  (StopRequested []
    (resume (if (>= (len log.reads) 2) "signal 15" None)))
  (EnvReport [] (resume None))
  (ObserveWorld [] (resume (WorldView #() #())))
  (PublishStatus [statuses note] (resume None)))


(defk child-then-ticks [log]
  {:pre [(: log ChildLog)] :post [(: % float)] :tags {:context "doeff-cluster-test" :role "program"}}
  "CHILD-SECONDS で終わる本物の子を立て、本物の run-worker と本番の待ちの答え手を回し、子を立てた刻(単調の時計の秒)を返すため。"
  (val started-at (time.monotonic))
  (<- child ProcessStarted (StartProcess :argv #("sleep" (str CHILD-SECONDS))))
  (setv log.pid child.pid)
  (<- _state WorkerState ((child-host log) (stop-signal-never-comes (tick-pauses (run-worker POLICY)))))
  started-at)


(deff on-real-clock [#^ Program program]  ; defk にできない: 検が自分の scheduler と本物の event loop(pidfd の読み待ち)で回す入口
  {:pre [(: program Program)] :post [(: % float)] :tags {:context "doeff-cluster-test" :role "entry"}}
  "program を本物の答え手の組(外側が先 — event loop・実時間・子 process・子の終わりの待ち)の上で回すため(core の
   test_process_exit_wait の handled と同じ組)。"
  (run (scheduled (with-handlers [(await-handler) (async-time-handler) subprocess-handler pidfd-exit-handler] program))))


(deftest test-a-child-that-ends-wakes-the-wait-at-once
  ;; 2(単位 4 の繋ぎ): 待つ子が終わると、60 秒の期限を待たずに EXIT-PROMPT の内に次の周が回る(前は周期 0.5 秒の位相で最大 0.5 秒)。
  (val log (ChildLog))
  (val started-at (on-real-clock (child-then-ticks log)))
  (assert (>= (len log.reads) 2) log.reads)
  (val lag (- (get log.reads 1) started-at CHILD-SECONDS))
  (assert (<= 0.0 lag EXIT-PROMPT) lag))


;; --- 周の頭の問いの後に来た止め(SIGTERM の取りこぼし)--------------------------------------------------------------

(defhandler stop-already [#^ str reason]
  ;; 引数に残す理由: 検ごとに別の理由。止めの問いはいつも reason を答え、止めの合図の待ちは来ない。
  (StopRequested [] (resume reason)))


(defk waited-ms [stopping]
  {:pre [(: stopping bool)] :post [(: % int)] :tags {:context "doeff-cluster-test" :role "program"}}
  "止めが既に来ている所で、60 秒の期限の待ちを stopping(周の頭で止めを知っていたか)つきで待ち、待った仮想の ms を返すため。"
  (<- began int (now-epoch-ms))
  (<- (AwaitNextTick POLICY None (WakeSet :due (DueAt :at (+ began 60000)) :bells #() :exits #()) :stopping stopping))
  (<- ended int (now-epoch-ms))
  (- ended began))


(deftest test-a-stop-that-came-after-the-step-began-ends-the-wait-at-once
  ;; 周の頭で止めを知らなかった周の後に止めが来ていれば、待たずに戻る(SIGTERM が周の頭の問いと待ちの間に来た時に、期限まで待たない)。
  ;; 止まりの手順の周(周の頭で止めを知っていた)は、止めで起きずに期限まで待つ(子の終わりを待つ間の空回りをしない)。
  (val clock (SimClock))
  (<- fresh int ((sim-time-handler :clock clock) ((stop-already "signal 15") (stop-signal-never-comes (tick-pauses (waited-ms False))))))
  (<- known int ((sim-time-handler :clock clock) ((stop-already "signal 15") (stop-signal-never-comes (tick-pauses (waited-ms True))))))
  (assert (= fresh 0) fresh)
  (assert (= known 60000) known))
