;; coordinator と記録の service の共用の部品(#3865 の単位 2 の前半 — 待ちは 1 秒のまま)。
;;
;; - 受付の箱の probe は、待つと定めた刻までの待ちを止まりと数えない(次の期限まで眠る形でも /readyz・/livez が落ちない)。歩の中に
;;   閾値より長く居る時は、今までどおり止まりと数える。
;; - 受付の箱は起こし(wake)で待ちを抜ける。停止の合図(SIGTERM)の受け手は印を立てて箱を起こすので、本物の process の待ちが
;;   すぐ抜ける(合図は main の thread の待ちに割り込む — 本物の process に本物の SIGTERM を送って確かめる)。
;; - worker は核の停止の答え手(os-signal-stop-handler)を使い、拍と拍の間の待ちの本番の答え手(tick-pauses)が停止の合図の待ち
;;   (AwaitStop)を拍の眠りと競わせるので、本物の process の待ちが期限(この検では 60 秒先)を待たずに抜ける(#3871 の単位 3)。
;; - coordinator は止まる時に、今の刻の生存の印を保存してから止まる(眠っている間は印を書かない形の前提 — 止まった長さを多く数えない)。
(require doeff-hy.macros [deftest defk deff defhandler <- val])
(val MODULE-TAGS {:context "doeff-cluster-test" :role "test"})
(import os)
(import signal)
(import subprocess)
(import sys)
(import threading)
(import time)
(import typing [Callable])
(import doeff [run with-handlers])
(import doeff_core_effects.handlers [await-handler])
(import doeff_core_effects.scheduler [ExternalPromise scheduled])
(import doeff_time [async-time-handler])
(import doeff_cluster.foundation.coordinator_inbox [RequestInbox RawRequest ReplySlot])
(import doeff_cluster.shared.protocol.inbox [http-requests])
(import doeff_cluster.coordinator.intent.cluster_model [ClusterState ClusterNaming])
(import doeff_cluster.shared.intent.protocol [ClusterTiming NextRequests Reply CoordinatorStopRequested])
(import doeff_cluster.coordinator.core.program [run-coordinator])
(import doeff_cluster.coordinator.protocol.request_bodies [request-bodies])
(import doeff_cluster.coordinator.protocol.store [Persist durable-states])
(import doeff_cluster.coordinator.protocol.replies [reply-bodies])
(import doeff_cluster.coordinator.protocol.kube [ObjectWatches kube-unavailable])
(import doeff_time [SimClock sim-time-handler])
(import datetime [timedelta])

;; 検の coordinator は k8s を持たない(本番の手元の coordinator と同じ答え手 — 調停ループは毎歩 Deployment と Node の見張りを揃える・
;; #3868・#4070)。
(val NO-KUBE "検の coordinator は k8s を持たない")


(defclass EnteredInbox [RequestInbox]
  "受付の箱: 取り手が待ちに入った(呼び鈴を掛けた)事を Event で知らせる(probe を、取り手が待っている最中に読むため)。"
  (defn #^ None __init__ [self #^ Callable clock]
    (.__init__ (super) 0 30.0 :clock clock)
    (setv self.entered (threading.Event))
    None)

  (defn #^ bool arm [self #^ (| float None) timeout #^ ExternalPromise bell]
    (setv queued (.arm (super) timeout bell))
    (.set self.entered)
    queued))


(defn #^ RawRequest a-read []
  "筋書きの要求 1 件(状態の読み)を、受付が並べる生の形で作るため。"
  (RawRequest "GET" "/state" {} None (ReplySlot) None "test"))


(defk taken-by-loop [inbox timeout]
  {:pre [(: inbox RequestInbox) (: timeout float)] :post [(: % list)] :tags {:context "doeff-cluster-test" :role "program"}}
  "調停ループと同じ形(本番の受付の答え手 http-requests と壁の時計)で、受付から 1 度取るため。"
  (<- batch list (with-handlers [(await-handler) (async-time-handler) (http-requests inbox)] (NextRequests timeout 10)))
  batch)


(deff take-on-a-thread [inbox timeout]  ; defk にできない: threading.Thread が別の thread で呼ぶ target
  {:pre [(: inbox RequestInbox) (: timeout float)] :post [(: % None)] :tags {:context "doeff-cluster-test" :role "foundation"}}
  "別の thread の取り手として、scheduler の上で受付から 1 度取るため(probe を、取り手が待っている最中に読む検の取り手)。"
  (run (scheduled (taken-by-loop inbox timeout)))
  None)


(deftest test-the-probe-does-not-count-a-planned-wait-as-a-stall
  ;; 取り手が 300 秒の待ちに入った後、45 秒目の /readyz と 130 秒目の /livez は 200(待つと定めた刻の前なので生きている)。
  ;; 直す前は「最後に取りに来てから」の秒で判じるので、45 秒目の /readyz が 503(閾値 30 秒)・130 秒目の /livez が 503(閾値 120 秒)。
  (val now [1000.0])
  (val inbox (EnteredInbox (fn [] (get now 0))))
  (val taker (threading.Thread :target take-on-a-thread :args #(inbox 300.0) :daemon True))
  (.start taker)
  (assert (.wait inbox.entered 5.0))
  (setv (get now 0) 1045.0)
  (val ready (get (.probe inbox "/readyz") 0))
  (setv (get now 0) 1130.0)
  (val live (get (.probe inbox "/livez") 0))
  (.offer inbox (a-read))
  (.join taker 5.0)
  (assert (not (.is-alive taker)) "要求を並べると取り手の待ちが抜ける")
  (assert (= #(ready live) #(200 200)) #(ready live)))


(deftest test-the-probe-still-reports-a-loop-stuck-inside-a-step
  ;; 守り: 取り手が要求を取って歩に入った後、31 秒戻らなければ /readyz は 503(閾値 30 秒)・121 秒で /livez も 503。
  (val now [1000.0])
  (val inbox (RequestInbox 0 30.0 :clock (fn [] (get now 0))))
  (.offer inbox (a-read))
  (.taken inbox 1)
  (setv (get now 0) 1031.0)
  (val ready (get (.probe inbox "/readyz") 0))
  (setv (get now 0) 1121.0)
  (val live (get (.probe inbox "/livez") 0))
  (assert (= #(ready live) #(503 503)) #(ready live)))


(deftest test-a-woken-inbox-returns-at-once-and-keeps-the-requests
  ;; 起こしが入っていれば、取り手は待ちの秒を待たずに空で返る。起こしの後ろに並んだ要求は、次の取りで受ける(落とさない)。
  (val inbox (RequestInbox 0 30.0))
  (.wake inbox)
  (.offer inbox (a-read))
  (val started (time.monotonic))
  (<- first list (taken-by-loop inbox 5.0))
  (val waited (- (time.monotonic) started))
  (<- second list (taken-by-loop inbox 5.0))
  (assert (= (len first) 0) first)
  (assert (< waited 1.0) waited)
  (assert (= (len second) 1) second))


(deftest test-requests-past-the-limit-wait-for-the-next-take
  ;; 並んだ要求が limit を越えたら、越えた分は次の取りで受ける(落とさない)。直す前の take は、limit 件目の次の要求を列から取り出して
  ;; から抜けたので、その 1 件を落とした(札に返事が置かれず、送り手は打ち切りまで待った)。
  (val inbox (RequestInbox 0 30.0))
  (.offer inbox (a-read))
  (.offer inbox (a-read))
  (.offer inbox (a-read))
  (val first (.taken inbox 2))
  (val second (.taken inbox 2))
  (assert (= #((len first) (len second)) #(2 1)) #(first second)))


(val CHILD-INBOX
  "import sys, time, hy
from doeff import run, with_handlers
from doeff_core_effects.handlers import await_handler
from doeff_core_effects.scheduler import scheduled
from doeff_time import async_time_handler
from doeff_cluster.foundation.coordinator_inbox import RequestInbox, StopState, stop_on_signals
from doeff_cluster.foundation.record_inbox import RecordInbox
from doeff_cluster.shared.intent.protocol import NextRequests
from doeff_cluster.shared.protocol.inbox import http_requests
inbox = RecordInbox(0) if sys.argv[1] == 'records' else RequestInbox(0, 30.0)
stop = StopState()
run(stop_on_signals(stop, wake=inbox.wake))
print('ready', flush=True)
started = time.monotonic()
run(scheduled(with_handlers([await_handler(), async_time_handler(), http_requests(inbox)], NextRequests(20.0, 10))))
print(f'{stop.requested} {time.monotonic() - started:.3f}', flush=True)
")


(val CHILD-WORKER
  "import time, hy
from doeff import do, run, with_handlers
from doeff_core_effects.handlers import await_handler, state
from doeff_core_effects.scheduler import scheduled
from doeff_core_effects.stop_signal_effects import StopRequested
from doeff_core_effects.stop_signal_handlers import os_signal_stop_handler
from doeff_time import async_time_handler
from doeff_cluster.shared.intent.due_model import DueAt
from doeff_cluster.worker.intent.worker_model import AwaitNextTick, WakeSet, WorkerPolicy
from doeff_cluster.worker.protocol.tick_pauses import tick_pauses

@do
def body():
    yield StopRequested()
    print('ready', flush=True)
    started = time.monotonic()
    due = DueAt(at=int(time.time() * 1000) + 60000)
    yield AwaitNextTick(WorkerPolicy(), None, WakeSet(due=due, bells=(), exits=()))
    reason = yield StopRequested()
    print(f'{reason} {time.monotonic() - started:.3f}', flush=True)

run(scheduled(with_handlers([await_handler(), async_time_handler(), state(), os_signal_stop_handler, tick_pauses], body())))
")


(defn #^ str after-sigterm [#^ str code #^ tuple args]
  "子の process に code を走らせ、ready を読んだら SIGTERM を送り、子が最後に書いた行を返すため(本物の process の本物の合図)。"
  (setv child (subprocess.Popen [sys.executable "-c" code #* args] :stdout subprocess.PIPE :stderr subprocess.PIPE :text True))
  (try
    (assert (= (.strip (.readline child.stdout)) "ready") (.read child.stderr))
    (os.kill child.pid signal.SIGTERM)
    (setv #(out err) (.communicate child :timeout 25))
    (assert (= child.returncode 0) err)
    (.strip out)
    (finally (when (is child.returncode None) (.kill child)))))


(deftest test-a-sigterm-wakes-the-coordinator-inbox-wait
  ;; 20 秒の待ちの最中の SIGTERM で、印が立ち、待ちが 1 秒以内に抜ける。直す前は stop-on-signals が箱を起こさない(待ちは 20 秒)。
  (val parts (.split (after-sigterm CHILD-INBOX #("coordinator"))))
  (assert (= (get parts 0) "True") parts)
  (assert (< (float (get parts 1)) 1.0) parts))


(deftest test-a-sigterm-wakes-the-records-inbox-wait
  ;; 記録の service の箱(RecordInbox — RequestInbox の子)も同じ。
  (val parts (.split (after-sigterm CHILD-INBOX #("records"))))
  (assert (= (get parts 0) "True") parts)
  (assert (< (float (get parts 1)) 1.0) parts))


(deftest test-a-sigterm-ends-the-worker-tick-wait
  ;; 失敗ケース(#3871 の単位 3): 拍 10 秒の待ちの最中の SIGTERM で、核の止めの理由が立ち、待ちが 1 秒以内に抜ける。直す前は拍の待ちが
  ;; 止めの合図を待たない(待ちは 10 秒 — 印は次の拍の頭で読む)。
  (val parts (.split (after-sigterm CHILD-WORKER #())))
  (assert (= (cut parts 0 2) ["signal" (str (int signal.SIGTERM))]) parts)
  (assert (< (float (get parts 2)) 1.0) parts))


;; --- 止まる時の生存の印 ----------------------------------------------------------------------------

(defclass QuietScript []
  "要求の来ない台本: 取りのたびに仮想の時計を 500 ms 進め、stop-ms を越えたら停止の合図を真にする。saved = Persist の差分の列。"
  (defn #^ None __init__ [self #^ int stop-ms]
    (setv self.clock (SimClock) self.saved [] self.stop-ms stop-ms)
    None))


(defn #^ int clock-now-ms [#^ QuietScript script]
  "台本の仮想の時計の今の刻(epoch ms)を読むため。"
  (int (* 1000 (.timestamp script.clock.current-time))))


(defhandler quiet-script [#^ QuietScript script]
  (NextRequests [timeout-seconds limit]
    (.set-time script.clock (+ script.clock.current-time (timedelta :milliseconds 500)))
    (resume []))
  (Reply [request status body] (resume None))
  (Persist [writes] (.append script.saved (dfor w writes w.key w.value)) (resume None))
  (CoordinatorStopRequested [] (resume (> (clock-now-ms script) script.stop-ms))))


(deftest test-the-coordinator-saves-the-alive-mark-when-it-stops
  ;; 要求の来ない 3.5 秒(生存の印の間隔 5 秒より短い)で止まる: 止まる直前に、止まった刻の生存の印(counter の aliveMs)を保存する。
  ;; 直す前は、印の間隔に届かないので 1 度も保存されず、起き直しが止まっていた長さを多く数える。
  (val script (QuietScript 3000))
  (<- _ ClusterState ((sim-time-handler :clock script.clock)
                       ((quiet-script script) (request-bodies (durable-states (reply-bodies ((kube-unavailable NO-KUBE (ObjectWatches) (ObjectWatches))
                                                                                              (run-coordinator (ClusterState) (ClusterTiming) (ClusterNaming)))))))))
  (val marks (lfor d script.saved :if (in "counter" d) (get (get d "counter") "aliveMs")))
  (assert marks script.saved)
  (assert (= (get marks -1) (clock-now-ms script)) #(marks (clock-now-ms script))))
