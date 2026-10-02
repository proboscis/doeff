;;; 検の道具: 本物の coordinator の process を起こす(test_detached.hy の served の組 — conftest.py の served_coordinator が使う)。
(require doeff-hy.macros [defk <- val])
(import os)
(import socket)
(import subprocess)
(import sys)
(import time)
(import pathlib [Path])
(import httpx)

(setv ROOT (. (Path (os.path.abspath __file__)) parent parent))   ; この package の根(子 process の cwd)
(setv HY (str (/ (. (Path sys.executable) parent) "hy")))


(defk free-port []
  {:pre [] :post [(: % int)] :tags {:context "doeff-cluster-test" :role "entry"}}
  "OS に空いている TCP の port を 1 つ選ばせ、その番号を返すため(coordinator の process に渡す)。"
  (with [s (socket.socket)]
    (.bind s #("127.0.0.1" 0))
    (get (.getsockname s) 1)))


(defk start-coordinator [tmp-path]
  {:pre [(: tmp-path Path)] :post [(: % tuple)] :tags {:context "doeff-cluster-test" :role "entry"}}
  "本物の coordinator の process を起こし、GET /state に答えるまで待つため。答え = #(URL process)。止めるのは呼び手。"
  (<- port int (free-port))
  (val url f"http://127.0.0.1:{port}")
  (val log (open (/ tmp-path "coordinator.log") "w"))
  (val process (subprocess.Popen [HY "-m" "doeff_cluster.coordinator.entry.main" "--state-file" (str (/ tmp-path "state.json"))
                                  "--port" (str port)]
                                 :cwd (str ROOT) :stdout log :stderr subprocess.STDOUT))
  (val deadline (+ (time.monotonic) 60))
  (while (< (time.monotonic) deadline)
    (try
      (when (= (. (httpx.get (+ url "/state") :timeout 1.0) status-code) 200)
        (return #(url process)))
      (except [httpx.TransportError] None))
    (when (is-not (.poll process) None)
      (raise (RuntimeError (+ "coordinator が起動しなかった: " (.read-text (/ tmp-path "coordinator.log"))))))
    (time.sleep 0.1))
  (.kill process)
  (raise (RuntimeError "coordinator が 60 秒で答えなかった")))


