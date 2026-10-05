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
;;;
;;; 宣言の remote を手元の checkout から読む(git-sources): 実行環境の宣言の url を配備と同じ remote の綴り(届かない名)のまま置き、
;;; git-sources でその url を検の bare の repo へ向けると、worker は remote へ取りに行かずに repo を取り込む(mirror が 1 つ)。失敗ケース:
;;; git-sources を置かないと、worker は届かない remote を取りに行って repo-unreachable を名乗り、mirror は無い。
(require doeff-hy.macros [deftest defk defhandler <- val var])
(require doeff-hy.record [defrecord])
(val MODULE-TAGS {:context "doeff-cluster-test" :role "entry"})
(import os)
(import hashlib)
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
(import doeff_cluster.shared.intent.cluster_control [ServiceReadiness ReadinessOf KillWorker StopCoordinator Redeclare Crash
                                                     AwaitReadiness ServiceFailed ReadinessWaitExpired AwaitJobProcess JobProcessSeen
                                                     JobProcessWaitExpired])
(import doeff_cluster.shared.intent.service_model [System])
(import doeff_cluster.shared.intent.runtime_env_model [RuntimeEnv RepoCheckout PythonProject])
(import tests.fixtures.machine_app [pings machine-foundation])
(import doeff_cluster.sim.machine :as machine-module)
(import doeff_cluster.sim.local [SimWorker ReadCoordinator CutWorker StallWorker FailRoute])
(import doeff_cluster.sim.machine [LocalMachine MachineCell MachineCannotAnswer GitSource local-machine-cluster machine-answers machine-run
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
  (LocalMachine :work-dir (str tmp-path) :port port :workers #((SimWorker :name WORKER :provides (frozenset ["local"]) :task-reserve 0))
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
;; 実行環境の宣言(#3042)が名指す uv の project の 2 file(lock の sha256 を宣言に載せ、worker が取り出した木の lock と照らす)。
(val APP-PYPROJECT "[project]\nname = \"machine-app\"\nversion = \"0\"\nrequires-python = \">=3.14\"\n")
(val APP-LOCK "version = 1\nrequires-python = \">=3.14\"\n")


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
  (.write-text (/ work "pyproject.toml") APP-PYPROJECT :encoding "utf-8")
  (.write-text (/ work "uv.lock") APP-LOCK :encoding "utf-8")
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


(defk runtime-machine-of [tmp-path repo]
  {:pre [(: tmp-path Path) (: repo AppRepo)] :post [(: % LocalMachine)] :tags {:context "doeff-cluster-test" :role "entry"}}
  "worker 1 つの手元の 1 台に、実行環境の宣言(app の repo とその commit と uv の lock — 配備と同じ道)を足すため(#3042)。"
  (<- machine LocalMachine (machine-of tmp-path))
  (val env (RuntimeEnv :repos #((RepoCheckout :name "app" :url repo.url :commit repo.sha))
                       :project (PythonProject :repo "app" :path "." :lock-sha256 (.hexdigest (hashlib.sha256 (.encode APP-LOCK)))
                                               :python "3.14")
                       :import-roots #("app/.")))
  (replace machine :revision repo.sha :runtime-env env))


(defrecord CrashAnswers
  "筋書き declared-and-crashed の答え: declared = Redeclare の答え・first = 宣言の後の準備の待ちの答え・before = 最初の process の待ちの
   答え・crashed = Crash の答え・after = 起こし直しの次の process の待ちの答え・again = その後の準備の待ちの答え・never-ready = 来ない
   状態(Missing)の待ちの答え・no-restart = 落としていない job の次の process の待ちの答え(後の 2 つは失敗ケース — 期限で値が返る)。"
  (#^ (get tuple #(str ...)) declared)
  (#^ (| ServiceReadiness ServiceFailed ReadinessWaitExpired) first)
  (#^ (| JobProcessSeen JobProcessWaitExpired) before)
  (#^ int crashed)
  (#^ (| JobProcessSeen JobProcessWaitExpired) after)
  (#^ (| ServiceReadiness ServiceFailed ReadinessWaitExpired) again)
  (#^ (| ServiceReadiness ServiceFailed ReadinessWaitExpired) never-ready)
  (#^ (| JobProcessSeen JobProcessWaitExpired) no-restart))


(defk seen-pid [seen]
  {:pre [(: seen (| JobProcessSeen JobProcessWaitExpired))] :post [(: % int)] :tags {:context "doeff-cluster-test" :role "judgment"}}
  "待ちの答えから process の pid を引くため(名乗られずに期限が来ていれば、その答えを名指して止める — 後の待ちの前提が崩れている)。"
  (match seen
    (JobProcessSeen :pid pid) pid
    (JobProcessWaitExpired) (raise (AssertionError (+ "job の process が名乗られない: " (repr seen))))))


(defk declared-and-crashed [system]
  {:pre [(: system System)] :post [(: % CrashAnswers)] :tags {:context "doeff-cluster-test" :role "program"}}
  "筋書き: 系を宣言して Ready を待ち、job を落として worker が起こし直す(pid が替わる)のを見て、もう一度 Ready を待つため。最後に、
   来ない状態と起きない次の process を短く待って、期限で値が返ることを見る(読み直しのループは書かない — 待つのは cluster の handler・#3053)。"
  ;; 待ちの上限は、壊した答え手で assert が pytest の打ち切り(60 秒)より先に鳴る長さ(普通の走りは全体で十数秒)。
  (<- declared tuple (Redeclare system))
  (<- first (| ServiceReadiness ServiceFailed ReadinessWaitExpired) (AwaitReadiness JOB "Ready" 30.0))
  (<- before (| JobProcessSeen JobProcessWaitExpired) (AwaitJobProcess JOB #() 10.0))
  (<- before-pid int (seen-pid before))
  (<- crashed int (Crash JOB))
  (<- after (| JobProcessSeen JobProcessWaitExpired) (AwaitJobProcess JOB #(before-pid) 20.0))
  (<- after-pid int (seen-pid after))
  (<- again (| ServiceReadiness ServiceFailed ReadinessWaitExpired) (AwaitReadiness JOB "Ready" 15.0))
  (<- never (| ServiceReadiness ServiceFailed ReadinessWaitExpired) (AwaitReadiness JOB "Missing" 2.0))
  (<- none (| JobProcessSeen JobProcessWaitExpired) (AwaitJobProcess JOB #(after-pid) 2.0))
  (CrashAnswers :declared declared :first first :before before :crashed crashed :after after :again again :never-ready never
                :no-restart none))


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
  (assert (and (isinstance answers.first ServiceReadiness) (= answers.first.state "Ready")) answers)
  (assert (= answers.crashed 1) answers)
  (assert (and (isinstance answers.after JobProcessSeen) (!= answers.after.pid answers.before.pid)) answers)
  (assert (and (isinstance answers.again ServiceReadiness) (= answers.again.state "Ready")) answers)
  ;; 失敗ケース: 来ない状態・起きない次の process の待ちは、期限で値が返る(黙って待ち続けない)。
  (assert (and (isinstance answers.never-ready ReadinessWaitExpired) (= answers.never-ready.last.state "Ready")) answers.never-ready)
  (assert (isinstance answers.no-restart JobProcessWaitExpired) answers.no-restart)
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


;; --- 実行環境の宣言(#3042)— 配備と同じ道: 宣言に実行環境を載せ、worker がその repo を取り込む(鍵の表は url を断らない)---
;; root の準備から Ready までの本物の道(空の uv の cache で uv sync が 36 秒前後 — 60 秒に入らない)は日次の側(#3033)で通す。ここは
;; 宣言と鍵の表と取り込み(uv の前の段)までを見る。

;; 取り込み(mirror)の段の失敗の種類。これを名乗らなければ取り込みの段を越えた(後の段の名乗り — 検の小さな偽の lock の lock-stale
;; など — は取り込みの外)。
(val MIRROR-FAILURES #("repo-unreachable" "commit-missing"))

(defrecord PrepareSeen
  "筋書き declared-and-preparing の答え: declared = Redeclare の答え・failure-kind = worker が job の準備で名乗った失敗の種類(空 = 時間内に
   名乗り無し — まだ準備の途中)。"
  (#^ (get tuple #(str ...)) declared)
  (#^ str failure-kind))


(defk failure-kind-within [name seconds]
  {:pre [(: name str) (: seconds float)] :post [(: % str)] :tags {:context "doeff-cluster-test" :role "program"}}
  "筋書き: worker が名乗る job name の行に失敗の種類が出るまで問い直すため(seconds を過ぎたら空 — まだ準備の途中)。"
  (var kind "")
  (var waited 0.0)
  (while (and (not kind) (< waited seconds))
    (<- state (ReadCoordinator "/state"))
    (:= kind (next (gfor status (.values (.get state "statuses" {}))
                         row (.get status "jobs" [])
                         :if (= (.get row "name") name)
                         (str (.get row "failureKind" "")))
                   ""))
    (when (not kind)
      (<- (Delay 0.5))
      (:= waited (+ waited 0.5))))
  kind)


(defk declared-and-preparing [system]
  {:pre [(: system System)] :post [(: % PrepareSeen)] :tags {:context "doeff-cluster-test" :role "program"}}
  "筋書き: 実行環境を載せて系を宣言し、worker が準備で失敗を名乗るか 15 秒経つまで見るため(取り込みの段の失敗は直ぐ名乗る)。"
  (<- declared tuple (Redeclare system))
  (<- kind str (failure-kind-within JOB 15.0))
  (PrepareSeen :declared declared :failure-kind kind))


;; 空の鍵の表: 実行環境の repo を鍵の表に載せない(--repo-keys を空で渡す worker の代役 — repo-key-table の差し替え)。
(defk no-key-table [machine]
  {:pre [(: machine LocalMachine)] :post [(: % str)] :tags {:context "doeff-cluster-test" :role "judgment"}}
  "空の鍵の表(WORKER_REPOS を空にする — どの repo にも鍵を結ばない)で worker を起こすため。"
  "")


(defk home-access-mtime []
  {:pre [] :post [(: % (| int None))] :tags {:context "doeff-cluster-test" :role "entry"}}
  "利用者の HOME の worker の鍵の表(boot.sh の既定の置き場)の書いた刻を読むため(無ければ None — 手元の 1 台が書き換えないことを見る)。"
  (val home-file (/ (Path.home) ".doeff-worker-repos" "repo-keys.json"))
  (if (.exists home-file) (. (.stat home-file) st-mtime-ns) None))


(defk mirrors-of [tmp-path]
  {:pre [(: tmp-path Path)] :post [(: % (get tuple #(str ...)))] :tags {:context "doeff-cluster-test" :role "entry"}}
  "worker が実行環境の repo を取り込んだ mirror(state の下の mirrors/*.git)を並べるため。"
  (tuple (sorted (gfor p (.rglob (/ tmp-path "workers" WORKER) "mirrors/*.git") (str p)))))


(deftest test-a-local-machine-declares-the-runtime-env-and-its-worker-takes-in-the-repo [tmp-path]
  (<- repo AppRepo (app-repo tmp-path))
  (<- machine LocalMachine (runtime-machine-of tmp-path repo))
  (<- before (| int None) (home-access-mtime))
  (<- seen PrepareSeen (local-machine-cluster (declared-and-preparing (pings machine-foundation)) :machine machine))
  (assert (= seen.declared #(JOB)) seen)
  (assert (not-in seen.failure-kind MIRROR-FAILURES) seen)
  (<- mirrors tuple (mirrors-of tmp-path))
  (assert (= (len mirrors) 1) mirrors)
  (val keys (/ tmp-path "workers" WORKER "access" "repo-keys.json"))
  (assert (in repo.url (.read-text keys :encoding "utf-8")) (.read-text keys :encoding "utf-8"))
  (<- after (| int None) (home-access-mtime))
  (assert (= before after) "手元の 1 台の worker が利用者の HOME の鍵の表を書き換えた")
  (<- left tuple (leftover tmp-path))
  (assert (= left #()) left))


(deftest test-a-worker-with-an-empty-key-table-still-takes-in-the-repo [tmp-path monkeypatch]
  ;; 鍵の表は url を断らない(2026-10-05 に断る分岐と repo-denied を外した): 表が空の worker(--repo-keys が空)も、表に無い
  ;; 実行環境の repo を鍵なしで clone し、取り込みの段を越える。
  (<- repo AppRepo (app-repo tmp-path))
  (<- machine LocalMachine (runtime-machine-of tmp-path repo))
  (.setattr monkeypatch machine-module "repo_key_table" no-key-table)
  (<- seen PrepareSeen (local-machine-cluster (declared-and-preparing (pings machine-foundation)) :machine machine))
  (assert (= seen.declared #(JOB)) seen)
  (assert (not-in seen.failure-kind MIRROR-FAILURES)
          (.format "鍵の表が空の worker が取り込みの段で止まった({!r})— 表に無い url を断っている" seen))
  (<- mirrors tuple (mirrors-of tmp-path))
  (assert (= (len mirrors) 1) mirrors)
  (<- left tuple (leftover tmp-path))
  (assert (= left #()) left))


;; 実行環境の宣言に書く remote の綴り(届かない名 — 手元の 1 台の worker は remote へ取りに行かない)。
(val REMOTE-URL "https://example.invalid/proboscis/machine-app.git")


(defk remote-runtime-machine-of [tmp-path repo sources]
  {:pre [(: tmp-path Path) (: repo AppRepo) (: sources (get tuple #(GitSource ...)))] :post [(: % LocalMachine)]
   :tags {:context "doeff-cluster-test" :role "entry"}}
  "実行環境の宣言の url を配備と同じ remote の綴り REMOTE-URL にした手元の 1 台を組むため(sources = その url を手元の checkout から
   読ませる組 — 空なら置かない)。"
  (<- machine LocalMachine (runtime-machine-of tmp-path repo))
  (val env (replace machine.runtime-env :repos #((RepoCheckout :name "app" :url REMOTE-URL :commit repo.sha))))
  (replace machine :runtime-env env :git-sources sources))


(deftest test-a-local-machine-reads-the-declared-remote-from-the-local-checkout [tmp-path]
  (<- repo AppRepo (app-repo tmp-path))
  (<- machine LocalMachine (remote-runtime-machine-of tmp-path repo #((GitSource :remote REMOTE-URL :path repo.url))))
  (<- seen PrepareSeen (local-machine-cluster (declared-and-preparing (pings machine-foundation)) :machine machine))
  (assert (= seen.declared #(JOB)) seen)
  ;; 取り込み(mirror)の段を越えた — 届かない(repo-unreachable)も commit の欠け(commit-missing)も名乗らない。
  (assert (not-in seen.failure-kind MIRROR-FAILURES) seen)
  (<- mirrors tuple (mirrors-of tmp-path))
  (assert (= (len mirrors) 1) mirrors)
  (<- left tuple (leftover tmp-path))
  (assert (= left #()) left))


(deftest test-a-counterexample-without-git-sources-fetches-the-unreachable-remote-and-fails [tmp-path]
  (<- repo AppRepo (app-repo tmp-path))
  (<- machine LocalMachine (remote-runtime-machine-of tmp-path repo #()))
  (<- seen PrepareSeen (local-machine-cluster (declared-and-preparing (pings machine-foundation)) :machine machine))
  (assert (= seen.failure-kind "repo-unreachable")
          (.format "git-sources を置かない形で、届かない remote を名乗らなかった({!r})— 検が手元の checkout からの読みを見ていない"
                   seen))
  (<- mirrors tuple (mirrors-of tmp-path))
  (assert (= mirrors #()) mirrors)
  (<- left tuple (leftover tmp-path))
  (assert (= left #()) left))
