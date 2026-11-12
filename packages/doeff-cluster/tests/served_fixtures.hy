;;; 検の道具: 本物の coordinator の process を起こす(test_detached.hy の served の組 — conftest.py の served_coordinator が使う)。
(require doeff-hy.macros [defk <- val])
(import os)
(import socket)
(import subprocess)
(import sys)
(import time)
(import pathlib [Path])
(import httpx)
(import pytest)

;; この file の待ちは実時間が対象(何が対象かは名簿 tests/realtime_tests.toml のこの file の行)— realtime を名乗る file の中の眠りを、
;; 検の眠りの規則(doeff-linter の DOEFF211)は当てない。
(val pytestmark pytest.mark.realtime)

(setv ROOT (. (Path (os.path.abspath __file__)) parent parent))   ; この package の根(子 process の cwd)
(setv HY (str (/ (. (Path sys.executable) parent) "hy")))


(defk free-port []
  {:pre [] :post [(: % int)] :tags {:context "doeff-cluster-test" :role "entry"}}
  "OS に空いている TCP の port を 1 つ選ばせ、その番号を返すため(coordinator の process に渡す)。"
  (with [s (socket.socket)]
    (.bind s #("127.0.0.1" 0))
    (get (.getsockname s) 1)))


;; 共有の coordinator を使う検の worker の名(test_detached の served の組・test_served_program・test_task_programs)。
(val SERVED-WORKERS #("w1" "served-program-worker" "served-task-program-worker"))


(defk start-coordinator [tmp-path]
  {:pre [(: tmp-path Path)] :post [(: % tuple)] :tags {:context "doeff-cluster-test" :role "entry"}}
  "本物の coordinator の process を起こし、GET /state に答えるまで待つため。答え = #(URL process)。止めるのは呼び手。"
  (<- port int (free-port))
  (val url f"http://127.0.0.1:{port}")
  (val log (open (/ tmp-path "coordinator.log") "w"))
  (val process (subprocess.Popen [HY "-m" "doeff_cluster._coordinator.entry.main" "--state-file" (str (/ tmp-path "state.json"))
                                  "--port" (str port) "--notice-broker" "memory"]
                                 :cwd (str ROOT) :stdout log :stderr subprocess.STDOUT))
  (val deadline (+ (time.monotonic) 60))
  (while (< (time.monotonic) deadline)
    (try
      (when (= (. (httpx.get (+ url "/state") :timeout 1.0) status-code) 200)
        ;; 共有の coordinator を使う検の worker の tasks 区画を、site の宣言の代役として資源の口で書く(置き方は区画の申告の和で
        ;; 置き、区画を持たない worker には門の内の task を置かない — ADR-DOE-CLUSTER-005 R2・R5)。
        (for [name SERVED-WORKERS]
          (val made (httpx.post (+ url "/resources/MachineCompartments") :timeout 10.0 :headers {"X-Actor" "site"}
                                :json {"name" name
                                       "spec" {"compartments" [{"use" "tasks" "worker" name "layer" "jobs"
                                                                "memoryMiB" 65536 "cpuMillis" 16000}]}}))
          (when (!= made.status-code 201)
            (.kill process)
            (raise (RuntimeError (+ "区画の宣言を書けなかった: " made.text)))))
        (return #(url process)))
      (except [httpx.TransportError] None))
    (when (is-not (.poll process) None)
      (raise (RuntimeError (+ "coordinator が起動しなかった: " (.read-text (/ tmp-path "coordinator.log"))))))
    (time.sleep 0.1))
  (.kill process)
  (raise (RuntimeError "coordinator が 60 秒で答えなかった")))


