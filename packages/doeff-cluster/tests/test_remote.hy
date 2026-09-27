;; RemoteJob: 同じ VM の handler(A)と、worker の子 process の入口(job_entry task)を通す往復。
;; task も Program の値 1 つで、handler は Program が本体の with-handlers で自分で並べる(ADR-DOE-CLUSTER-001 R1・R2)。実行先は handler を
;; 足さない。handler の値は詰めない(R3b — encode-program が UnsendableProgram で断る)。
(require doeff-hy.macros [deftest defk <- val var])
(import json)
(import os)
(import pathlib [Path])
(import subprocess)
(import sys)
(import threading)
(import pytest)
(import doeff [run DoExpr with-handlers])
(import doeff_core_effects.handlers [reader])
(import doeff_cluster.remote_model [RemoteJob UnsendableProgram VersionMismatch RemoteJobFailed
                                          TaskSucceeded TaskFailed encode-program decode-program decode-outcome
                                          current-versions version-mismatch])
(import doeff_cluster.remote [remote-inline])
(import tests.fixtures.services [self-contained-program holding-program bare-program])
(import tests.fixtures.entry_programs [answer-base based-add counter-program boom-program])

(val NET (frozenset ["net"]))


(deftest test-inline-handler-runs-the-program-with-its-own-handlers
  ;; 外側に reader を置かない: Program が自分で並べた handler だけで答える。
  (<- a int (with-handlers [(remote-inline)] (RemoteJob (based-add 3) :needs NET)))
  (assert (= a 103))
  (<- b str (with-handlers [(remote-inline)] (RemoteJob (counter-program "card") :needs NET)))
  (assert (= b "card-1")))


(deftest test-inline-handler-returns-the-program-exception-to-the-caller
  (var caught None)
  (try
    (<- (with-handlers [(remote-inline)] (RemoteJob (boom-program) :needs NET)))
    (except [error ValueError] (:= caught error)))
  (assert (= (str caught) "業務の失敗 base=100")))


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
  ;; RemoteJob の送り手(remote-inline は詰めないので、送り手の 1 か所 encode-program を直に)も同じ。
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
  (assert (in "doeff: 送り手 0.0.0" message))
  (assert (not-in "python" message)))


;; --- 子 process の入口(worker が起動する形そのもの)を通す ----------------------------------------

(val ROOT (. (Path (os.path.abspath __file__)) parent parent))   ; この package の根(検の module は tests.* の名)
(val HY (str (/ (. (Path sys.executable) parent) "hy")))


(defk run-task-in-child [tmp-path program versions]
  {:pre [(: tmp-path Path) (: program DoExpr) (: versions dict)] :post [(: % (| TaskSucceeded TaskFailed))]
   :tags {:context "doeff-cluster-test" :role "entry"}}
  "job_entry task を worker と同じ形(--blob --result --versions — --env は無い)で起こし、結果の file を読む。"
  (val blob (/ tmp-path "task.blob"))
  (val result (/ tmp-path "task.result"))
  (.write-text blob (encode-program program))
  (val env (| (dict os.environ) {"PYTHONPATH" (str ROOT) "DOEFF_WORKER_NAME" "child" "DOEFF_WORKER_JOB" "t"}))
  (val done (subprocess.run [HY "-m" "doeff_cluster.job_entry" "task" "--blob" (str blob) "--result" (str result)
                             "--versions" (json.dumps versions)]
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
