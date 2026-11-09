;;; 本物の process と HTTP で、coordinator の入れ替えの重なり。
;;;
;;; 版上げで Deployment が前の Pod を止めてから新しい Pod を起こしていた間(Recreate)、coordinator に届く先が 1 つも無く、全部の worker が
;;; 15〜135 秒届かなかった。新しい process は受け口を先に開き、置き場の錠(foundation/sole_writer)を前の process が離すまで待つ:
;;;   - 前の process が動く間、新しい process は接続を受けるが、置き場を読まず返事をしない(書き手は 1 つ)。
;;;   - 前の process が止めの合図で調停ループを抜けて錠を離すと、新しい process が置き場を読み直し、待たせていた要求に答える。
;;;   - 錠を待つ間に止めの合図を受けた process は、置き場を読まずに止まる。
(require doeff-hy.macros [deftest defk val <-])
(import concurrent.futures [ThreadPoolExecutor])
(import signal)
(import socket)
(import subprocess)
(import time)
(import pathlib [Path])
(import httpx)
(import doeff [run])
(import doeff_cluster._foundation.sole_writer [SoleWriter])
(import tests.served_fixtures [ROOT HY free-port start-coordinator])
(import pytest)

;; この file の待ちは実時間が対象(何が対象かは名簿 tests/realtime_tests.toml のこの file の行)— realtime を名乗る file の中の眠りを、
;; 検の眠りの規則(doeff-linter の DOEFF211)は当てない。
(val pytestmark pytest.mark.realtime)

;; 後の process が受け口を開くまでの上限(Hy の module の読み込みを含む)と、前の process が止まってから後の process が答えるまでの上限。
(val LISTEN-SECONDS 60)
(val TAKEOVER-SECONDS 20)


(defk start-follower [tmp-path]
  {:pre [(: tmp-path Path)] :post [(: % tuple)] :tags {:context "doeff-cluster-test" :role "entry"}}
  "前の coordinator と同じ置き場の上に後の coordinator の process を起こし、受け口が接続を受けるまで待つため。答え = #(URL process
   log の path)。止めるのは呼び手。"
  (<- port int (free-port))
  (val log-path (/ tmp-path "follower.log"))
  (val log (open log-path "w"))
  (val process (subprocess.Popen [HY "-m" "doeff_cluster._coordinator.entry.main" "--state-file" (str (/ tmp-path "state.json"))
                                  "--port" (str port) "--notice-broker" "memory"]
                                 :cwd (str ROOT) :stdout log :stderr subprocess.STDOUT))
  (val deadline (+ (time.monotonic) LISTEN-SECONDS))
  (while (< (time.monotonic) deadline)
    (try
      (with [_ (socket.create-connection #("127.0.0.1" port) :timeout 1.0)]
        (return #(f"http://127.0.0.1:{port}" process log-path)))
      (except [OSError] None))
    (when (is-not (.poll process) None)
      (raise (RuntimeError (+ "後の coordinator が起動しなかった: " (.read-text log-path)))))
    (time.sleep 0.1))
  (.kill process)
  (raise (RuntimeError f"後の coordinator が {LISTEN-SECONDS} 秒で受け口を開かなかった")))


(defk state-status [url seconds]
  {:pre [(: url str) (: seconds float)] :post [(: % (| int None))] :tags {:context "doeff-cluster-test" :role "entry"}}
  "GET /state の status を読むため(seconds の内に答えなければ None)。"
  (try
    (. (httpx.get (+ url "/state") :timeout seconds) status-code)
    (except [httpx.TimeoutException] None)))


(defk stopped [process]
  {:pre [(: process subprocess.Popen)] :post [(: % None)] :tags {:context "doeff-cluster-test" :role "entry"}}
  "検の終わりに process を止めるため(止めの合図で止まらなければ殺す)。"
  (when (is (.poll process) None)
    (.send-signal process signal.SIGTERM)
    (try (.wait process :timeout 30)
         (except [subprocess.TimeoutExpired] (.kill process) (.wait process))))
  None)


(deftest test-a-follower-listens-but-serves-only-after-the-elder-releases-the-store [tmp-path]
  (<- elder tuple (start-coordinator tmp-path))
  (val elder-process (get elder 1))
  (<- follower tuple (start-follower tmp-path))
  (val url (get follower 0))
  (val process (get follower 1))
  (val log-path (get follower 2))
  (try
    ;; 前の process が置き場を持つ間、後の process は接続を受けるが返事をしない。
    (<- early (| int None) (state-status url 2.0))
    (assert (is early None))
    (assert (in "置き場の錠を前の process が持っている" (.read-text log-path)))
    ;; 前の process を止める前に送った要求は、受付の列で待ち、引き継いだ後に答えられる(接続を断られない)。
    (with [pool (ThreadPoolExecutor 1)]
      (val pending (.submit pool (fn [] (run (state-status url (float (+ TAKEOVER-SECONDS 10)))))))
      (time.sleep 0.5)
      (.send-signal elder-process signal.SIGTERM)
      (assert (= (.wait elder-process :timeout 30) 0))
      (assert (= (.result pending) 200)))
    (assert (in "置き場の錠を取った" (.read-text log-path)))
    (finally
      (<- (stopped elder-process))
      (<- (stopped process)))))


(deftest test-a-follower-stopped-while-waiting-for-the-store-quits-without-reading-it [tmp-path]
  (<- elder tuple (start-coordinator tmp-path))
  (val elder-process (get elder 1))
  (<- follower tuple (start-follower tmp-path))
  (val url (get follower 0))
  (val process (get follower 1))
  (val log-path (get follower 2))
  (try
    (.send-signal process signal.SIGTERM)
    (assert (= (.wait process :timeout 30) 0))
    (assert (in "置き場の錠を待つ間に止めの合図を受けた" (.read-text log-path)))
    (assert (not-in "で受けます" (.read-text log-path)))
    (finally
      (<- (stopped elder-process))
      (<- (stopped process)))))


(deftest test-the-sole-writer-lock-is-held-by-one-holder-at-a-time [tmp-path]
  (val path (str (/ tmp-path "coord" "writer.lock")))
  (val first (SoleWriter path))
  (val second (SoleWriter path))
  (assert (.try-take first))
  (assert (not (.try-take second)))
  ;; 止めの合図が立っていれば、持ち主の居る錠を待たずに抜ける。
  (assert (is (.wait second (fn [] True) 0.01) None))
  (.release first)
  (val waited (.wait second (fn [] False) 0.01))
  (assert (is-not waited None))
  (assert (not (.try-take first)))
  (.release second)
  (assert (.try-take first))
  (.release first))
