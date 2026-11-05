;;; Tests for cancelling a child-process request run by offloaded-subprocess-handler (os_process.hy) (the child-process-side
;;; user of the Handoff lock in offloaded_call.hy that #2792 L1276 made an RLock). Uses real children (/bin/sh, sleep, true).
;;;
;;;   (a) Cancel before the work starts: the work that spawns the child was handed to a thread, but the thread has not started it yet, and the
;;;       waiting task is cancelled. Handoff's give-up calls Future.cancel while holding the lock, and Future.cancel calls the completion
;;;       callback (deliver) on the same thread, so if the lock cannot be re-entered the scheduler thread stops right there. To reliably create
;;;       the before-start window, only this test replaces os_process's PROCESS-THREADS with GatedStart (an Executor that does not start work
;;;       until the gate opens). The cancelled work does not start even after the gate opens,
;;;       so the child is never spawned.
;;;   (b) Cancel while the child runs: the waiting task is cancelled after the child has written its own pid to a FIFO (so it is running).
;;;       The child is gone within the time limit (RunProcess timeout) plus the grace period -- both on the path that waits the same way
;;;       as subprocess.run (no process-group) and on the run-watched path
;;;       (process-group).
;;;   (c) Cancel while a child with no time limit runs: since there is no limit, the child would remain until it ends
;;;       naturally unless the cancel handling stops it (found in #2829 -- before, the answer was merely discarded). After the cancel, the child is
;;;       gone within RunProcess's stop-grace. On the process-group path, the grandchild the child started in the background is gone too (the whole group is stopped).
;;;   (d) When the (c) child ignores SIGTERM: it is stopped by SIGKILL after the stop-grace (the grandchild too, for a group).
;;;   (e) Counting stopped children: under metered-offloaded-subprocess-handler given a meter responder, cancelling (c) and (d) once each advances the meter's
;;;       counters process_cancel_terminated and process_cancel_killed by one each (rendering adds _total to the name). The counting happens outside the request's run (a new VM
;;;       on another thread), so the meter is a process-meter-handler (a per-test place name) that reads and writes the same place across runs.
;;;
;;; What is checked: the cancelled task ends with TaskCancelledError; the scheduler does not hang and moves on to the next task (a RunProcess of one child);
;;; no child is left over ((a) never starts; for (b)(c)(d) no pid is alive after the grace).
;;; The scenario runs in a new VM on a daemon thread separate from the test thread; if there is no answer in SCENARIO-LIMIT seconds, the test fails naming it -- even if the lock hangs,
;;; the test ends red with that thread left behind and does not stop the test suite (it does not wait for pytest's 60-second limit).
(require doeff-hy.macros [defk deff deftest <- val])
(require doeff-hy.record [defrecord])
(import contextlib)
(import dataclasses [dataclass])
(import os)
(import signal)
(import subprocess)
(import unittest [mock])
(import threading)
(import time)
(import collections.abc [Callable])
(import concurrent.futures [Future])
(import pytest)
(import doeff [Program with_handlers])
(import doeff_core_effects [state])
(import doeff_core_effects [MeterSettings MeterSnapshot ReadMeter])
(import doeff_core_effects [process-meter-handler])
(import doeff_core_effects._os_process :as process-module)
(import doeff_core_effects [offloaded-subprocess-handler metered-offloaded-subprocess-handler]
        doeff_core_effects._os_process [CANCEL-TERMINATED-METRIC CANCEL-KILLED-METRIC])
(import doeff_core_effects._offloaded_call [ThreadPerCall run-detached])
(import doeff_core_effects [ProcessAlive ProcessOutcome RunProcess])
(import doeff_core_effects [scheduled CreateExternalPromise ExternalPromise Task Wait Spawn Cancel TaskCancelledError])

;; The waits in this file are about real time (what they are about is in this file's row of the roster tests/realtime_tests.toml) -- the sleep rule for tests
;; (DOEFF211 of doeff-linter) is not applied to sleeps in a file that declares itself realtime.
(val pytestmark pytest.mark.realtime)

;; Upper limit (seconds) for one scenario. It normally ends within 1 second. Past it, the scheduler thread is considered hung and the test fails.
(val SCENARIO-LIMIT 5.0)
;; The child's time limit in (b) (RunProcess timeout, seconds), the grace (seconds) until the child is stopped and reaped after the limit, and the interval (seconds) for asking whether it is alive.
(val CHILD-LIMIT 0.5)
(val GONE-GRACE 1.0)
(val POLL 0.02)
;; A child that writes its own pid to $1 and then sleeps (exec, so the sleep has the same pid). The sleep is longer than the limit plus the grace -- it remains if nobody stops it.
(val PID-THEN-SLEEP "echo $$ > \"$1\"; exec sleep 5")
;; The child of the next task (the responder still runs children after the cancel) and its answer.
(val NEXT-ARGV #("true"))
(val NEXT-OUTCOME (ProcessOutcome :exit-code 0 :stdout "" :stderr ""))
;; RunProcess's stop-grace for the (c)(d) child (seconds -- the grace from sending SIGTERM on cancel to SIGKILL).
(val CANCEL-GRACE 0.5)
;; The (c)(d) child: run without a time limit; if nobody stops it, it remains for 30 seconds. On the process-group path it starts one grandchild in the background and writes the child's and grandchild's pids to
;; $1 (exec, so the sleep has the same pid as the child). (d) ignores SIGTERM at the start (the ignore is inherited by the exec and the grandchild).
(val LONE-UNBOUNDED "echo $$ > \"$1\"; exec sleep 30")
(val GROUP-UNBOUNDED "sleep 30 & echo $$ $! > \"$1\"; exec sleep 30")
(val IGNORE-TERM "trap '' TERM; ")


(defrecord BeforeStart
  "What scenario (a) saw: ended-by-cancel = whether the cancelled task ended with TaskCancelledError; held-cancelled = whether the Future of the work
   handed to the thread was cancelled; started = whether the work thread started the work after the gate opened; pid-written = whether the child woke up and wrote its pid;
   next-outcome = the answer of the next child run after the cancel."
  (#^ bool ended-by-cancel)
  (#^ bool held-cancelled)
  (#^ bool started)
  (#^ bool pid-written)
  (#^ ProcessOutcome next-outcome))


(defrecord WhileRunning
  "What scenario (b) saw: pid = the pid of the running child; ended-by-cancel = whether the cancelled task ended with TaskCancelledError;
   moved-on-seconds = seconds from the cancel to the next child's answer; next-outcome = the next child's answer; alive-at-deadline = whether the
   pid was still alive past the child's limit and grace."
  (#^ int pid)
  (#^ bool ended-by-cancel)
  (#^ float moved-on-seconds)
  (#^ ProcessOutcome next-outcome)
  (#^ bool alive-at-deadline))


(defrecord Unbounded
  "What scenarios (c)(d) saw: pids = the pids of the child (child and grandchild for a process-group); ended-by-cancel = whether the cancelled task ended with TaskCancelledError;
   moved-on-seconds = seconds from the cancel to the next child's answer; next-outcome = the next child's answer; survivors = the pids still alive past the grace (CANCEL-GRACE) and
   GONE-GRACE."
  (#^ (get tuple #(int ...)) pids)
  (#^ bool ended-by-cancel)
  (#^ float moved-on-seconds)
  (#^ ProcessOutcome next-outcome)
  (#^ (get tuple #(int ...)) survivors))


(defrecord CountedStops
  "What scenario (e) saw: terminated, killed = the meter's counters (number stopped by SIGTERM, number that needed SIGKILL); survivors = the pids still alive after the grace of
   the two cancels."
  (#^ float terminated)
  (#^ float killed)
  (#^ (get tuple #(int ...)) survivors))


(defclass GatedStart [ThreadPerCall]
  "Like ThreadPerCall in that each call starts one daemon thread, but an Executor that waits for the gate to open before the thread starts the work (turns the Future into running)
   (a test shell -- a resource, so not a value type). Passes the submitted Future to submitted, and whether the work started after the gate
   (the answer of set_running_or_notify_cancel -- False for a cancelled Future) to decided."

  (deff __init__ [self gate submitted decided]  ; cannot be a defk: initializing the resources of the test shell
    {:pre [(: self GatedStart) (: gate threading.Event) (: submitted (get ExternalPromise Future)) (: decided (get ExternalPromise bool))]
     :post [(: % None)]}
    "To hold the gate and two promises that receive notifications."
    (setv self.gate gate
          self.submitted submitted
          self.decided decided))

  (deff submit [self call #* args]  ; cannot be a defk: the Executor's interface (a concurrent.futures contract -- outside the VM)
    {:pre [(: self GatedStart) (: call Callable) (: args tuple)] :post [(: % Future)]}
    "To return a Future that runs call on a new thread after the gate opens, and to pass that Future to submitted."
    (setv future (Future))
    (.complete self.submitted future)
    (.start (threading.Thread :target self.start-after-gate :args #(future call args) :name "doeff-gated-call" :daemon True))
    future)

  (deff start-after-gate [self future call args]  ; cannot be a defk: the body of the work thread (outside the VM)
    {:pre [(: self GatedStart) (: future Future) (: call Callable) (: args tuple)] :post [(: % None)]}
    "To wait for the gate to open (up to SCENARIO-LIMIT seconds) and, if the Future was not cancelled, run call and put the answer in the Future."
    (.wait self.gate SCENARIO-LIMIT)
    (setv started (.set-running-or-notify-cancel future))
    (.complete self.decided started)
    (when started
      (try
        (.set-result future (call #* args))
        (except [error BaseException]
          (.set-exception future error))))))


(deff report-pid [fifo promise]  ; cannot be a defk: the body of a test-shell thread (waits outside the VM for the FIFO writer)
  {:pre [(: fifo str) (: promise (get ExternalPromise int))] :post [(: % None)]}
  "To read the pid the child wrote to the FIFO and pass it to the promise (the child waits to write until a reader opens, so the pid of the child that started is known without asking again)."
  (try
    (with [reader (open fifo :encoding "utf-8")]
      (.complete promise (int (.read reader))))
    (except [error Exception]
      (.fail promise error))))


(deff report-pids [fifo promise]  ; cannot be a defk: the body of a test-shell thread (waits outside the VM for the FIFO writer)
  {:pre [(: fifo str) (: promise (get ExternalPromise tuple))] :post [(: % None)]}
  "To read the whitespace-separated pids the child wrote to the FIFO (the child and, for a process-group, the grandchild) and pass them to the promise (the plural form of report-pid)."
  (try
    (with [reader (open fifo :encoding "utf-8")]
      (.complete promise (tuple (gfor word (.split (.read reader)) (int word)))))
    (except [error Exception]
      (.fail promise error))))


(defk ended-by-cancel [task]
  {:pre [(: task (get Task ProcessOutcome))] :post [(: % bool)]
   :tags {:context "process-test" :role "program"}}
  "To wait for the cancelled task and answer whether it ended by the cancel."
  (try
    (<- (Wait task))
    False
    (except [TaskCancelledError] True)))


(defk alive-at-deadline [pid deadline]
  {:pre [(: pid int) (: deadline float)] :post [(: % bool)]
   :tags {:context "process-test" :role "program"}}
  "To ask every POLL seconds until the child of pid is gone, and answer whether it was still alive past deadline (monotonic seconds)."
  (<- alive bool (ProcessAlive pid))
  (if (and alive (< (time.monotonic) deadline))
      (do (time.sleep POLL)
          (<- later bool (alive-at-deadline pid deadline))
          later)
      alive))


(defk survivors [pids deadline]
  {:pre [(: pids tuple) (: deadline float)] :post [(: % tuple)]
   :tags {:context "process-test" :role "program"}}
  "To answer which of pids were still alive past deadline (monotonic seconds) (asks alive-at-deadline for each -- the deadline is shared)."
  (var left #())
  (for [pid pids]
    (<- alive bool (alive-at-deadline pid deadline))
    (when alive
      (:= left (+ left #(pid)))))
  left)


(defk cancelled-before-the-call-starts [monkeypatch pid-file]
  {:pre [(: monkeypatch pytest.MonkeyPatch) (: pid-file str)] :post [(: % BeforeStart)]
   :tags {:context "process-test" :role "program"}}
  "Scenario (a) (see the header note): after handing the work that spawns the child to a thread, and before that thread starts the work, cancel the waiting task. After opening the gate,
   put back the real PROCESS-THREADS and run the next child."
  (<- submitted (CreateExternalPromise))
  (<- decided (CreateExternalPromise))
  (val gate (threading.Event))
  (.setattr monkeypatch process-module "PROCESS_THREADS" (GatedStart gate submitted decided))
  (<- task (Spawn (RunProcess :argv #("/bin/sh" "-c" PID-THEN-SLEEP "sh" pid-file))))
  (<- held Future (Wait submitted.future))
  ;; Future.cancel inside the lock-holding give-up calls deliver on the same thread (with a lock that cannot be re-entered, it hangs here).
  (<- (Cancel task))
  (<- ended bool (ended-by-cancel task))
  (.set gate)
  (<- started bool (Wait decided.future))
  (.undo monkeypatch)
  (<- next (Spawn (RunProcess :argv NEXT-ARGV)))
  (<- after ProcessOutcome (Wait next))
  (BeforeStart :ended-by-cancel ended :held-cancelled (.cancelled held) :started started
               :pid-written (os.path.exists pid-file) :next-outcome after))


(defk cancelled-while-the-child-runs [fifo grouped]
  {:pre [(: fifo str) (: grouped bool)] :post [(: % WhileRunning)]
   :tags {:context "process-test" :role "program"}}
  "Scenario (b) (see the header note): cancel the waiting task after the child wrote its pid (while it runs), run the next child, and see whether the child
   is gone within the limit and the grace. grouped = whether to run with a process-group (the run-watched path)."
  (<- seen-pid (CreateExternalPromise))
  (os.mkfifo fifo)
  (.start (threading.Thread :target report-pid :args #(fifo seen-pid) :name "doeff-pid-reader" :daemon True))
  (<- task (Spawn (RunProcess :argv #("/bin/sh" "-c" PID-THEN-SLEEP "sh" fifo) :timeout CHILD-LIMIT :process-group grouped)))
  (<- pid int (Wait seen-pid.future))
  (val cancelled-at (time.monotonic))
  (<- (Cancel task))
  (<- ended bool (ended-by-cancel task))
  (<- next (Spawn (RunProcess :argv NEXT-ARGV)))
  (<- after ProcessOutcome (Wait next))
  (val moved-on (- (time.monotonic) cancelled-at))
  (<- left bool (alive-at-deadline pid (+ cancelled-at CHILD-LIMIT GONE-GRACE)))
  (WhileRunning :pid pid :ended-by-cancel ended :moved-on-seconds moved-on :next-outcome after :alive-at-deadline left))


(defk cancelled-without-a-time-limit [fifo grouped ignores-term]
  {:pre [(: fifo str) (: grouped bool) (: ignores-term bool)] :post [(: % Unbounded)]
   :tags {:context "process-test" :role "program"}}
  "Scenarios (c)(d) (see the header note): cancel the waiting task after a child with no time limit wrote its pid (while it runs), run the next child, and see whether the child (and
   grandchild) are gone within the grace. grouped = whether to run with a process-group (the run-watched path); ignores-term = whether the child and grandchild
   ignore SIGTERM. The pids remaining after the grace are named in the answer and stopped with SIGKILL as test cleanup (so a red run does not leave a 30-second sleep)."
  (<- seen-pids (CreateExternalPromise))
  (os.mkfifo fifo)
  (.start (threading.Thread :target report-pids :args #(fifo seen-pids) :name "doeff-pid-reader" :daemon True))
  (val script (+ (if ignores-term IGNORE-TERM "") (if grouped GROUP-UNBOUNDED LONE-UNBOUNDED)))
  (<- task (Spawn (RunProcess :argv #("/bin/sh" "-c" script "sh" fifo) :process-group grouped :stop-grace CANCEL-GRACE)))
  (<- pids tuple (Wait seen-pids.future))
  (val cancelled-at (time.monotonic))
  (<- (Cancel task))
  (<- ended bool (ended-by-cancel task))
  (<- next (Spawn (RunProcess :argv NEXT-ARGV)))
  (<- after ProcessOutcome (Wait next))
  (val moved-on (- (time.monotonic) cancelled-at))
  (<- left tuple (survivors pids (+ cancelled-at CANCEL-GRACE GONE-GRACE)))
  (for [pid left]
    (with [(contextlib.suppress ProcessLookupError PermissionError)]
      (os.kill pid signal.SIGKILL)))
  (Unbounded :pids pids :ended-by-cancel ended :moved-on-seconds moved-on :next-outcome after :survivors left))


(defk meter-by [place deadline]
  {:pre [(: place str) (: deadline float)] :post [(: % MeterSnapshot)]
   :tags {:context "process-test" :role "program"}}
  "To read the two counters (number of children stopped by cancel) of the meter place every POLL seconds until both are at least 1, up to deadline, and answer the last
   snapshot read (the counting is done by the stopping thread, so it shows up a little after the child is gone)."
  (<- snapshot MeterSnapshot (with_handlers [(state) (process-meter-handler place (MeterSettings))] (ReadMeter)))
  (val counters snapshot.counters)
  (if (and (< (min (.get counters CANCEL-TERMINATED-METRIC 0.0) (.get counters CANCEL-KILLED-METRIC 0.0)) 1.0)
           (< (time.monotonic) deadline))
      (do (time.sleep POLL)
          (<- later MeterSnapshot (meter-by place deadline))
          later)
      snapshot))


(defk counted-cancels [term-fifo kill-fifo grouped place]
  {:pre [(: term-fifo str) (: kill-fifo str) (: grouped bool) (: place str)] :post [(: % CountedStops)]
   :tags {:context "process-test" :role "program"}}
  "Scenario (e) (see the header note): cancel one child that stops on SIGTERM and one that ignores SIGTERM (the (c)(d) scenario), and read the two counters of the meter place
   place."
  (<- by-term Unbounded (cancelled-without-a-time-limit term-fifo grouped False))
  (<- by-kill Unbounded (cancelled-without-a-time-limit kill-fifo grouped True))
  (<- snapshot MeterSnapshot (meter-by place (+ (time.monotonic) GONE-GRACE)))
  (CountedStops :terminated (.get snapshot.counters CANCEL-TERMINATED-METRIC 0.0)
                :killed (.get snapshot.counters CANCEL-KILLED-METRIC 0.0)
                :survivors (+ by-term.survivors by-kill.survivors)))


(defk settled-within [scenario what [handler offloaded-subprocess-handler]]
  {:pre [(: scenario Program) (: what str) (: handler Callable)] :post [(: % "the scenario's answer (the type depends on the scenario)")]
   :tags {:context "process-test" :role "program"}}
  "To run the scenario once, in a new VM on a daemon thread separate from the test thread, under handler (default offloaded-subprocess-handler) and scheduled,
   waiting for the answer up to SCENARIO-LIMIT seconds. Past it, fail naming what (the hung thread is left as a daemon -- see the header note)."
  (val running (.submit (ThreadPerCall) run-detached (scheduled (with_handlers [handler] scenario))))
  (try
    (.result running :timeout SCENARIO-LIMIT)
    (except [TimeoutError]
      (raise (AssertionError (.format "{} did not finish in {} seconds (the scheduler thread hung)" what SCENARIO-LIMIT))))))


(deftest test-a-run-cancelled-before-its-call-starts-moves-on-and-starts-no-child [monkeypatch tmp-path]
  ;; (a) (header note): with a lock that cannot be re-entered, the scheduler thread hangs inside the cancel and the test goes red at SCENARIO-LIMIT.
  (val pid-file (str (/ tmp-path "pid")))
  (<- seen BeforeStart (settled-within (cancelled-before-the-call-starts monkeypatch pid-file) "cancel before the work starts"))
  (assert seen.ended-by-cancel seen)
  (assert seen.held-cancelled seen)
  ;; The cancelled work does not start even after the gate opens, and the child is never spawned.
  (assert (not seen.started) seen)
  (assert (not seen.pid-written) seen)
  (assert (= seen.next-outcome NEXT-OUTCOME) seen))


(deftest test-a-run-cancelled-while-its-child-runs-moves-on-and-leaves-no-child [#^ bool grouped tmp-path]
  {:params {"grouped" [False True]}}
  ;; (b) (header note): the cancel moves on to the next task without waiting for the child to end, and the child is gone within the limit and the grace.
  (val fifo (str (/ tmp-path "pid.fifo")))
  (<- seen WhileRunning (settled-within (cancelled-while-the-child-runs fifo grouped) "cancel while the child runs"))
  (assert seen.ended-by-cancel seen)
  (assert (= seen.next-outcome NEXT-OUTCOME) seen)
  (assert (< seen.moved-on-seconds CHILD-LIMIT)
          (.format "{:.2f} seconds from the cancel to the next child's answer (waited for the child's limit of {} seconds)" seen.moved-on-seconds CHILD-LIMIT))
  (assert (not seen.alive-at-deadline)
          (.format "child {} remains after the limit of {} seconds and the grace of {} seconds" seen.pid CHILD-LIMIT GONE-GRACE)))


(deftest test-a-cancelled-child-without-a-time-limit-is-stopped-within-the-grace [#^ bool grouped tmp-path]
  {:params {"grouped" [False True]}}
  ;; (c) (header note): if the cancel handling does not stop the child, the child with no limit (and the grandchild) remains for 30 seconds and the test goes red.
  (val fifo (str (/ tmp-path "pid.fifo")))
  (<- seen Unbounded (settled-within (cancelled-without-a-time-limit fifo grouped False) "cancel of a child with no limit"))
  (assert seen.ended-by-cancel seen)
  (assert (= seen.next-outcome NEXT-OUTCOME) seen)
  (assert (< seen.moved-on-seconds CANCEL-GRACE)
          (.format "{:.2f} seconds from the cancel to the next child's answer (waited for the child to stop)" seen.moved-on-seconds))
  (assert (= seen.survivors #())
          (.format "of child and grandchild {}, {} remain after the cancel plus the grace of {} seconds and {} seconds" seen.pids seen.survivors CANCEL-GRACE GONE-GRACE)))


(deftest test-a-cancelled-child-that-ignores-term-is-killed-after-the-grace [#^ bool grouped tmp-path]
  {:params {"grouped" [False True]}}
  ;; (d) (header note): a child (and grandchild) that ignores SIGTERM remains and the test goes red unless SIGKILL is sent after the grace.
  (val fifo (str (/ tmp-path "pid.fifo")))
  (<- seen Unbounded (settled-within (cancelled-without-a-time-limit fifo grouped True) "cancel of a child that ignores SIGTERM"))
  (assert seen.ended-by-cancel seen)
  (assert (= seen.next-outcome NEXT-OUTCOME) seen)
  (assert (< seen.moved-on-seconds CANCEL-GRACE)
          (.format "{:.2f} seconds from the cancel to the next child's answer (waited for the child to stop)" seen.moved-on-seconds))
  (assert (= seen.survivors #())
          (.format "of child and grandchild {}, {} remain after the cancel plus the grace of {} seconds and {} seconds" seen.pids seen.survivors CANCEL-GRACE GONE-GRACE)))


(deftest test-children-stopped-by-cancel-are-counted-by-how-they-stopped [#^ bool grouped tmp-path caplog]
  {:params {"grouped" [False True]}}
  ;; (e) (header note): if stopped children are not counted, the counters stay at 0 (or absent) and the test goes red. The place name is separate per test (one per process).
  ;; The two stopped children each also appear as one line in the log, naming the head of argv (/bin/sh).
  (val place (.format "test-offloaded-process-cancel:{}" tmp-path))
  (<- seen CountedStops (settled-within (counted-cancels (str (/ tmp-path "term.fifo")) (str (/ tmp-path "kill.fifo")) grouped place)
                                        "counting of stopped children"
                                        (metered-offloaded-subprocess-handler (process-meter-handler place (MeterSettings)))))
  (assert (= seen.survivors #()) seen)
  (assert (= seen.terminated 1.0) (.format "the number stopped by SIGTERM is {} (should be 1)" seen.terminated))
  (assert (= seen.killed 1.0) (.format "the number that needed SIGKILL is {} (should be 1)" seen.killed))
  (val lines (lfor record caplog.records :if (= record.name process-module.__name__) (.getMessage record)))
  (assert (and (= (len lines) 2) (all (gfor line lines (in "'/bin/sh'" line)))) lines))


;; (f) A machine where the OS refuses to send signals (EPERM -- on helios on 2026-11-01 AppArmor refused signals between processes inside the container):
;; neither SIGTERM nor SIGKILL reaches the child that the cancel stops. The stop handling returns after the refused SIGKILL without waiting for the child to end (if it waited, the stopping
;; thread would not return until the child ended), does not count it as stopped, and names the failure to stop in one warning line. The child keeps running.
;; Counterexamples = the form that waits for the child after the refused SIGKILL (before the fix) goes red at SCENARIO-LIMIT; the form that sends to a non-group child with Popen's terminate
;; goes red with PermissionError.

(defrecord RefusedStop
  "What scenario (f) saw: alive = whether the child was still running after the stop handling; terminated, killed = the meter's counters (number stopped by cancel)."
  (#^ bool alive)
  (#^ float terminated)
  (#^ float killed))


(defk refused-cancel-stop [grouped place]
  {:pre [(: grouped bool) (: place str)] :post [(: % RefusedStop)]
   :tags {:context "process-test" :role "program"}}
  "Scenario (f): have the cancel handling stop a running child under a stand-in for a machine that refuses all signal sends (os.killpg and os.kill raise EPERM),
   and read the child's liveness and the counters of the meter place. The child is finally cleaned up with a real SIGKILL."
  (val child (subprocess.Popen ["sleep" "30"] :start-new-session grouped))
  (try
    (val watch (process-module.ChildWatch CANCEL-GRACE (process-meter-handler place (MeterSettings))))
    (with [_ (mock.patch.object os "killpg" :side-effect (PermissionError 1 "Operation not permitted"))
           _ (mock.patch.object os "kill" :side-effect (PermissionError 1 "Operation not permitted"))]
      (<- (process-module.stop-cancelled-child child grouped watch)))
    (val alive (is (.poll child) None))
    (<- snapshot MeterSnapshot (with_handlers [(state) (process-meter-handler place (MeterSettings))] (ReadMeter)))
    (RefusedStop :alive alive
                 :terminated (.get snapshot.counters CANCEL-TERMINATED-METRIC 0.0)
                 :killed (.get snapshot.counters CANCEL-KILLED-METRIC 0.0))
    (finally
      (.kill child)
      (.wait child))))


(deftest test-a-cancelled-child-whose-kill-the-os-refuses-is-not-waited-for-nor-counted [#^ bool grouped tmp-path caplog]
  {:params {"grouped" [False True]}}
  ;; (f) (note above).
  (val place (.format "test-offloaded-process-cancel-refused:{}" tmp-path))
  (<- seen RefusedStop (settled-within (refused-cancel-stop grouped place) "stop on cancel on a machine that refuses signals"))
  (assert seen.alive seen)
  (assert (= #(seen.terminated seen.killed) #(0.0 0.0)) seen)
  (val lines (lfor record caplog.records :if (= record.name process-module.__name__) (.getMessage record)))
  (assert (and (= (len lines) 1) (in "could not stop" (get lines 0)) (in "'sleep'" (get lines 0))) lines))
