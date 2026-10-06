;; 記録の service の Program(record_store/core/program.hy の store-loop)は、要求の無い間、次の保守の期限まで 1 本で待つ(#3867
;; — 1 秒ごとに起きる形をやめる)。要求か停止の合図で待ちが抜ける部品は #3865 の単位 2a(受付の箱の wake・stop-on-signals)。
;;
;; - 台本の受付(仮想の時計): 取りのたびに、待ちの長さの中に届く要求が在ればその刻へ時計を進めて渡し、無ければ待ちの長さだけ進める。
;;   保守の刻は CompactRecords の now-ms で見る。
;; - 本物の process: 長い待ちの最中の /healthz と SIGTERM(守り)。
(require doeff-hy.macros [deftest defhandler val])
(val MODULE-TAGS {:context "doeff-cluster-test" :role "test"})
(import json)
(import os)
(import signal)
(import subprocess)
(import sys)
(import tempfile)
(import time)
(import urllib.request)
(import doeff [run])
(import doeff_core_effects.scheduler [scheduled])
(import doeff_time [SimClock sim-time-handler])
(import doeff_cluster.shared.core.clock [datetime-of-epoch-ms])
(import doeff_cluster.shared.intent.protocol [Request NextRequests Reply CoordinatorStopRequested])
(import doeff_cluster.record_store.intent.record_store_model [CompactRecords PruneRecords])
(import doeff_cluster.record_store.core.program [store-loop MAINTENANCE-MS])


(val START-MS 1800000000000)


(defclass StoreScript []
  "記録の service の台本。arrivals = 届く要求の #(刻 ms Request) の list(刻の順)・stop-ms = この刻を越えたら停止の合図が真。
   takes = 取りに来た刻(ms)の tuple・maintained = 保守(CompactRecords)の刻の tuple。"
  (defn #^ None __init__ [self #^ list arrivals #^ int stop-ms]
    (setv self.clock (SimClock (datetime-of-epoch-ms START-MS)) self.arrivals arrivals self.stop-ms stop-ms
          self.takes #() self.maintained #())
    None))


(defn #^ int script-now-ms [#^ StoreScript script]
  "台本の仮想の時計の今の刻(epoch ms)を読むため。"
  (int (round (* 1000 (.timestamp script.clock.current-time)))))


(defn #^ list script-take [#^ StoreScript script #^ float timeout-seconds]
  "取り 1 回: 待ちの長さの中に届く要求が在れば、その刻へ時計を進めて渡す。無ければ待ちの長さだけ時計を進め、空を返す。"
  (setv now (script-now-ms script))
  (setv script.takes (+ script.takes #(now)))
  (setv until (+ now (round (* timeout-seconds 1000))))
  (if (and script.arrivals (<= (get (get script.arrivals 0) 0) until))
      (do (setv #(at request) (get script.arrivals 0) script.arrivals (cut script.arrivals 1 None))
          (.set-time script.clock (datetime-of-epoch-ms (max now at)))
          [request])
      (do (.set-time script.clock (datetime-of-epoch-ms until))
          [])))


(defhandler store-script [#^ StoreScript script]
  (NextRequests [timeout-seconds limit] (resume (script-take script timeout-seconds)))
  (Reply [request status body] (resume None))
  (CompactRecords [now-ms idle-ms] (setv script.maintained (+ script.maintained #(now-ms))) (resume 0))
  (PruneRecords [now-ms retention-ms] (resume []))
  (CoordinatorStopRequested [] (resume (> (script-now-ms script) script.stop-ms))))


(defn #^ Request a-health-check []
  "台本の要求 1 件(GET /healthz — 置き場の effect を出さずに答える)。"
  (Request "GET" "/healthz" {} None #("healthz")))


(defn #^ StoreScript played [#^ list arrivals #^ int stop-ms]
  "台本の上で store-loop を停止まで回すため。"
  (setv script (StoreScript arrivals stop-ms))
  (run (scheduled ((sim-time-handler :clock script.clock) ((store-script script) (store-loop 86400000 900000)))))
  script)


(deftest test-a-quiet-store-does-not-wake-each-second
  ;; 要求の無い 3 秒に、取りに来ない。直す前は 1 秒ごとに取りに来る(3 回)。
  (val script (played [] (+ START-MS 3000)))
  (val woken (lfor at script.takes :if (< START-MS at (+ START-MS 3001)) at))
  (assert (= woken []) script.takes))


(deftest test-maintenance-runs-at-its-deadline-after-an-off-grid-request
  ;; 0.5 秒の刻に要求が 1 つ来ても、保守は起動の刻と、そこから 5 分ちょうどごとに走る(期限の 1 ミリ秒前にも後にも走らない)。
  ;; 直す前は、要求で 1 秒の周期の位相がずれ、保守が 0.5 秒遅れる。
  (val script (played [#((+ START-MS 500) (a-health-check))] (+ START-MS (* 2 MAINTENANCE-MS))))
  (assert (= (cut script.maintained 0 3) #(START-MS (+ START-MS MAINTENANCE-MS) (+ START-MS (* 2 MAINTENANCE-MS))))
          script.maintained))


(deftest test-a-request-just-before-the-deadline-does-not-move-it
  ;; 期限の 0.5 秒前に要求が来ても、次の保守は最後に保守した刻 + 5 分のまま。直す前は 1 秒の周期の位相のずれで遅れる。
  (val script (played [#((+ START-MS (- MAINTENANCE-MS 500)) (a-health-check))] (+ START-MS MAINTENANCE-MS)))
  (assert (= (cut script.maintained 0 2) #(START-MS (+ START-MS MAINTENANCE-MS))) script.maintained))


;; --- 本物の process(守り)------------------------------------------------------------------------

(val CHILD-STORE
  "import sys, hy
from doeff import run
from doeff_core_effects.scheduler import scheduled
from doeff_cluster.foundation.coordinator_inbox import StopState, stop_on_signals
from doeff_cluster.foundation.record_inbox import RecordInbox
from doeff_cluster.record_store.entry.handler_sets import production_handlers
from doeff_cluster.record_store.entry.main import record_store_on
stop = StopState()
inbox = RecordInbox(0)
run(stop_on_signals(stop, wake=inbox.wake))
inbox.start()
print(inbox.server.server_address[1], flush=True)
served = run(scheduled(record_store_on(run(production_handlers(sys.argv[1], inbox, stop)), 86400000, 900000)))
print(f'served {served}', flush=True)
")


(defn #^ subprocess.Popen started-store [#^ str root]
  "本物の process で記録の service(本番の handler の組)を起こし、受付の port を読むまで待つため。"
  (subprocess.Popen [sys.executable "-c" CHILD-STORE root] :stdout subprocess.PIPE :stderr subprocess.PIPE :text True))


(defn #^ tuple health-in [#^ int port]
  "/healthz を 1 度読み、#(status 秒) を返すため。"
  (setv started (time.monotonic))
  (with [reply (urllib.request.urlopen (.format "http://127.0.0.1:{}/healthz" port) :timeout 10)]
    #(reply.status (- (time.monotonic) started))))


(defn #^ tuple waiting-store [#^ str root]
  "記録の service を起こし、最初の保守を終えて長い待ちに入るまで待つため。#(子 port)。"
  (setv child (started-store root))
  (setv port (int (.strip (.readline child.stdout))))
  ;; 最初の /healthz が答えれば store-loop は回っている。その後の 1 秒で、起動の刻の保守を終えて次の期限までの待ちに入る。
  (health-in port)
  (time.sleep 1.0)
  #(child port))


(deftest test-a-health-check-in-the-long-wait-is-answered-at-once
  ;; 次の保守の期限までの待ちの最中の /healthz は、待ちを起こして 1 秒以内に答える(守り — 受付の箱の put が待ちを抜ける)。
  (with [root (tempfile.TemporaryDirectory)]
    (setv #(child port) (waiting-store root))
    (try
      (setv #(status seconds) (health-in port))
      (assert (= status 200) status)
      (assert (< seconds 1.0) seconds)
      (finally (.kill child) (.communicate child)))))


(deftest test-a-sigterm-ends-the-long-wait-at-once
  ;; 次の保守の期限までの待ちの最中の SIGTERM で、store-loop が 2 秒以内に止まって process が終わる(守り — 合図が受付の箱を起こす)。
  (with [root (tempfile.TemporaryDirectory)]
    (setv #(child port) (waiting-store root))
    (try
      (setv signalled (time.monotonic))
      (os.kill child.pid signal.SIGTERM)
      (setv #(out err) (.communicate child :timeout 25))
      (setv seconds (- (time.monotonic) signalled))
      (assert (= child.returncode 0) err)
      (assert (in "served 1" out) #(out err))
      (assert (< seconds 2.0) #(seconds err))
      (finally (when (is child.returncode None) (.kill child) (.communicate child))))))
