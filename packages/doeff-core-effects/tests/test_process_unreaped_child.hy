;;; A RunProcess timeout whose child SIGKILL does not end (card ki-0596f60ccd12): a child in uninterruptible sleep (D state) on a mount that does not answer stays until the mount
;;; comes back, even after SIGKILL - a 20-second git rev-parse answered its timeout after 29 minutes, because the wait after SIGKILL had no limit. subprocess-handler (os_process.hy)
;;; now waits for the end after SIGKILL only stop-grace seconds, hands the child to a thread that reaps it whenever it ends, and gives the timeout answer.
;;;
;;; The stand-in for the D-state child is StuckPopen: a real child (sleep) whose end this process does not see until the test opens the gate (its wait with a limit runs out, and its wait
;;; without a limit and its poll do not see the end). The signals really reach the child, so only the reaping is held - the part the D state holds.
;;; Each path of the timeout is checked: the wait of subprocess.run (run-communicated), stream-output without a group, and process-group (both run-watched):
;;;   - the timeout answer comes back within the limit plus a few stop-graces (it hung until the gate opened before);
;;;   - the child is not reaped when the answer comes back, the warning names it, and once the gate opens the thread it was handed to reaps it.
;;; The scenario runs in a new VM on a daemon thread; if there is no answer in SCENARIO-LIMIT seconds the test fails, and the gate is opened at the end in any case (the hung thread ends).
(require doeff-hy.macros [defk deff deftest <- val var])
(import logging)
(import subprocess)
(import threading)
(import time)
(import pytest)
(import doeff [Program with_handlers])
(import doeff_core_effects [subprocess-handler])
(import doeff_core_effects._offloaded_call [ThreadPerCall run-detached])
(import doeff_core_effects [ProcessOutcome RunProcess timed-out-outcome])

;; The waits in this file are about real time (what they are about is in this file's row of the roster tests/realtime_tests.toml) -- the sleep rule for tests
;; (DOEFF211 of doeff-linter) is not applied to sleeps in a file that declares itself realtime.
(val pytestmark pytest.mark.realtime)

;; The child's time limit (RunProcess timeout, seconds) and stop-grace (seconds). The answer is expected within the limit plus a few graces (ANSWER-LIMIT).
(val CHILD-LIMIT 0.3)
(val GRACE 0.3)
(val ANSWER-LIMIT 3.0)
;; Upper limit (seconds) for one scenario (past it, the wait after SIGKILL is taken as unbounded), for the reaping once the gate opens, and the interval (seconds) for asking.
(val SCENARIO-LIMIT 8.0)
(val REAP-LIMIT 2.0)
(val POLL 0.02)
;; The child: a sleep longer than every limit here (it holds its output open until it is killed).
(val LONG-SLEEP #("sleep" "30"))


(defclass StuckPopen [subprocess.Popen]
  "A real child whose end this process does not see until gate opens (the stand-in for a D-state child - header note). gate and child are set per test (child = the child started last).
   A test shell - a resource, so not a value type."
  (setv gate None
        child None)

  (deff __init__ [self #* args #** kwargs]  ; cannot be a defk: initializing a Popen (the subprocess contract - outside the VM)
    {:pre [(: self StuckPopen) (: args tuple) (: kwargs dict)] :post [(: % None)]}
    "To start the real child and note it in child."
    (.__init__ (super) #* args #** kwargs)
    (setv StuckPopen.child self))

  (deff _wait [self timeout]  ; cannot be a defk: the wait of a Popen (the subprocess contract - outside the VM)
    {:pre [(: self StuckPopen) (: timeout (| int float None))] :post [(: % int)]}
    "To not see the end until the gate opens: a wait with a limit runs out (TimeoutExpired), a wait without one waits for the gate, then reaps for real."
    (when (not (.wait StuckPopen.gate timeout))
      (raise (subprocess.TimeoutExpired self.args timeout)))
    (._wait (super) :timeout None))

  (deff _internal_poll [self #* args #** kwargs]  ; cannot be a defk: the poll of a Popen (the subprocess contract - outside the VM)
    {:pre [(: self StuckPopen) (: args tuple) (: kwargs dict)] :post [(: % (| int None))]}
    "To answer still running until the gate opens."
    (if (.is-set StuckPopen.gate)
        (._internal_poll (super) #* args #** kwargs)
        None)))


(defk timed-out-run [process-group stream-output output-path]
  {:pre [(: process-group bool) (: stream-output bool) (: output-path (| str None))] :post [(: % tuple)]
   :tags {:context "process-test" :role "program"}}
  "To run the long child once with the short limit and answer #(answer seconds)."
  (val started (time.monotonic))
  (<- outcome ProcessOutcome (RunProcess :argv LONG-SLEEP :timeout CHILD-LIMIT :stop-grace GRACE :process-group process-group
                                         :stream-output stream-output :output-path output-path))
  #(outcome (- (time.monotonic) started)))


(deff reaped-by [child deadline]  ; cannot be a defk: a wait of the test thread for another thread (outside the VM)
  {:pre [(: child subprocess.Popen) (: deadline float)] :post [(: % bool)]}
  "To ask every POLL seconds until deadline (monotonic seconds) whether child was reaped (its returncode set by the thread that reaps it)."
  (while (and (is child.returncode None) (< (time.monotonic) deadline))
    (time.sleep POLL))
  (is-not child.returncode None))


(deftest test-a-timeout-answers-without-waiting-for-a-child-sigkill-does-not-end [#^ str path monkeypatch tmp-path caplog]
  {:params {"path" ["communicated" "streamed" "grouped"]}}
  (val gate (threading.Event))
  (.setattr monkeypatch StuckPopen "gate" gate)
  (.setattr monkeypatch StuckPopen "child" None)
  (.setattr monkeypatch subprocess "Popen" StuckPopen)
  (.set-level caplog logging.WARNING :logger "doeff_core_effects._os_process")
  (val scenario (timed-out-run (= path "grouped") (= path "streamed") (if (= path "streamed") (str (/ tmp-path "out.log")) None)))
  (val running (.submit (ThreadPerCall) run-detached (with_handlers [subprocess-handler] scenario)))
  (try
    (val answered (try
                    (.result running :timeout SCENARIO-LIMIT)
                    (except [TimeoutError]
                      (raise (AssertionError (.format "the {} timeout did not answer in {} seconds (it waits for the end of a child SIGKILL does not end)"
                                                      path SCENARIO-LIMIT))))))
    (val outcome (get answered 0))
    (val seconds (get answered 1))
    (<- expected ProcessOutcome (timed-out-outcome "" ""))
    (assert (= outcome expected) outcome)
    (assert (< seconds ANSWER-LIMIT) (.format "the timeout answered after {:.2f} seconds (limit {} + grace {})" seconds CHILD-LIMIT GRACE))
    (val child StuckPopen.child)
    (assert (is child.returncode None) "the child was reaped before the answer (the stand-in did not hold the reaping)")
    (assert (any (gfor record caplog.records (and (in "after SIGKILL" (.getMessage record)) (in (str child.pid) (.getMessage record)))))
            (lfor record caplog.records (.getMessage record)))
    (.set gate)
    (assert (reaped-by child (+ (time.monotonic) REAP-LIMIT)) (.format "child {} was not reaped {} seconds after the gate opened" child.pid REAP-LIMIT))
    (finally
      (.set gate))))
