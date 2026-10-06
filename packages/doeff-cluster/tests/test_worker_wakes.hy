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
(require doeff-hy.macros [deftest defk defhandler <- val var])
(val MODULE-TAGS {:context "doeff-cluster-test" :role "test"})
(import pytest)
(import doeff [with-handlers])
(import doeff_time [Delay SimClock sim-time-handler])
(import doeff_core_effects.scheduler [CreatePromise CompletePromise Promise Spawn])
(import doeff_core_effects.stop_signal_effects [StopRequested])
(import tests.stop_fixtures [stop-signal-never-comes])
(import tests.clock_fixtures [clock-ms])
(import tests.link_rig [cell-of LINK-ROUTE])
(import doeff_cluster.shared.intent.due_model [DueAt DueNow DueNever])
(import doeff_cluster.worker.intent.worker_model [WorkerPolicy WorkerState WorldView DesiredJobs ReadDesired ObserveWorld PublishStatus EnvReport
                                                  WakeSet WorkerWakes WorkerUnsettled])
(import doeff_cluster.worker.core.program [run-worker next-tick-due count-unsettled-ticks UNSETTLED-TICK-LIMIT])
(import doeff_cluster.worker.protocol.tick_pauses [tick-pauses])
(import doeff_cluster.worker.protocol.worker_wakes [no-wakes])
(import doeff_cluster.worker.protocol.coordinator_link [LinkState coordinator-link])

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

(defk link-wakes [with-link]
  {:pre [(: with-link bool)] :post [(: % WakeSet)] :tags {:context "doeff-cluster-test" :role "program"}}
  "待ちの口を使わない送り手の口(最後に届いた返事 1000・間隔 BEAT-MS・fence 20 秒)の下で、今 1500 の起きる物の組を問うため。with-link =
   送り手の口の handler を並べるか(偽 = 一番外の空の組だけ)。"
  (val state (LinkState "w" #("cpu") 1 0 20000 "tasks" "boot" 0 1000 :watch False))
  (setv state.beat-interval-ms BEAT-MS)
  (<- cell (cell-of "http://127.0.0.1:9"))
  (<- watch-cell (cell-of "http://127.0.0.1:9"))
  (val clock (SimClock))
  (<- ((sim-time-handler :clock clock) (Delay 1.5)))
  (<- wakes WakeSet (with-handlers (+ [(sim-time-handler :clock clock) (no-wakes)]
                                      (if with-link [(coordinator-link state cell LINK-ROUTE watch-cell)] []))
                      (WorkerWakes)))
  wakes)


(deftest test-the-link-adds-the-heartbeat-deadline-even-without-the-watch
  ;; 9: 待ちの口を使わない時も、送り手の口は heartbeat の期限(最後に届いた返事 + 間隔 = 3500)を組に足す。呼び鈴は足さない。
  (<- wakes WakeSet (link-wakes True))
  (assert (= wakes.due (DueAt :at 3500)) wakes)
  (assert (= wakes.bells #()) wakes))


(deftest test-without-the-link-handler-its-deadline-is-missing
  ;; 組み立て: 送り手の口の handler を並べ忘れると、その期限が組から消える(一番外の空の組のまま)。
  (<- wakes WakeSet (link-wakes False))
  (assert (= wakes.due (DueNever)) wakes))
