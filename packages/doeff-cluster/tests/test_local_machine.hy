;;; 手元の 1 台の cluster(sim/machine.hy の local-machine-cluster・#3031)の検。
;;;
;;; coordinator 1 つと worker 1 つを、配備と同じ deploy/boot.sh でこの機体の子 process として起こし、筋書きを走らせ、終わりに全部止める。
;;;   - 筋書きの中で、coordinator の GET /state に worker が名乗っている(本物の process・本物の HTTP)。
;;;   - 筋書きが終わった後、この検の作業の dir を命令の行に持つ process が 1 つも残っていない(止めが効いた)。
;;;   - 筋書きが例外で終わっても同じく 1 つも残らない。
;;; 失敗ケース: 止め(StopProcess)に「止めた」と答えるだけで止めない壊した答え手を挟むと、終わった後に process が残る
;;; (残った process は検の後片づけで SIGKILL する)。
(require doeff-hy.macros [deftest defk defhandler <- val var])
(val MODULE-TAGS {:context "doeff-cluster-test" :role "entry"})
(import os)
(import signal)
(import socket)
(import pathlib [Path])
(import pytest)
(import doeff [with-handlers Program EffectBase])
(import doeff_core_effects.handlers [await-handler])
(import doeff_core_effects.scheduler [scheduled])
(import doeff_core_effects.http_handlers [http-production-handler])
(import doeff_core_effects.os_file [os-file-handler])
(import doeff_core_effects.os_process [subprocess-handler])
(import doeff_core_effects.process_effects [StopProcess ProcessExited])
(import doeff_time [async-time-handler])
(import doeff_cluster.sim.local [SimWorker ReadCoordinator])
(import doeff_cluster.sim.machine [LocalMachine local-machine-cluster machine-answers machine-run coordinator-url])

(val WORKER "w-local")


(defk free-port []
  {:pre [] :post [(: % int)] :tags {:context "doeff-cluster-test" :role "entry"}}
  "coordinator の受け口に使う空いた port を OS に選ばせるため(閉じてから渡す — tests/served_fixtures の free-port と同じ形)。"
  (with [s (socket.socket socket.AF-INET socket.SOCK-STREAM)]
    (.bind s #("127.0.0.1" 0))
    (get (.getsockname s) 1)))


(defk machine-of [tmp-path]
  {:pre [(: tmp-path Path)] :post [(: % LocalMachine)] :tags {:context "doeff-cluster-test" :role "entry"}}
  "検ごとの作業の dir と空いた port で、worker 1 つの手元の 1 台を組むため。"
  (<- port int (free-port))
  (LocalMachine :work-dir (str tmp-path) :port port :workers #((SimWorker :name WORKER :provides (frozenset ["local"])))
                :boot-seconds 120.0 :stop-grace 15.0))


(defk leftover [tmp-path]
  {:pre [(: tmp-path Path)] :post [(: % (get tuple #(int ...)))] :tags {:context "doeff-cluster-test" :role "entry"}}
  "この検の作業の dir を命令の行に持つ process(起こした coordinator・worker とその子孫)の pid を並べるため(/proc を読む)。"
  (val needle (.encode (str tmp-path)))
  (tuple (gfor entry (os.listdir "/proc")
               :if (.isdigit entry)
               :setv cmdline (try (.read-bytes (Path "/proc" entry "cmdline")) (except [OSError] b""))
               :if (in needle cmdline)
               (int entry))))


(defk kill-leftover [pids]
  {:pre [(: pids (get tuple #(int ...)))] :post [(: % None)] :tags {:context "doeff-cluster-test" :role "entry"}}
  "失敗ケースが残した process を後片づけするため(SIGKILL — 既に終わっていれば何もしない)。"
  (for [pid pids]
    (try (os.kill pid signal.SIGKILL) (except [ProcessLookupError])))
  None)


(defk registered-workers []
  {:pre [] :post [(: % (get tuple #(str ...)))] :tags {:context "doeff-cluster-test" :role "program"}}
  "筋書き: coordinator の /state に名乗っている worker の名を並べるため。"
  (<- state (ReadCoordinator "/state"))
  (tuple (sorted (.get state "workers" {}))))


(defk failing-scenario []
  {:pre [] :post [(: % None)] :tags {:context "doeff-cluster-test" :role "program"}}
  "筋書き: coordinator を 1 度読んでから例外で終わる(例外で終わっても止めが効くかを見るため)。"
  (<- _state (ReadCoordinator "/state"))
  (raise (RuntimeError "筋書きの失敗")))


;; 壊した答え手: StopProcess に「止めた」と答えるだけで、子に signal を送らない(止めを忘れた手元の 1 台の代役)。
(defhandler stop-ignored
  (StopProcess [pid stop-grace]
    (resume (ProcessExited :pid pid :exit-code 0))))


(defk run-with-stop-ignored [scenario machine]
  {:pre [(: scenario (| Program EffectBase)) (: machine LocalMachine)] :post [(: % "scenario の答え")]
   :tags {:context "doeff-cluster-test" :role "entry"}}
  "local-machine-cluster と同じ組で、StopProcess の答え手だけを壊した物に差し替えて走らせるため(失敗ケース)。"
  (<- url str (coordinator-url machine))
  (<- answer (scheduled (with-handlers [(await-handler) (async-time-handler) (http-production-handler) subprocess-handler
                                        os-file-handler (machine-answers url) stop-ignored]
                          (machine-run scenario machine))))
  answer)


(deftest test-a-local-machine-starts-a-coordinator-and-a-worker-and-stops-both-at-the-end [tmp-path]
  (<- machine LocalMachine (machine-of tmp-path))
  (<- names (local-machine-cluster (registered-workers) :machine machine))
  (assert (= names #(WORKER)) names)
  (<- left tuple (leftover tmp-path))
  (assert (= left #()) left))


(deftest test-a-local-machine-stops-everything-when-the-scenario-raises [tmp-path]
  (<- machine LocalMachine (machine-of tmp-path))
  (with [_ (pytest.raises RuntimeError :match "筋書きの失敗")]
    (! (local-machine-cluster (failing-scenario) :machine machine)))
  (<- left tuple (leftover tmp-path))
  (assert (= left #()) left))


(deftest test-a-counterexample-that-only-answers-stop-leaves-processes-behind [tmp-path]
  (<- machine LocalMachine (machine-of tmp-path))
  (<- names (run-with-stop-ignored (registered-workers) machine))
  (<- left tuple (leftover tmp-path))
  (<- (kill-leftover left))
  (assert (= names #(WORKER)) names)
  (assert (> (len left) 0) "止めを答えるだけの壊した答え手でも process が残らない — 検が止めを見ていない"))
