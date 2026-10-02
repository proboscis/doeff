;;; 手元の 1 台の cluster(sim/machine.hy の local-machine-cluster・#3031)の検。
;;;
;;; coordinator 1 つと worker 1 つを、配備と同じ deploy/boot.sh でこの機体の子 process として起こし、筋書きを走らせ、終わりに全部止める。
;;;   - 筋書きの中で、coordinator の GET /state に worker が名乗っている(本物の process・本物の HTTP)。
;;;   - 筋書きが終わった後、この検の作業の dir を命令の行に持つ process が 1 つも残っていない(止めが効いた)。
;;;   - 筋書きが例外で終わっても同じく 1 つも残らない。
;;; 失敗ケース: 止め(StopProcess)に「止めた」と答えるだけで止めない壊した答え手を挟むと、終わった後に process が残る
;;; (残った process は検の後片づけで SIGKILL する)。
;;;
;;; 契約の effect への答え(#3032): 準備の状態(無い Service は Missing)・worker を落とす(1 回目は 1・2 回目は 0)・coordinator を止めて
;;; 作り直す(coordinator の log の起動の行が 2 つ・作り直した後も /state を読める)・網を切る / 固める / 5xx は MachineCannotAnswer。
;;; 失敗ケース: StopCoordinator に何もせず答える壊した答え手を挟むと、起動の行は 1 つのまま。
(require doeff-hy.macros [deftest defk defhandler <- val var])
(require doeff-hy.record [defrecord])
(val MODULE-TAGS {:context "doeff-cluster-test" :role "entry"})
(import os)
(import collections.abc [Callable])
(import dataclasses [dataclass])
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
(import doeff_cluster.shared.intent.cluster_control [ReadinessOf KillWorker StopCoordinator])
(import doeff_cluster.sim.local [SimWorker ReadCoordinator CutWorker StallWorker FailRoute])
(import doeff_cluster.sim.machine [LocalMachine MachineCell MachineCannotAnswer local-machine-cluster machine-answers machine-run
                                   coordinator-url])

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


;; 壊した答え手: StopCoordinator に何もせずに答える(coordinator を止めて作り直すのを忘れた手元の 1 台の代役)。
(defhandler coordinator-stop-ignored
  (StopCoordinator [seconds]
    (resume None)))


(defk run-with-broken [scenario machine broken]
  {:pre [(: scenario (| Program EffectBase)) (: machine LocalMachine) (: broken Callable)] :post [(: % "scenario の答え")]
   :tags {:context "doeff-cluster-test" :role "entry"}}
  "local-machine-cluster と同じ組で、1 つの effect の答え手だけを壊した物(一番内側)に差し替えて走らせるため(失敗ケース)。"
  (<- url str (coordinator-url machine))
  (val cell (MachineCell))
  (<- answer (scheduled (with-handlers [(await-handler) (async-time-handler) (http-production-handler) subprocess-handler
                                        os-file-handler (machine-answers url cell machine) broken]
                          (machine-run scenario cell machine))))
  answer)


(defrecord ContractAnswers
  "筋書き contract-answers の答え: missing = 宣言していない Service の準備の状態・first-kill / second-kill = 同じ worker を 2 度落とした
   数・workers-after = coordinator を作り直した後の /state の worker の名。"
  (#^ str missing)
  (#^ int first-kill)
  (#^ int second-kill)
  (#^ (get tuple #(str ...)) workers-after))


(defk contract-answers []
  {:pre [] :post [(: % ContractAnswers)] :tags {:context "doeff-cluster-test" :role "program"}}
  "筋書き: 契約の effect(準備の状態・worker を落とす・coordinator を止めて作り直す)を順に出し、答えを並べるため。"
  (<- ready (ReadinessOf "not-declared"))
  (<- first-kill int (KillWorker WORKER))
  (<- second-kill int (KillWorker WORKER))
  (<- (StopCoordinator 0.5))
  (<- state (ReadCoordinator "/state"))
  (ContractAnswers :missing ready.state :first-kill first-kill :second-kill second-kill
                   :workers-after (tuple (sorted (.get state "workers" {})))))


(defk coordinator-starts [tmp-path]
  {:pre [(: tmp-path Path)] :post [(: % int)] :tags {:context "doeff-cluster-test" :role "entry"}}
  "coordinator の log の起動の行(「… で受けます」— 起きるたびに 1 行)を数えるため(作り直しを数える)。"
  (.count (.read-text (/ tmp-path "coordinator" "coordinator.log") :encoding "utf-8") "で受けます"))


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
  (<- names (run-with-broken (registered-workers) machine stop-ignored))
  (<- left tuple (leftover tmp-path))
  (<- (kill-leftover left))
  (assert (= names #(WORKER)) names)
  (assert (> (len left) 0) "止めを答えるだけの壊した答え手でも process が残らない — 検が止めを見ていない"))


(deftest test-a-local-machine-answers-readiness-kills-a-worker-and-remakes-the-coordinator [tmp-path]
  (<- machine LocalMachine (machine-of tmp-path))
  (<- answers ContractAnswers (local-machine-cluster (contract-answers) :machine machine))
  (assert (= answers.missing "Missing") answers)
  (assert (= #(answers.first-kill answers.second-kill) #(1 0)) answers)
  ;; 落とした worker は coordinator の作り直しの後も、置き場の行として名が残る(sim の KillWorker と同じく、lease が切れるまでは居る)。
  (assert (in WORKER answers.workers-after) answers)
  (<- starts int (coordinator-starts tmp-path))
  (assert (= starts 2) starts)
  (<- left tuple (leftover tmp-path))
  (assert (= left #()) left))


(deftest test-a-local-machine-refuses-to-cut-stall-or-fail-routes [tmp-path]
  (<- machine LocalMachine (machine-of tmp-path))
  (with [_ (pytest.raises MachineCannotAnswer :match "CutWorker")]
    (! (local-machine-cluster (CutWorker WORKER 1.0) :machine machine)))
  (<- left tuple (leftover tmp-path))
  (assert (= left #()) left))


(deftest test-a-counterexample-that-does-not-remake-the-coordinator-starts-it-only-once [tmp-path]
  (<- machine LocalMachine (machine-of tmp-path))
  (<- answers ContractAnswers (run-with-broken (contract-answers) machine coordinator-stop-ignored))
  (<- starts int (coordinator-starts tmp-path))
  (assert (= answers.missing "Missing") answers)
  (assert (= starts 1) "StopCoordinator に何もせず答える壊した答え手でも起動の行が 2 つ — 検が作り直しを見ていない"))
