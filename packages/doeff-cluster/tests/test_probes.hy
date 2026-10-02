;;; coordinator の probe(2026-09-25): /livez・/readyz は調停ループを通さず、HTTP の受付が「ループが最後に要求を取りに来た時刻」
;;; だけで答える。fsync や k8s の読みでループが数秒止まっても落ちない。ループが本当に固まった時だけ落ちる。
(require doeff-hy.macros [defk deftest <- val])
(import json)
(import socket)
(import urllib.request)
(import urllib.error)
(import doeff_cluster.foundation.coordinator_inbox [probe-verdict RequestInbox READY-STALL-SECONDS LIVE-STALL-SECONDS])


(defn #^ None test-probe-verdict-before-the-loop-starts []
  ;; 起動中: liveness は通す(起動の遅さは startupProbe が見る)・readiness は通さない。
  (assert (= (get (probe-verdict "/livez" None) 0) 200))
  (assert (= (get (probe-verdict "/readyz" None) 0) 503)))


(defn #^ None test-a-slow-fsync-does-not-make-the-coordinator-unready []
  ;; 実測の最長の詰まり(fsync 10.4 秒 + 返事 13 秒)は readiness の閾値より短い。
  (assert (= (get (probe-verdict "/readyz" 13.0) 0) 200))
  (assert (= (get (probe-verdict "/livez" 13.0) 0) 200)))


(defn #^ None test-a-stalled-loop-is-unready-then-dead []
  (assert (= (get (probe-verdict "/readyz" (+ READY-STALL-SECONDS 1)) 0) 503))
  (assert (= (get (probe-verdict "/livez" (+ READY-STALL-SECONDS 1)) 0) 200))
  (assert (= (get (probe-verdict "/livez" (+ LIVE-STALL-SECONDS 1)) 0) 503)))


(defk free-port []
  {:pre [] :post [(: % int)] :tags {:context "doeff-cluster-test" :role "foundation"}}
  "手元の空いた TCP の port の番号を 1 つ取るため(検の HTTP の受付を起こす前)。"
  (with [s (socket.socket)]
    (.bind s #("127.0.0.1" 0))
    (get (.getsockname s) 1)))


(defk get-status [port path]
  {:pre [(: port int) (: path str)] :post [(: % tuple)] :tags {:context "doeff-cluster-test" :role "foundation"}}
  "手元の port の path へ GET を 1 回送り、状態の番号と JSON の本文の組を返すため(4xx・5xx も組で返す)。"
  (try
    (with [r (urllib.request.urlopen f"http://127.0.0.1:{port}{path}" :timeout 2)]
      #(r.status (json.loads (.read r))))
    (except [e urllib.error.HTTPError]
      #(e.code (json.loads (.read e))))))


(deftest test-probes-answer-without-the-loop
  ;; 調停ループが 1 度も要求を取らない(= fsync で塞がっている)間も、probe は並ばずに即答する。
  (val now [1000.0])
  (<- port int (free-port))
  (val inbox (RequestInbox port :clock (fn [] (get now 0))))
  (.start inbox)
  (val server inbox.server)
  (assert (is-not server None) "start の後は HTTP server が在る")
  (assert (= (get (! (get-status port "/readyz")) 0) 503))    ; まだループが来ていない
  (.take inbox 0.01 10)                                   ; ループが 1 度取りに来た
  (setv (get now 0) 1020.0)                               ; 20 秒 取りに来ない(fsync が遅い)
  (assert (= (get (! (get-status port "/readyz")) 0) 200))
  (setv (get now 0) 1040.0)                               ; 40 秒 = 止まった
  (setv #(status body) (! (get-status port "/readyz")))
  (assert (= status 503))
  (assert (in "40.0" (get body "reason")))
  (assert (= (get (! (get-status port "/livez")) 0) 200))
  ;; probe は箱に並ばない(ループの仕事を増やさない)
  (assert (.empty inbox.queue))
  (.shutdown server))
