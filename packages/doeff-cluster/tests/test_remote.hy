;; RemoteJob: 手元の sim-cluster(偽の宿が task を別の process — 別のスコープ — で走らせる)と、worker の子 process の入口(job_entry task)を
;; 通す往復。task も Program の値 1 つで、handler は Program が自分で並べる(ADR-DOE-CLUSTER-001 R1・R2)。実行先は handler を足さず、呼び手の
;; handler も継がない(以前の remote-inline は継いでいたので消した — 段 5)。handler の値は詰めない(R3b — encode-program が
;; UnsendableProgram で断る)。
(require doeff-hy.macros [deftest defk <- val var])
(import json)
(import os)
(import pathlib [Path])
(import subprocess)
(import sys)
(import threading)
(import pytest)
(import doeff [run Program with-handlers])
(import doeff_core_effects.handlers [reader])
(import doeff_time [Delay])
(import doeff_cluster.remote_model [RemoteJob UnsendableProgram VersionMismatch RemoteJobFailed
                                          TaskSucceeded TaskFailed encode-program decode-program decode-outcome version-mismatch])
(import doeff_cluster.process_versions [current-versions])
(import doeff_cluster.remote_model [program-sha])
(import doeff_cluster.handlers [write-program-file])
(import doeff_cluster.local [sim-cluster SharedRows ProcessesOf])
(import tests.fixtures.envs [sim-foundation])
(import tests.fixtures.sim_programs [delegating])
(import tests.fixtures.services [self-contained-program holding-program bare-program])
(import tests.fixtures.entry_programs [answer-base based-add counter-program boom-program])

(val NET (frozenset ["net"]))


(defk watch-delegator []
  {:pre [] :post [(: % dict)] :tags {:context "doeff-cluster-test" :role "program"}}
  "筋書き: task が走り終わるまで 20 秒待ち、delegator が盤に書いた結果と、delegator の process の数を読む。"
  (<- (Delay 20.0))
  (<- rows dict (SharedRows "remote/"))
  (<- processes tuple (ProcessesOf "delegator"))
  (| rows {"processes" (len processes)}))


(deftest test-a-remote-job-runs-in-its-own-process-and-its-answer-comes-back
  ;; service の中から出した task は、別の process(別のスコープ)で走り、答えが呼び手へ返る。自分で reader を並べた add-task は
  ;; 100 + 3。reader を並べない orphan-task は、呼び手が並べた reader(base = 1)を継がないので答えが無く、呼び手には失敗が届く
  ;; (呼び手の handler を継ぐ remote-inline なら黙って 1 と答えていた)。
  (<- rows dict (sim-cluster (delegating sim-foundation) (watch-delegator)))
  (assert (= (get rows "remote/result" "sum") 103) rows)
  (val orphan (get rows "remote/result" "orphan"))
  (assert (.startswith orphan "失敗") orphan)
  (assert (in "Ask" orphan) orphan)
  ;; 呼び手の service は 1 度だけ起きた(task の失敗で落ちていない)。
  (assert (= (get rows "processes") 1) rows))


(deftest test-program-holding-a-lock-is-refused-before-sending
  (val lock (threading.Lock))
  (defk holds-lock []
    {:pre [] :post [(: % int)] :tags {:context "doeff-cluster-test" :role "entry"}}
    (with [lock] 1))
  (with [(pytest.raises UnsendableProgram)]
    (encode-program (holds-lock))))


(deftest test-program-holding-a-file-is-refused-before-sending [tmp-path]
  ;; cloudpickle の既定は読みの file を中身の写し(StringIO)に黙って替える(実測 2026-09-23)。送り手で断る。
  (val f (open (/ tmp-path "x.txt") "w+"))
  (defk holds-file []
    {:pre [] :post [(: % str)] :tags {:context "doeff-cluster-test" :role "entry"}}
    (str f))
  (try
    (with [(pytest.raises UnsendableProgram :match "file を捕まえている")]
      (encode-program (holds-file)))
    (finally (.close f))))


(deftest test-a-program-holding-a-handler-value-is-refused-before-sending
  ;; handler の値(defhandler の値そのもの・handler を作る関数を呼んだ値)を捕まえた Program は詰めない(R3b・改訂 1 の D)。
  (with [raised (pytest.raises UnsendableProgram)]
    (encode-program (holding-program answer-base 1)))
  (assert (in "handler の値" (str raised.value)) (str raised.value))
  (with [raised (pytest.raises UnsendableProgram)]
    (encode-program (holding-program (reader {"base" 1}) 1)))
  (assert (in "Program の本体の中で関数を呼んで作る" (str raised.value)) (str raised.value))
  ;; RemoteJob の送り手(本番の remote-cluster と sim の宿が通る 1 か所 encode-program)も同じ。
  (with [(pytest.raises UnsendableProgram)]
    (encode-program (RemoteJob (holding-program answer-base 1) :needs NET))))


(deftest test-a-program-that-makes-its-handlers-in-its-body-is-sent-and-runs
  ;; handler を本体の中で関数を呼んで作る Program は詰められ、解いた値だけで走る(何も足さない run)。
  (val blob (encode-program (self-contained-program 5)))
  (assert (= (run (decode-program blob)) 15)))


(deftest test-version-mismatch-names-the-differing-key
  (val mine (current-versions))
  (assert (is (version-mismatch mine mine) None))
  (val message (version-mismatch (| mine {"doeff" "0.0.0"}) mine))
  (assert (is-not message None) "版の違う鍵があるのに食い違いの文が無い")
  (assert (in "doeff: 送り手 0.0.0" message))
  (assert (not-in "python" message)))


;; --- 子 process の入口(worker が起動する形そのもの)を通す ----------------------------------------

(val ROOT (. (Path (os.path.abspath __file__)) parent parent))   ; この package の根(検の module は tests.* の名)
(val HY (str (/ (. (Path sys.executable) parent) "hy")))


(defk run-task-in-child [tmp-path program versions]
  {:pre [(: tmp-path Path) (: program Program) (: versions dict)] :post [(: % (| TaskSucceeded TaskFailed))]
   :tags {:context "doeff-cluster-test" :role "entry"}}
  "job_entry task を worker と同じ形(--result と、置き場から取った Program の cache の file の --program — --blob・--versions・--env は
   無い)で起こし、結果の file を読む。versions = 詰めた送り手の版(file の中に Program と一緒に置く — service と同じ形)。"
  (val blob (encode-program program))
  (val program-path (write-program-file (/ tmp-path "programs") (program-sha blob) blob versions))
  (val result (/ tmp-path "task.result"))
  (val env (| (dict os.environ) {"PYTHONPATH" (str ROOT) "DOEFF_WORKER_NAME" "child" "DOEFF_WORKER_JOB" "t"}))
  (val done (subprocess.run [HY "-m" "doeff_cluster.job_entry" "task" "--result" (str result) "--program" (str program-path)]
                            :cwd (str ROOT) :env env :capture-output True :text True :timeout 120))
  (assert (= done.returncode 0) done.stderr)
  (decode-outcome (.read-text result)))


(deftest test-child-process-restores-the-program-and-runs-it-with-its-own-handlers [tmp-path]
  (<- outcome (run-task-in-child tmp-path (counter-program "card") (current-versions)))
  (assert (isinstance outcome TaskSucceeded) outcome)
  ;; base = 1 は Program が自分で並べた reader の答え(子は handler を足さない)。
  (assert (= outcome.value "card-1")))


(deftest test-child-process-adds-no-handler-to-the-task [tmp-path]
  ;; 反例: handler を並べない Program の Ask は子の中で答えが無く、task は失敗として返る(子が既定の handler を足していない)。
  (<- outcome (run-task-in-child tmp-path (bare-program 1) (current-versions)))
  (assert (isinstance outcome TaskFailed) outcome)
  (assert (in "Ask" (str outcome.message)) outcome.message))


(deftest test-child-process-returns-the-exception-itself [tmp-path]
  (<- outcome (run-task-in-child tmp-path (boom-program) (current-versions)))
  (assert (isinstance outcome TaskFailed))
  (assert (= outcome.kind "ValueError"))
  (assert (isinstance outcome.error ValueError))
  (assert (= (str outcome.error) "業務の失敗 base=100"))
  (assert (in "boom" outcome.traceback)))


(deftest test-child-process-refuses-a-blob-from-a-different-version-without-restoring-it [tmp-path]
  (<- outcome (run-task-in-child tmp-path (based-add 1) (| (current-versions) {"python" "3.9.6"})))
  (assert (isinstance outcome TaskFailed))
  (assert (= outcome.kind "VersionMismatch"))
  (assert (in "python: 送り手 3.9.6" outcome.message)))


(deftest test-child-process-writes-a-failure-when-the-program-file-is-missing [tmp-path]
  ;; worker が置き場から Program を取れていない(cache の file が無い)時も、task の入口は結果の file に失敗を書いて 0 で終わる
  ;; (結果を書かずに終わった = lost と取り違えない)。
  (val result (/ tmp-path "task.result"))
  (val env (| (dict os.environ) {"PYTHONPATH" (str ROOT) "DOEFF_WORKER_NAME" "child" "DOEFF_WORKER_JOB" "t"}))
  (val done (subprocess.run [HY "-m" "doeff_cluster.job_entry" "task" "--result" (str result)
                             "--program" (str (/ tmp-path "programs" "absent.json"))]
                            :cwd (str ROOT) :env env :capture-output True :text True :timeout 120))
  (assert (= done.returncode 0) done.stderr)
  (val outcome (decode-outcome (.read-text result)))
  (assert (isinstance outcome TaskFailed) outcome)
  (assert (= outcome.kind "RemoteJobFailed") outcome)
  (assert (in "Program の file" outcome.message) outcome.message))
