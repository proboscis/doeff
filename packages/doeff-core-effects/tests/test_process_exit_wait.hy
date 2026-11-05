;;; Effects that wait for a child process to end (AwaitProcessExit, AwaitWarmChildExit), and the answerer process-exit-handler (process_exit.hy).
;;; Uses real processes (sleep, true, sh). How the fd that becomes readable on exit is opened depends on the machine (Linux = pidfd,
;;; macOS = kqueue EVFILT_PROC), and the same tests measure the machine's own way on either machine.
;;;
;;;   - Answers when a spawned child ends, and not before. The wait does not reap the child (the exit code is answered by a later PollProcess).
;;;   - A pid that was not spawned is ProcessNotChild. A child that already ended (not reaped) is answered on the spot.
;;;   - A non-child process (in the same position as a child detached from a waiting child - sh starts it in the background and itself exits) is waited for by the pair of pid and start-ticks.
;;;     If start-ticks differs (the pid was reused), it answers on the spot (it does not wait for the end of a different process that exists).
;;;   - A cancelled wait leaves no fd and emits no warning about a never-awaited coroutine.
;;;   - The function that obtains the fd that becomes readable on exit (the machine's way of opening it) returns None for a pid that does not exist.
;;;   - A wait for a running child's end is not counted as a scheduler stall. A wait for a child that ended but whose notice never arrives is counted as a stall
;;;     (rule S1 - packages/doeff-core-effects/architecture.hy).
(require doeff-hy.macros [defk deff deftest <- val])
(import gc)
(import logging)
(import os)
(import platform)
(import select)
(import sys)
(import subprocess)
(import threading)
(import time)
(import warnings)
(import asyncio)
(import collections.abc [Callable])
(import doeff [run with-handlers])
(import doeff.program [Pure])
(import doeff_core_effects [Await])
(import doeff_core_effects [await-handler])
(import doeff_core_effects [scheduled Spawn Race Cancel Wait TaskCancelledError])
(import doeff_core_effects._scheduler :as scheduler-module)
(import doeff_core_effects [subprocess-handler])
(import doeff_core_effects [os-warm-process-handler proc-stat-of])
(import doeff_core_effects [StartProcess PollProcess AwaitProcessExit ProcessStarted ProcessExited ProcessEnded ProcessNotChild])
(import doeff_core_effects [AwaitWarmChildExit])
(import doeff_core_effects [process-exit-handler]
        doeff_core_effects._process_exit [exit-fd-of])
(import doeff_core_effects._process_exit :as process-exit-module)
(import doeff_core_effects._pidfd_exit [pidfd-of])
(import doeff_core_effects._os_process [STARTED-CHILDREN])
(import pytest)

;; The waits in this file are about real time (what they cover is in this file's row of the roster tests/realtime_tests.toml) - the test-sleep rule
;; (DOEFF211 of doeff-linter) is not applied to sleeps inside a file that declares itself realtime.
(val pytestmark pytest.mark.realtime)

;; Upper bound (seconds) on the delay from the end to the answer, and the margin (seconds) of the lower bound for checking that it does not answer before the end.
(val PROMPT 0.3)
;; The period (seconds) of the scheduler's stall watchdog, shortened for the stall tests (production is 30 seconds).
(val STALL-INTERVAL 0.05)


(defn #^ object handled [#^ object program #^ (| str None) [implementation None]]
  "To run program on top of the set of real answerers (outermost first). implementation = the scheduler implementation (None = default)."
  (run (scheduled (with-handlers [await-handler subprocess-handler os-warm-process-handler process-exit-handler] program)
                  :implementation implementation)))


(defk started-then-awaited [argv]
  {:pre [(: argv tuple)] :post [(: % tuple)]}
  "Spawn a child, wait for its end, and after waiting ask with PollProcess: #(answer of the wait, answer of Poll)."
  (<- started ProcessStarted (StartProcess :argv argv))
  (<- ended (| ProcessEnded ProcessNotChild) (AwaitProcessExit started.pid))
  (<- polled (PollProcess started.pid))
  #(ended polled))


(defk awaited [wait]
  {:pre [(: wait (| AwaitProcessExit AwaitWarmChildExit))] :post [(: % (| ProcessEnded ProcessNotChild))]}
  "Issue one wait effect and return its answer."
  (<- answer (| ProcessEnded ProcessNotChild) wait)
  answer)


(defn #^ tuple timed [#^ object program #^ (| str None) [implementation None]]
  "To run program and return #(answer seconds)."
  (setv started (time.monotonic))
  (setv answer (handled program implementation))
  #(answer (- (time.monotonic) started)))


;; When a wait for a child's end lasts longer than this many seconds, start leaving evidence (the child's state, waitid, pidfd, the scheduler's wait queue).
(val EVIDENCE-AFTER 10.0)
;; Interval (seconds) at which evidence is taken.
(val EVIDENCE-EVERY 5.0)


(defn #^ str child-evidence [#^ int pid]
  "To make the current state of child pid into one line (only reads that do not disturb the wait - no reaping, and the fd is opened and closed): state of /proc/<pid>/stat,
   waitid (WNOWAIT, no reaping), poll of pidfd, and the Popen returncode."
  (try (setv stat (. (open (.format "/proc/{}/stat" pid)) (read)))
       (setv state (get (.split (get (.rsplit stat ")" 1) 1)) 0))
       (except [error OSError] (setv state (.format "gone({!r})" error))))
  (try (setv waited (os.waitid os.P_PID pid (| os.WEXITED os.WNOHANG os.WNOWAIT)))
       (except [error OSError] (setv waited (.format "error({!r})" error))))
  (try (setv fd (run (pidfd-of pid)))
       (if (is fd None)
           (setv polled "no-pidfd")
           (try (setv poller (select.poll))
                (.register poller fd select.POLLIN)
                (setv polled (.poll poller 0))
                (finally (os.close fd))))
       (except [error Exception] (setv polled (.format "error({!r})" error))))
  (setv found (.find STARTED-CHILDREN pid))
  (.format "pid={} stat-state={} waitid={} pidfd-poll={} popen-returncode={}" pid state waited polled
           (if (is found None) "not-in-table" (. (get found 0) returncode))))


(defclass ChildWaitEvidence []
  "To leave the child's state and the scheduler's wait queue in lines when a wait for a running child's end exceeds after seconds (
   so that when the daily run stalls for about 60 seconds and fails, the cause can be determined from a single failure record). It changes neither the wait limit nor the strictness of the test."
  (defn __init__ [self [after EVIDENCE-AFTER] [every EVIDENCE-EVERY]]
    (setv self.after after self.every every self.lines [] self.pid None self.began (time.monotonic)
          self.finished (threading.Event) self.lock (threading.Lock)))

  (defn note [self text]
    (setv line (.format "[+{:.1f}s] {}" (- (time.monotonic) self.began) text))
    (with [self.lock] (.append self.lines line))
    (.write sys.stderr (+ "child-wait-evidence " line "\n"))
    (.flush sys.stderr))

  (defn watch [self]
    "Another thread: if it has not ended after after seconds, leave the child's state every every seconds (stops when it ends)."
    (when (not (.wait self.finished :timeout self.after))
      (while (not (.is-set self.finished))
        (.note self (if (is self.pid None) "pid not started yet" (child-evidence self.pid)))
        (.wait self.finished :timeout self.every))))

  (defn wrap-source-alive [self original]
    "Wrap the read \"is the child not yet ended\" (child-running in process_exit) that the stall judgment calls, and for each read after after seconds,
     leave the time the scheduler judged that wait and the read's answer (= the wait queue the scheduler's stall watchdog is looking at now. Both the Python and Rust
     schedulers go through this read)."
    (defn probe [pid]
      (setv alive (run (original pid)))
      (when (> (- (time.monotonic) self.began) self.after)
        (.note self (.format "scheduler probes pending watched wait: pid={} source_alive={}" pid alive)))
      (Pure alive))
    probe)

  (defn text [self]
    (.join "\n" self.lines)))


(defk started-then-awaited-watched [argv evidence]
  {:pre [(: argv tuple) (: evidence ChildWaitEvidence)] :post [(: % tuple)]}
  "The same wait as started-then-awaited, and tells evidence the pid of the spawned child."
  (<- started ProcessStarted (StartProcess :argv argv))
  (setv evidence.pid started.pid)
  (<- ended (| ProcessEnded ProcessNotChild) (AwaitProcessExit started.pid))
  (<- polled (PollProcess started.pid))
  #(ended polled))


(defn #^ ChildWaitEvidence run-with-evidence [#^ object monkeypatch #^ tuple argv #^ str implementation #^ float after #^ float every]
  "Run the wait for the end of an argv child, and return a ChildWaitEvidence that left evidence for the period the wait exceeded after seconds (the answers of the wait are in .answers)."
  (setv evidence (ChildWaitEvidence after every))
  (.setattr monkeypatch process-exit-module "child_running" (.wrap-source-alive evidence process-exit-module.child_running))
  (setv watcher (threading.Thread :target evidence.watch :daemon True))
  (.start watcher)
  (try (setv evidence.answers (get (timed (started-then-awaited-watched argv evidence) implementation) 0))
       (finally (.set evidence.finished)
                (.join watcher)))
  evidence)


(defn #^ int stray-pid [#^ str seconds]
  "To start one non-child process: sh starts sleep in the background and itself exits (the parent of sleep is init or a subreaper - not a child of this process)."
  (int (.strip (. (subprocess.run ["sh" "-c" (.format "sleep {} >/dev/null 2>&1 & echo $!" seconds)] :capture-output True :text True :check True)
                  stdout))))


(defn #^ int start-ticks-of [#^ int pid]
  "To read the start-ticks of the process pid (Linux = the 22nd field of /proc/<pid>/stat, macOS = the start time from libproc)."
  (. (run (proc-stat-of pid)) start-ticks))


(deftest test-the-wait-ends-when-the-started-child-ends
  ;; A 0.5-second child: does not answer before it ends, and answers within PROMPT seconds after it ends. The wait does not reap, so a later PollProcess answers exit code 0.
  (setv #(answers seconds) (timed (started-then-awaited #("sleep" "0.5"))))
  (setv #(ended polled) answers)
  (assert (isinstance ended ProcessEnded) ended)
  (assert (<= 0.45 seconds (+ 0.5 PROMPT)) seconds)
  (assert (= polled (ProcessExited :pid ended.pid :exit-code 0)) polled))


(deftest test-a-pid-the-handler-did-not-start-is-not-a-child
  ;; A pid that was not spawned (this process itself) is ProcessNotChild - not waited for.
  (setv #(answer seconds) (timed (awaited (AwaitProcessExit (os.getpid)))))
  (assert (= answer (ProcessNotChild :pid (os.getpid))) answer)
  (assert (< seconds PROMPT) seconds))


(deftest test-a-child-that-already-ended-answers-at-once
  ;; A child that ended but has not been reaped is answered on the spot.
  (setv started (handled (StartProcess :argv #("true"))))
  (time.sleep 0.3)
  (setv #(answer seconds) (timed (awaited (AwaitProcessExit started.pid))))
  (assert (= answer (ProcessEnded :pid started.pid)) answer)
  (assert (< seconds PROMPT) seconds)
  (handled (PollProcess started.pid)))


(deftest test-the-wait-ends-when-a-process-that-is-not-a-child-ends
  ;; A non-child process also has its end waited for, by the pair of pid and start-ticks.
  (setv pid (stray-pid "0.6"))
  (setv ticks (start-ticks-of pid))
  (setv #(answer seconds) (timed (awaited (AwaitWarmChildExit :pid pid :start-ticks ticks))))
  (assert (= answer (ProcessEnded :pid pid)) answer)
  (assert (<= 0.3 seconds (+ 0.6 PROMPT)) seconds))


(deftest test-a-reused-pid-is-not-waited-on
  ;; If start-ticks differs (the pid was reused by another process), it answers on the spot without waiting for the end of the process that exists.
  (setv pid (stray-pid "5"))
  (try
    (setv #(answer seconds) (timed (awaited (AwaitWarmChildExit :pid pid :start-ticks (+ (start-ticks-of pid) 1)))))
    (assert (= answer (ProcessEnded :pid pid)) answer)
    (assert (< seconds PROMPT) seconds)
    (finally (os.kill pid 9))))


(defk raced-away [pid]
  {:pre [(: pid int)] :post [(: % None)]}
  "Race the wait for pid's end against a 0.2-second wait so that it loses, cancel the losing wait, and wait until the cancellation completes."
  (<- waiting (Spawn (awaited (AwaitProcessExit pid))))
  (<- short (Spawn (Await (asyncio.sleep 0.2))))
  (<- (Race waiting short))
  (<- (Cancel waiting))
  (try (<- (Wait waiting))
       (except [TaskCancelledError] None))
  None)


(defn #^ int open-descriptors []
  "To count the number of open fds of this process (/dev/fd exists on both Linux and macOS)."
  (len (os.listdir "/dev/fd")))


(deftest test-a-cancelled-wait-leaves-no-descriptor-and-no-warning
  ;; A wait that lost the race and was cancelled closes the end fd and leaves no never-awaited coroutine.
  (setv started (handled (StartProcess :argv #("sleep" "5"))))
  (setv before (open-descriptors))
  (with [_ (warnings.catch-warnings)]
    (warnings.simplefilter "error" RuntimeWarning)
    (handled (raced-away started.pid))
    (time.sleep 0.2)
    (gc.collect))
  (setv after (open-descriptors))
  (os.kill started.pid 9)
  (handled (PollProcess started.pid))
  (assert (= after before) #(before after)))


(deftest test-the-exit-fd-of-a-pid-that-is-gone-is-none
  ;; None for a pid that does not exist (a child that has been fully reaped).
  (setv child (subprocess.Popen ["true"]))
  (.wait child)
  (assert (is (run (exit-fd-of child.pid)) None)))


(defclass OnStallReport [logging.Handler]
  "Calls on-report when the scheduler writes a stall warning (from any thread)."
  (deff __init__ [self on-report]  ; cannot be a defk: constructor of a logging.Handler (outside the VM)
    {:pre [(: self OnStallReport) (: on-report Callable)] :post [(: % None)]}
    "To receive the call to make when a stall warning is seen."
    (.__init__ (super) :level logging.WARNING)
    (setv self.on-report on-report))

  (deff emit [self record]  ; cannot be a defk: callback that logging calls per record
    {:pre [(: self OnStallReport) (: record logging.LogRecord)] :post [(: % None)]}
    "To call on-report if the record is the scheduler's stall warning."
    (when (in "scheduler stalled" (.getMessage record))
      (self.on-report))))


(deftest test-waiting-on-a-running-child-is-not-a-stall [implementation monkeypatch caplog]
  {:params {"implementation" ["python" "rust"]}}
  ;; Rule S1: for a wait for a running child's end, the source of the end notice (the fd that becomes readable on end) is registered, and it is not a stall while the child is alive.
  ;; The child's 0.5 seconds is 10 times the shortened stall-watchdog period. Before the fix, a warning was written every period (every 30 seconds in the courier job).
  ;; When a wait exceeds EVIDENCE-AFTER seconds, leave the child's state and the scheduler's wait queue (evidence shows up in the failure record).
  (.setattr monkeypatch scheduler-module "EXTERNAL_STALL_LOG_INTERVAL_SECONDS" STALL-INTERVAL)
  (.set-level caplog logging.WARNING :logger scheduler-module.__name__)
  (val evidence (run-with-evidence monkeypatch #("sleep" "0.5") implementation EVIDENCE-AFTER EVIDENCE-EVERY))
  (val answers evidence.answers)
  (val reports (lfor record caplog.records :if (in "scheduler stalled" (.getMessage record)) (.getMessage record)))
  (assert (isinstance (get answers 0) ProcessEnded) #(answers (.text evidence)))
  (assert (= reports []) #(reports (.text evidence))))


(deftest test-a-long-wait-on-a-child-leaves-evidence [implementation monkeypatch]
  {:params {"implementation" ["python" "rust"]}}
  ;; Test of the evidence-leaving mechanism: set a 1-second child's wait to start taking evidence at 0.3 seconds, and check that the child's state, waitid, pidfd, and the wait queue the scheduler sees
  ;; are left (the production threshold is EVIDENCE-AFTER).
  (.setattr monkeypatch scheduler-module "EXTERNAL_STALL_LOG_INTERVAL_SECONDS" STALL-INTERVAL)
  (val evidence (run-with-evidence monkeypatch #("sleep" "1") implementation 0.3 0.2))
  (val text (.text evidence))
  (assert (isinstance (get evidence.answers 0) ProcessEnded) text)
  (for [needle ["stat-state=S" "waitid=None" "pidfd-poll=[]" "popen-returncode=None" "scheduler probes pending watched wait" "source_alive=True"]]
    (assert (in needle text) #(needle text))))


(deftest test-a-child-that-ended-without-its-exit-notice-is-a-stall [implementation monkeypatch caplog]
  {:params {"implementation" ["python" "rust"]}}
  ;; Failure case of rule S1: a wait where the child ended but the end notice never arrives (a lost wakeup - the target of the #495b watchdog) is a stall.
  ;; Replace the way of opening the fd that becomes readable on end with a pipe that is unreadable until written, and write to it only when the scheduler reports a stall. If it does not report,
  ;; it is written after 40 periods and the test is red.
  (.setattr monkeypatch scheduler-module "EXTERNAL_STALL_LOG_INTERVAL_SECONDS" STALL-INTERVAL)
  (.set-level caplog logging.WARNING :logger scheduler-module.__name__)
  (val pipe-ends (os.pipe))
  (val never-ready (get pipe-ends 0))
  (val notice (get pipe-ends 1))
  (.setattr monkeypatch process-exit-module "EXIT_FD_OPENERS" {(platform.system) (fn [pid] (Pure (os.dup never-ready)))})
  (val reported (threading.Event))
  (val watcher (OnStallReport (fn [] (.set reported))))
  (val logger (logging.getLogger scheduler-module.__name__))
  (deff notice-late []  ; cannot be a defk: entry called by threading.Thread on another thread (outside the VM)
    {:pre [] :post [(: % None)]}
    "To make the end-notice pipe readable when a stall is reported (after 40 periods if not reported)."
    (.wait reported :timeout (* STALL-INTERVAL 40))
    (os.write notice b"x")
    None)
  (.addHandler logger watcher)
  (.start (threading.Thread :target notice-late :daemon True))
  (val answers (try (get (timed (started-then-awaited #("true")) implementation) 0)
                    (finally (.removeHandler logger watcher)
                             (os.close never-ready)
                             (os.close notice))))
  (assert (isinstance (get answers 0) ProcessEnded) answers)
  (assert (.is-set reported) "a wait for a child that ended but whose notice never arrives is reported as a stall"))
