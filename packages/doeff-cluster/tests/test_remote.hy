;; RemoteJob: 同じ VM の handler(A)と、worker の子 process の入口(job_entry task)を通す往復。
(require doeff-hy.macros [deftest defk <-])
(import json)
(import os)
(import pathlib [Path])
(import subprocess)
(import sys)
(import threading)
(import pytest)
(import doeff [with_handlers])
(import doeff_core_effects.effects [Ask])
(import doeff_core_effects.handlers [reader])
(import doeff_cluster.remote_model [RemoteJob UnsendableProgram VersionMismatch RemoteJobFailed
                                          TaskSucceeded TaskFailed encode-program decode-outcome
                                          current-versions version-mismatch])
(import doeff_cluster.remote [remote-inline])


(defk add-base [n]
  {:pre [(: n int)] :post [(: % int)]}
  (<- base int (Ask "base"))
  (+ base n))


(defk boom []
  {:pre [] :post [(: % int)]}
  (<- base int (Ask "base"))
  (raise (ValueError (.format "業務の失敗 base={}" base))))


(defn make-counter [prefix]
  ;; closure が値を捕まえた Program
  (defk counter []
    {:pre [] :post [(: % str)]}
    (<- base int (Ask "base"))
    (.format "{}-{}" prefix base))
  counter)


(deftest test-inline-handler-runs-the-program-under-the-outer-handlers
  (<- a int (with-handlers [(reader {"base" 1}) (remote-inline)] (RemoteJob (add-base 3) :env "x")))
  (assert (= a 4))
  (<- b str (with-handlers [(reader {"base" 1}) (remote-inline)] (RemoteJob ((make-counter "card")) :env "x")))
  (assert (= b "card-1")))


(deftest test-inline-handler-returns-the-program-exception-to-the-caller
  (setv caught None)
  (try
    (<- (with-handlers [(reader {"base" 1}) (remote-inline)] (RemoteJob (boom) :env "x")))
    (except [error ValueError] (setv caught error)))
  (assert (= (str caught) "業務の失敗 base=1")))


(defn test-program-holding-a-lock-is-refused-before-sending []
  (setv lock (threading.Lock))
  (defk holds-lock []
    {:pre [] :post [(: % int)]}
    (<- base int (Ask "base"))
    (with [lock] base))
  (with [(pytest.raises UnsendableProgram)]
    (encode-program (holds-lock))))


(defn test-version-mismatch-names-the-differing-key []
  (setv mine (current-versions))
  (assert (is (version-mismatch mine mine) None))
  (setv other (| mine {"doeff" "0.0.0"}))
  (setv message (version-mismatch other mine))
  (assert (in "doeff: 送り手 0.0.0" message))
  (assert (not-in "python" message)))


;; --- 子 process の入口(worker が起動する形そのもの)を通す ----------------------------------------

(setv ROOT (. (Path (os.path.abspath __file__)) parent parent))   ; この package の根(検の module は tests.* の名)
(setv HY (str (/ (. (Path sys.executable) parent) "hy")))
(setv ENV "tests.fixtures.envs:plain_env")


(defn run-task-in-child [tmp-path program versions]
  (setv blob (/ tmp-path "task.blob") result (/ tmp-path "task.result"))
  (.write-text blob (encode-program program))
  (setv env (| (dict os.environ) {"PYTHONPATH" (str ROOT)
                                   "DOEFF_WORKER_NAME" "child" "DOEFF_WORKER_JOB" "t"}))
  (setv done (subprocess.run [HY "-m" "doeff_cluster.job_entry" "task" "--blob" (str blob) "--result" (str result)
                              "--env" ENV "--versions" (json.dumps versions)]
                             :cwd (str ROOT) :env env :capture-output True :text True :timeout 120))
  (assert (= done.returncode 0) done.stderr)
  (decode-outcome (.read-text result)))


(defn test-child-process-restores-the-program-and-runs-it-under-its_own-handlers [tmp-path]
  (setv outcome (run-task-in-child tmp-path ((make-counter "card")) (current-versions)))
  (assert (isinstance outcome TaskSucceeded))
  ;; 受け側の env(base=100)で走った = handler は送られず、名前で組まれた。
  (assert (= outcome.value "card-100")))


(defn test-child-process-returns-the-exception-itself [tmp-path]
  (setv outcome (run-task-in-child tmp-path (boom) (current-versions)))
  (assert (isinstance outcome TaskFailed))
  (assert (= outcome.kind "ValueError"))
  (assert (isinstance outcome.error ValueError))
  (assert (= (str outcome.error) "業務の失敗 base=100"))
  (assert (in "boom" outcome.traceback)))


(defn test-child-process-refuses-a-blob-from-a-different-version-without-restoring-it [tmp-path]
  (setv outcome (run-task-in-child tmp-path (add-base 1) (| (current-versions) {"python" "3.9.6"})))
  (assert (isinstance outcome TaskFailed))
  (assert (= outcome.kind "VersionMismatch"))
  (assert (in "python: 送り手 3.9.6" outcome.message)))


(defn test-program-holding-a-file-is-refused-before-sending [tmp-path]
  ;; cloudpickle の既定は読みの file を中身の写し(StringIO)に黙って替える(実測 2026-09-23)。送り手で断る。
  (setv f (open (/ tmp-path "x.txt") "w+"))
  (defk holds-file []
    {:pre [] :post [(: % str)]}
    (<- base int (Ask "base"))
    (str f))
  (with [(pytest.raises UnsendableProgram :match "file を捕まえている")]
    (encode-program (holds-file))))
