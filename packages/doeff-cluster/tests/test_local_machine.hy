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
;;;
;;; 系の宣言と job を落とす(#3040): 検の git の repo に app の module(tests/fixtures/machine_app.hy)を push し、その commit を宣言の版・
;;; その repo を worker の版の木(CODE_REPO_URL)にする。Redeclare で宣言した service が手元の worker の上で起きて Ready になり、Crash で
;;; job を落とすと worker が起こし直し(pid が替わる)、もう一度 Ready になる。失敗ケース: Redeclare に宣言を送らずに答える壊した答え手を
;;; 挟むと、5 秒後も Service は Missing のまま。
(require doeff-hy.macros [deftest defk defhandler <- val var])
(require doeff-hy.record [defrecord])
(val MODULE-TAGS {:context "doeff-cluster-test" :role "entry"})
(import os)
(import subprocess)
(import collections.abc [Callable])
(import dataclasses [dataclass replace])
(import signal)
(import socket)
(import pathlib [Path])
(import pytest)
(import doeff [with-handlers Program EffectBase])
(import doeff_core_effects.handlers [await-handler slog-handler])
(import doeff_core_effects.scheduler [scheduled])
(import doeff_core_effects.http_handlers [http-production-handler])
(import doeff_core_effects.os_file [os-file-handler])
(import doeff_core_effects.os_process [subprocess-handler])
(import doeff_core_effects.process_effects [StopProcess ProcessExited])
(import doeff_time [Delay async-time-handler])
(import doeff_cluster.shared.intent.cluster_control [ServiceReadiness ReadinessOf KillWorker StopCoordinator Redeclare Crash])
(import doeff_cluster.shared.intent.service_model [System])
(import tests.fixtures.machine_app [pings machine-foundation])
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
  (<- answer (scheduled (with-handlers [(await-handler) slog-handler (async-time-handler) (http-production-handler) subprocess-handler
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


;; --- 系の宣言(Redeclare)と job を落とす(Crash)— #3040 ---

;; worker が版の木から取り出す app の module(検の git の repo へ、この package の中と同じ path で写して push する — 検の process と
;; worker の子が同じ名 tests.fixtures.machine_app で import する。詰めた Program は module の最上位の関数を名で運ぶ)と、その系の job の名。
(val TESTS-DIR (. (Path __file__) parent))
(val APP-FILES #("tests/__init__.py" "tests/fixtures/__init__.py" "tests/fixtures/machine_app.hy"))
(val JOB "ping")


(defrecord AppRepo
  "検の app の repo: url = bare の repo(worker の CODE_REPO_URL)・sha = push した commit(宣言の版)。"
  (#^ str url)
  (#^ str sha))


(defk git [cwd args]
  {:pre [(: cwd Path) (: args (get tuple #(str ...)))] :post [(: % str)] :tags {:context "doeff-cluster-test" :role "entry"}}
  "検の app の repo を作るために git を 1 回呼ぶため(tests/careful_rig.hy の git と同じ形)。"
  (val done (subprocess.run ["git" "-C" (str cwd) "-c" "user.name=t" "-c" "user.email=t@example.invalid" #* args]
                            :capture-output True :text True :check True))
  (.strip done.stdout))


(defk app-repo [tmp-path]
  {:pre [(: tmp-path Path)] :post [(: % AppRepo)] :tags {:context "doeff-cluster-test" :role "entry"}}
  "bare の repo と、それを clone した checkout を作り、app の module を package と同じ path で commit して push するため(worker はこの
   commit の木を取り出す)。"
  (val bare (/ tmp-path "remote" "app.git"))
  (val work (/ tmp-path "app-work"))
  (.mkdir bare.parent :parents True)
  (<- (git tmp-path #("init" "-q" "--bare" (str bare))))
  (<- (git tmp-path #("clone" "-q" (str bare) (str work))))
  (for [rel APP-FILES]
    (val target (/ work rel))
    (.mkdir target.parent :parents True :exist-ok True)
    (.write-text target (.read-text (/ TESTS-DIR.parent rel) :encoding "utf-8") :encoding "utf-8"))
  (<- (git work #("add" "-A")))
  (<- (git work #("commit" "-q" "-m" "machine app")))
  (<- (git work #("push" "-q" "origin" "HEAD:main")))
  (<- sha str (git work #("rev-parse" "HEAD")))
  (AppRepo :url (str bare) :sha sha))


(defk code-machine-of [tmp-path repo]
  {:pre [(: tmp-path Path) (: repo AppRepo)] :post [(: % LocalMachine)] :tags {:context "doeff-cluster-test" :role "entry"}}
  "worker 1 つの手元の 1 台に、job の code の道(版の木の repo と宣言の版)を足すため。"
  (<- machine LocalMachine (machine-of tmp-path))
  (replace machine :code-repo repo.url :revision repo.sha))


(defk ready-within [name seconds]
  {:pre [(: name str) (: seconds float)] :post [(: % ServiceReadiness)] :tags {:context "doeff-cluster-test" :role "program"}}
  "筋書き: Service name の準備の状態が Ready になるまで問い直すため(seconds を過ぎたら最後の答えを返す)。"
  (<- first ServiceReadiness (ReadinessOf name))
  (var seen first)
  (var waited 0.0)
  (while (and (!= seen.state "Ready") (< waited seconds))
    (<- (Delay 0.5))
    (:= waited (+ waited 0.5))
    (<- again ServiceReadiness (ReadinessOf name))
    (:= seen again))
  seen)


(defk job-pids [name]
  {:pre [(: name str)] :post [(: % (get tuple #(int ...)))] :tags {:context "doeff-cluster-test" :role "program"}}
  "筋書き: coordinator の /state に worker が名乗っている job name の pid を並べるため。"
  (<- state (ReadCoordinator "/state"))
  (tuple (sorted (gfor status (.values (.get state "statuses" {}))
                       row (.get status "jobs" [])
                       :if (and (= (.get row "name") name) (isinstance (.get row "pid") int))
                       (get row "pid")))))


(defk pids-other-than [name before seconds]
  {:pre [(: name str) (: before (get tuple #(int ...))) (: seconds float)] :post [(: % (get tuple #(int ...)))]
   :tags {:context "doeff-cluster-test" :role "program"}}
  "筋書き: job name に before に無い pid が名乗られるまで問い直すため(before が空なら、pid が 1 つでも名乗られるまで — 答え = その時の pid)。"
  (<- first tuple (job-pids name))
  (var seen first)
  (var waited 0.0)
  (while (and (not (- (set seen) (set before))) (< waited seconds))
    (<- (Delay 0.5))
    (:= waited (+ waited 0.5))
    (<- again tuple (job-pids name))
    (:= seen again))
  seen)


(defrecord CrashAnswers
  "筋書き declared-and-crashed の答え: declared = Redeclare の答え・first-ready = 宣言の後の準備の状態・before = 落とす前の job の pid・
   crashed = Crash の答え・after = 起こし直した後の job の pid・ready-again = 起こし直した後の準備の状態。"
  (#^ (get tuple #(str ...)) declared)
  (#^ str first-ready)
  (#^ (get tuple #(int ...)) before)
  (#^ int crashed)
  (#^ (get tuple #(int ...)) after)
  (#^ str ready-again))


(defk declared-and-crashed [system]
  {:pre [(: system System)] :post [(: % CrashAnswers)] :tags {:context "doeff-cluster-test" :role "program"}}
  "筋書き: 系を宣言して Ready を待ち、job を落として worker が起こし直す(pid が替わる)のを見て、もう一度 Ready を待つため。"
  ;; 待ちの上限は、壊した答え手で assert が pytest の打ち切り(60 秒)より先に鳴る長さ(普通の走りは全体で十数秒)。
  (<- declared tuple (Redeclare system))
  (<- first ServiceReadiness (ready-within JOB 30.0))
  ;; Ready にならなければ落とさずに答える(落とす前提が崩れている — 検はその場の答えで赤になる)。
  (when (!= first.state "Ready")
    (return (CrashAnswers :declared declared :first-ready first.state :before #() :crashed 0 :after #() :ready-again first.state)))
  (<- before tuple (pids-other-than JOB #() 10.0))
  (<- crashed int (Crash JOB))
  (<- after tuple (pids-other-than JOB before 20.0))
  (<- again ServiceReadiness (ready-within JOB 15.0))
  (CrashAnswers :declared declared :first-ready first.state :before before :crashed crashed :after after :ready-again again.state))


;; 壊した答え手: Redeclare に、宣言を coordinator へ送らずに「宣言した物は無い」と答える(宣言を忘れた手元の 1 台の代役)。
(defhandler redeclare-ignored
  (Redeclare [system]
    (resume #())))


(defrecord DeclaredReadiness
  "筋書き declared-then-readiness の答え: declared = Redeclare の答え・state = 5 秒待った後の準備の状態。"
  (#^ (get tuple #(str ...)) declared)
  (#^ str state))


(defk declared-then-readiness [system]
  {:pre [(: system System)] :post [(: % DeclaredReadiness)] :tags {:context "doeff-cluster-test" :role "program"}}
  "筋書き: 系を宣言し、5 秒待って準備の状態を読むため(失敗ケース — 宣言が届かなければ Missing のまま)。"
  (<- declared tuple (Redeclare system))
  (<- (Delay 5.0))
  (<- seen ServiceReadiness (ReadinessOf JOB))
  (DeclaredReadiness :declared declared :state seen.state))


(deftest test-a-local-machine-declares-a-system-from-its-code-repo-and-the-worker-restarts-a-crashed-job [tmp-path]
  (<- repo AppRepo (app-repo tmp-path))
  (<- machine LocalMachine (code-machine-of tmp-path repo))
  (val system (pings machine-foundation))
  (<- answers CrashAnswers (local-machine-cluster (declared-and-crashed system) :machine machine))
  (assert (= answers.declared #(JOB)) answers)
  (assert (= answers.first-ready "Ready") answers)
  (assert (= answers.crashed 1) answers)
  (assert (and answers.after (not (& (set answers.before) (set answers.after)))) answers)
  (assert (= answers.ready-again "Ready") answers)
  (<- left tuple (leftover tmp-path))
  (assert (= left #()) left))


(deftest test-a-counterexample-that-does-not-send-the-declaration-never-becomes-ready [tmp-path]
  (<- repo AppRepo (app-repo tmp-path))
  (<- machine LocalMachine (code-machine-of tmp-path repo))
  (val system (pings machine-foundation))
  (<- answers DeclaredReadiness (run-with-broken (declared-then-readiness system) machine redeclare-ignored))
  (assert (= answers.declared #()) answers)
  (assert (= answers.state "Missing") "宣言を送らない壊した答え手でも Service が在る — 検が宣言を見ていない")
  (<- left tuple (leftover tmp-path))
  (assert (= left #()) left))
