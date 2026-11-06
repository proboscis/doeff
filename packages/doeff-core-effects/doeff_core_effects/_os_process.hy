;;; The real responder subprocess-handler for the generic child-process effects (process_effects.hy). It only calls subprocess and os.environ
;;; and repackages the values, with no judgment of its own.
;;; ReadMachineName answers socket.gethostname as is.
;;; The child's stdin, stdout, stderr and output-path are read and written as utf-8 with surrogateescape (reversible - see the header note of process_effects.hy).
;;;
;;; Only calls that use process-group or stream-output start the child with Popen and wait while reading stdout and stderr line by line with
;;; 2 threads (run-watched). Calls that use neither go through the same wait as subprocess.run (Popen communicate - run-communicated).
;;; offloaded-subprocess-handler runs the same implementation with 1 thread per call (ThreadPerCall in offloaded_call.hy) - other scheduler
;;; tasks keep running while the child is awaited. It needs scheduled outside.
;;;
;;; When the waiting task is cancelled: the answer is dropped (keep-nothing) and the running child is stopped. The stopping is done by the offloaded
;;; stop method (interrupt), run in a new VM on another thread (so the scheduler's thread is not blocked during the grace period). For process-group, to the group; otherwise to
;;; the child, send SIGTERM -> wait for it (and its grandchildren, for a group) to be gone within RunProcess's stop-grace seconds -> if anything remains, send SIGKILL and reap. The rendezvous between the child and the cancellation is
;;; ChildWatch (the work thread puts the child it started there and the cancellation takes it out - a cancellation before the child is put is handled by the work thread that puts it).
;;; The stopped child is named in one log line (a warning from logger doeff_core_effects._os_process - head of argv, pid, how it was stopped), and in
;;; metered-offloaded-subprocess-handler, which is given a meter responder, counted once with CountMetric in the counters process_cancel_terminated (stopped by SIGTERM) and process_cancel_killed
;;; (needed SIGKILL - when rendered, _total is appended to the name). Counting happens outside the requesting run, so the caller that installs the handler supplies the meter (the requesting run's responder
;;; stack does not reach it). The stopping VM is new, so state is placed outside the meter - what keeps the count across runs is the per-process single-store responder
;;; (process-meter-handler); passing a responder inside one run (memory-meter-handler) does not show up in the requesting run's cross-section.
;;; offloaded-subprocess-handler is the form without a meter (it does not count, only logs - so users without a meter responder do not fail with "no responder").
;;; If the child already finished before the cancellation, nothing is sent and nothing is counted. For a child whose SIGKILL the OS refused (EPERM - e.g. a machine where AppArmor denies
;;; signals inside a container), it does not wait for the end, does not count it as stopped, and names it in one warning line (the child keeps running). The wait of subprocess-handler (not run on a thread) is not left by cancellation (it waits for the child to finish).
;;; StartProcess, PollProcess, StopProcess, SignalProcess (#2461) and WriteProcessInput (#3672) keep the started children in one per-process table,
;;; STARTED-CHILDREN. Starting, polling, sending a signal and writing a short line to the held stdin do not wait, so both responders answer on the spot. Stopping waits only during the grace period, so in offloaded-subprocess-handler
;;; it runs on a thread.
(require doeff-hy.macros [defhandler defk deff <- val var])
(require doeff-hy.record [defenum defrecord])
(val MODULE-TAGS {:context "process" :role "foundation"})
(import dataclasses [dataclass])
(import enum [StrEnum])
(import collections.abc [Callable])
(import contextlib)
(import fnmatch)
(import importlib.util)
(import io)
(import logging)
(import os)
(import signal)
(import socket)
(import subprocess)
(import sys)
(import threading)
(import time)
(import doeff [with-handlers])
(import doeff_core_effects._file_effects [FileFailed PathKind PathStat])
(import doeff_core_effects._handlers [state])
(import doeff_core_effects._meter_effects [CountMetric])
(import doeff_core_effects._os_file [stat-path])
(import doeff_core_effects._offloaded_call [ThreadPerCall offloaded run-detached keep-nothing])
(import doeff_lifeline._watch [Lifeline])
(import doeff_core_effects._process_effects [ChildLifetime EnvEntry EnvMode ProcessOutcome RunProcess ExecutableAt ReadEnvironment WorkingDirectory
                                            ProcessAlive StartProcess PollProcess StopProcess ProcessStarted ProcessNotStarted
                                            ProcessRunning ProcessExited ProcessNotChild SignalProcess ProcessSignal ProcessSignalled ProcessSignalRefused
                                            WriteProcessInput ProcessInputWritten
                                            ReadInterpreter ReadMachineName ResolveModule InterpreterFacts ModuleFound ModuleNotFound
                                            timed-out-outcome not-started-outcome executable-file-answer environment-answer])

;; Threads of offloaded-subprocess-handler (1 per call - the cap on concurrency is the number of calls the caller lines up).
(val PROCESS-THREADS (ThreadPerCall))

;; Names of the counters that count children stopped by cancellation (header note): the number stopped by SIGTERM, and the number that needed SIGKILL after the grace period. Do not append _total to the names -
;; the renderers (meter_prometheus, the /metrics of doeff-cluster's coordinator) append _total to counter names (#2938).
(val CANCEL-TERMINATED-METRIC "process_cancel_terminated")
(val CANCEL-KILLED-METRIC "process_cancel_killed")
;; Interval (seconds) for asking whether the processes (grandchildren) left in a group stopped by cancellation are gone.
(val GROUP-POLL 0.02)


;; Outcomes of stopping a child by cancellation (header note): NOT-RUNNING = it had finished before stopping (nothing sent), TERMINATED = it stopped within the grace period on SIGTERM,
;; KILLED = it remained after the grace period and was stopped with SIGKILL, REFUSED = the OS refused to send SIGKILL (EPERM - the child keeps running; not counted as stopped).
(defenum CancelStop NOT-RUNNING TERMINATED KILLED REFUSED)


;; Outcomes of sending a signal to a process group (the answer of signal-group): SENT = sent, GONE = the group no longer exists, REFUSED = the OS refused to send
;; (EPERM - a permission check or LSM. e.g. a machine where AppArmor denies signals between processes inside a container).
(defenum GroupSignal SENT GONE REFUSED)


(defrecord WatchedChild
  "One child to stop by cancellation (the content of a ChildWatch slot): child = the child started by the work thread, process-group = whether the child was started in its own group
    (stopped as a whole group)."
  {:tags {:context "process" :role "type"}}
  (#^ subprocess.Popen child)
  (#^ bool process-group))


(defclass ChildWatch []
  "The rendezvous between one RunProcess's child and the cancellation of that request (header note). The work thread puts the child it started (attach) and removes it (release) once it has seen it end.
    The cancellation handler marks the watch and takes out the child that is placed (cancel). Whichever comes first, the running child passes to the stopping side exactly once -
    if the request was cancelled before the child is put, attach does not put it and reports so, and the work thread that tried to put it stops it. stop-grace = the grace seconds
    from SIGTERM to SIGKILL (RunProcess's stop-grace), meter = the meter responder that counts stopped children (None = do not count). Reads and writes of the slot are under one lock (the work
    thread and the cancellation thread touch it concurrently). It is a resource handle, not a value type."

  (deff __init__ [self stop-grace meter]  ; defk にできない: initialization of a resource class
    {:pre [(: self ChildWatch) (: stop-grace float) (: meter (| Callable None))] :post [(: % None)]}
    "To hold the stop grace and the meter, and start with an empty child slot."
    (setv self.stop-grace stop-grace
          self.meter meter
          self.lock (threading.Lock)
          self.cancelled False
          self.running None))

  (deff attach [self child process-group]  ; defk にできない: lock entry of a resource class (synchronization between the work thread and the cancellation thread - a lock outside the VM)
    {:pre [(: self ChildWatch) (: child subprocess.Popen) (: process-group bool)] :post [(: % bool)]}
    "To put the started child. Answer = whether it was already cancelled (if True, do not put it - the caller stops it on the spot)."
    (with [self.lock]
      (if self.cancelled
          True
          (do (setv self.running (WatchedChild :child child :process-group process-group))
              False))))

  (deff release [self]  ; defk にできない: lock entry of a resource class (synchronization between the work thread and the cancellation thread - a lock outside the VM)
    {:pre [(: self ChildWatch)] :post [(: % None)]}
    "To empty the slot after seeing the child end (a later cancellation has no child to stop)."
    (with [self.lock]
      (setv self.running None)))

  (deff cancel [self]  ; defk にできない: lock entry of a resource class (synchronization between the work thread and the cancellation thread - a lock outside the VM)
    {:pre [(: self ChildWatch)] :post [(: % (| WatchedChild None))]}
    "To mark the cancellation and take out the child that is placed (None = not placed yet, or already removed - the work thread stops it when it puts the child)."
    (with [self.lock]
      (setv self.cancelled True)
      (setv found self.running)
      (setv self.running None)
      found)))


;; The starter-side entry of the promise that children end when the started process ends (one per process - Lifeline of doeff_lifeline._watch, #3866). Whenever a child whose lifetime is WITH-STARTER
;; is started, the watcher is told, and a child removed from the table is also removed from the watcher.
(val LIFELINE (Lifeline))


(defclass StartedChildren []
  "To hold, once per process, the table of children started by StartProcess (pid -> #(Popen process-group reap-group)) (a child is a per-OS-process resource,
      and the same child can be queried even if the responder stack is rebuilt). If the Popen is discarded, subprocess cleanup reaps the child and steals the exit code, so the table holds it until the end is answered.
      Reads and writes of the table are under one lock (the offloaded thread and the main body touch it concurrently)."
  (defn __init__ [self]
    (setv self.lock (threading.Lock))
    (setv self.children {}))

  (defn add [self child process-group reap-group]
    "Put the started child in the table."
    (with [self.lock]
      (setv (get self.children child.pid) #(child process-group reap-group))))

  (defn find [self pid]
    "The #(Popen process-group reap-group) of the child with this pid - None for a pid that was not started."
    (with [self.lock]
      (.get self.children pid)))

  (defn forget [self pid]
    "Remove a child whose end was answered from the table (afterwards that pid is ProcessNotChild). Close the child's stdin pipe the responder was holding (hold-stdin),
          and for a reap-group child send SIGKILL to the processes left in the group (#2471)."
    (with [self.lock]
      (setv found (.pop self.children pid None)))
    (when (is-not found None)
      (setv #(child process-group reap-group) found)
      (.forget LIFELINE pid)
      (when (is-not child.stdin None)
        (with [(contextlib.suppress OSError)] (.close child.stdin)))
      (when (and process-group reap-group)
        (with [(contextlib.suppress ProcessLookupError PermissionError)] (os.killpg pid signal.SIGKILL))))))


;; The table of children started by StartProcess (one per process - see the note on StartedChildren).
(val STARTED-CHILDREN (StartedChildren))


(defk os-executable-at [path]
  {:pre [(: path str)] :post [(: % bool)] :tags {:context "process" :role "foundation"}}
  "To read from the real filesystem whether an executable file exists at path: the kind follows symlinks (os.stat), and permission to execute is os.access with X_OK.
      A path that cannot be read (a file in the middle, a dir that cannot be entered) is treated the same as nonexistent. The judgment is executable-file-answer (the same function as the responder without I/O)."
  (<- seen (| PathStat FileFailed) (stat-path path True))
  (val kind (match seen
              (PathStat :kind found) found
              (FileFailed) PathKind.MISSING))
  (<- answer bool (executable-file-answer kind (os.access path os.X-OK)))
  answer)


(defk os-process-alive [pid]
  {:pre [(: pid int)] :post [(: % bool)] :tags {:context "process" :role "foundation"}}
  "To check with signal 0 whether the process with this pid is alive (the landing queue window tells apart the rows of dead runners). A process that merely lacks
      permission to be signalled is alive. A pid of 0 or less would signal a group, so nothing is sent and the answer is not alive."
  (if (<= pid 0)
      False
      (try
        (do (os.kill pid 0) True)
        (except [ProcessLookupError] False)
        (except [PermissionError] True))))


(defk os-interpreter-facts []
  {:pre [] :post [(: % InterpreterFacts)] :tags {:context "process" :role "foundation"}}
  "To read facts about this process's Python interpreter (the absolute path of sys.prefix resolved through symlinks, and the pid) - the entry check (doeff-cluster) that walks the root above the venv
      asks through an effect instead of reading where the interpreter lives directly."
  (InterpreterFacts :prefix (os.path.realpath sys.prefix) :pid (os.getpid)))


(defk os-module-location [name]
  {:pre [(: name str)] :post [(: % (| ModuleFound ModuleNotFound))] :tags {:context "process" :role "foundation"}}
  "To map a module name to the location this process's import resolves it to (the dir where files and submodules are searched - an absolute path resolved through symlinks) - the entry check (doeff-cluster) that verifies which tree's
      code is running asks through an effect instead of reading the import machinery directly.
      It does not import (a dotted name imports its parent package - same as importlib.util.find_spec). Cannot resolve / name is malformed / parent cannot be read
      = ModuleNotFound. The origin of a module without a file (a package with no __init__, built-in, frozen) is None."
  (val spec (try (importlib.util.find-spec name)
                 (except [[ImportError ValueError]] None)))
  (if (is spec None)
      (ModuleNotFound :name name)
      (ModuleFound :name name
                   :origin (if (and spec.origin (not-in spec.origin #("built-in" "frozen"))) (os.path.realpath spec.origin) None)
                   :search-locations (tuple (gfor d (or spec.submodule-search-locations []) (os.path.realpath d))))))


(defk decoded [value]
  {:pre [(: value (| str bytes None))] :post [(: % str)]}
  "The partial output held by TimeoutExpired may be bytes - to align it with the same reversible text as the whole answer (utf-8 and surrogateescape)."
  (cond
    (is value None) ""
    (isinstance value bytes) (.decode value "utf-8" "surrogateescape")
    True (str value)))


(defk append-output [output-path stdout stderr]
  {:pre [(: output-path (| str None)) (: stdout str) (: stderr str)] :post [(: % None)]}
  "To append the child's output to the end of output-path (do nothing if None). It is written with surrogateescape, so the file receives exactly the bytes the child emitted."
  (when (is-not output-path None)
    (with [handle (open output-path "a" :encoding "utf-8" :errors "surrogateescape")]
      (.write handle stdout)
      (.write handle stderr)))
  None)


(defk child-environment [env env-mode env-drop]
  {:pre [(: env (| tuple None)) (: env-mode EnvMode) (: env-drop tuple)] :post [(: % (| dict None))]}
  "To shape the child's whole environment variables for subprocess (None = inherit the caller's environment, REPLACE = the tuple is everything, EXTEND = remove from os.environ the names matching the env-drop
      patterns and add the tuple - for the same name the tuple wins)."
  (val given (if (is env None) None (dfor e env e.name e.value)))
  (match #(given env-mode)
    #(None _) None
    #(_ EnvMode.EXTEND) (| (dfor #(k v) (.items os.environ) :if (not (any (gfor p env-drop (fnmatch.fnmatchcase k p)))) k v) given)
    _ given))


(defk signal-group [pgid sig]
  {:pre [(: pgid int) (: sig int)] :post [(: % GroupSignal)] :tags {:context "process" :role "foundation"}}
  "To send a signal once to the child's process group. Answer = SENT, GONE (the group no longer exists), REFUSED (the OS refused to send - EPERM. The group
      keeps running). The stopping path does not count REFUSED as sent (ProcessSignalRefused in SignalProcess's answer)."
  (try
    (do (os.killpg pgid sig) GroupSignal.SENT)
    (except [ProcessLookupError] GroupSignal.GONE)
    (except [PermissionError] GroupSignal.REFUSED)))


(defk group-alive [pgid]
  {:pre [(: pgid int)] :post [(: % bool)] :tags {:context "process" :role "foundation"}}
  "To ask with signal 0 whether any process remains in the child's process group (to see off the grandchildren of a group stopped by cancellation). A process that merely lacks permission to be signalled
      is remaining."
  (try
    (do (os.killpg pgid 0) True)
    (except [ProcessLookupError] False)
    (except [PermissionError] True)))


(defk group-gone-by [child deadline]
  {:pre [(: child subprocess.Popen) (: deadline float)] :post [(: % bool)] :tags {:context "process" :role "foundation"}}
  "To answer whether the group that was sent SIGTERM became empty within the deadline (monotonic seconds): wait until the deadline for the group's head child to end (and reap it), then
      ask every GROUP-POLL seconds until the deadline whether the processes left in the group (background grandchildren) are gone.
      The wait between polls stays time.sleep (not doeff-time's Delay): this is the implementation of a responder (subprocess-handler, the cancellation handler of offloaded), and the
      cancellation handler runs in a new VM that the requesting responder stack does not reach (header note), so nothing is there to answer Delay. What is awaited is the end of a real
      process, and doeff-core-effects does not depend on doeff-time (ADR-DOE-TIME-001 R2 - :role foundation is
      that declaration)."
  (val leader-gone (try
                     (do (.wait child :timeout (max 0.0 (- deadline (time.monotonic)))) True)
                     (except [subprocess.TimeoutExpired] False)))
  (var left True)
  (when leader-gone
    (<- first bool (group-alive child.pid))
    (:= left first)
    (while (and left (< (time.monotonic) deadline))
      (time.sleep GROUP-POLL)
      (<- again bool (group-alive child.pid))
      (:= left again)))
  (not left))


(defk stop-for-cancel [child process-group stop-grace]
  {:pre [(: child subprocess.Popen) (: process-group bool) (: stop-grace float)] :post [(: % CancelStop)]
   :tags {:context "process" :role "foundation"}}
  "To stop a child whose request was cancelled (header note): for process-group, send SIGTERM to the group, otherwise to the child; if it (and, for a group,
      its grandchildren) is gone within stop-grace seconds, TERMINATED; if it remains, send SIGKILL and reap it: KILLED. For a child that had already finished before stopping (and, for a group, nothing remains), send nothing:
      NOT-RUNNING. SIGTERM and SIGKILL to the child alone go through the Popen entry (never sent to the pid of a reaped child). If the OS refuses to send SIGKILL
      (EPERM), REFUSED without waiting for the end - the child keeps running (a refused SIGTERM proceeds to SIGKILL after the grace period)."
  (val deadline (+ (time.monotonic) stop-grace))
  (val leader-done (is-not (.poll child) None))
  (if process-group
      (do
        (<- leftover bool (group-alive child.pid))
        (if (and leader-done (not leftover))
            CancelStop.NOT-RUNNING
            (do
              (<- (signal-group child.pid signal.SIGTERM))
              (<- gone bool (group-gone-by child deadline))
              (if gone
                  CancelStop.TERMINATED
                  (do
                    (<- killed GroupSignal (signal-group child.pid signal.SIGKILL))
                    (if (= killed GroupSignal.REFUSED)
                        CancelStop.REFUSED
                        (do (.wait child) CancelStop.KILLED)))))))
      (if leader-done
          CancelStop.NOT-RUNNING
          (do
            (<- (send-child-signal child signal.SIGTERM))
            (try
              (do (.wait child :timeout stop-grace) CancelStop.TERMINATED)
              (except [subprocess.TimeoutExpired]
                (<- killed GroupSignal (send-child-signal child signal.SIGKILL))
                (if (= killed GroupSignal.REFUSED)
                    CancelStop.REFUSED
                    (do (.wait child) CancelStop.KILLED))))))))


(defk reported-stop [child how metric meter]
  {:pre [(: child subprocess.Popen) (: how str) (: metric str) (: meter (| Callable None))] :post [(: % None)]
   :tags {:context "process" :role "foundation"}}
  "To name the child stopped by cancellation in one log line (head of argv, pid, how it was stopped), and if a meter exists, count it once in the metric counter (header note - the stopping
      VM is new, so state is placed outside the meter)."
  (.warning (logging.getLogger __name__) "Stopped a child process whose request was cancelled: head of argv %r, pid %d, %s" (get child.args 0) child.pid how)
  (when (is-not meter None)
    (<- (with-handlers [(state) meter] (CountMetric metric))))
  None)


(defk stop-cancelled-child [child process-group watch]
  {:pre [(: child subprocess.Popen) (: process-group bool) (: watch ChildWatch)] :post [(: % None)]
   :tags {:context "process" :role "foundation"}}
  "To stop a child whose request was cancelled using the watch's grace, and count the stop by name in the watch's meter (a child that had finished before stopping is not counted)."
  (<- stopped CancelStop (stop-for-cancel child process-group watch.stop-grace))
  (match stopped
    CancelStop.TERMINATED (<- (reported-stop child "stopped by SIGTERM" CANCEL-TERMINATED-METRIC watch.meter))
    CancelStop.KILLED (<- (reported-stop child (.format "still remained after {} seconds of grace and was stopped with SIGKILL" watch.stop-grace) CANCEL-KILLED-METRIC
                                         watch.meter))
    CancelStop.REFUSED (.warning (logging.getLogger __name__)
                                 "could not stop the child process whose request was cancelled (the OS refused to send SIGKILL; the child keeps running): argv head %r, pid %d"
                                 (get child.args 0) child.pid)
    CancelStop.NOT-RUNNING None)
  None)


(defk watched-child [watch child process-group]
  {:pre [(: watch ChildWatch) (: child subprocess.Popen) (: process-group bool)] :post [(: % None)]
   :tags {:context "process" :role "foundation"}}
  "To put the started child in the watch (so the cancellation handler can stop it). If the request was cancelled before putting, do not put it, but stop and count it here
      (see the note on ChildWatch)."
  (when (.attach watch child process-group)
    (<- (stop-cancelled-child child process-group watch)))
  None)


(defk stop-on-cancel [watch]
  {:pre [(: watch ChildWatch)] :post [(: % None)] :tags {:context "process" :role "foundation"}}
  "For the cancellation handler of a request (the offloaded stop method - run in a new VM on a thread other than the work thread): mark the cancellation, and if a child is placed
      stop and count it. If the child is not placed yet, the work thread stops it when it puts it (see the note on ChildWatch)."
  (match (.cancel watch)
    None None
    (WatchedChild :child child :process-group process-group) (<- (stop-cancelled-child child process-group watch)))
  None)


(defclass ChildPipes []
  "To feed stdin through a thread, read stdout and stderr line by line with 2 threads, accumulate the full text of each, and write to the sink (the opened
      output-path - None means do not write) in order of arrival (a part of run-watched - writes are under one lock). If the sink cannot be written, only the copy is lost
      and reading continues (the child's output is not blocked)."
  (defn __init__ [self child sink]
    (setv self.child child)
    (setv self.sink sink)
    (setv self.lock (threading.Lock))
    (setv self.out (io.StringIO))
    (setv self.err (io.StringIO))
    (setv self.readers (tuple (gfor #(buffer stream) #(#(self.out child.stdout) #(self.err child.stderr))
                                (threading.Thread :target self.take :args #(buffer stream) :daemon True)))))

  (defn take [self buffer stream]
    "Read one stream line by line until EOF (the body of a reader thread)."
    (for [line (iter stream.readline "")]
      (with [self.lock]
        (.write buffer line)
        (when self.sink
          (with [(contextlib.suppress OSError)]
            (.write self.sink line)
            (.flush self.sink))))))

  (defn give [self text]
    "Write text to stdin and close it (the body of a writer thread - it does not hang even if the child ends without reading)."
    (with [(contextlib.suppress BrokenPipeError OSError)]
      (.write self.child.stdin text)
      (.close self.child.stdin)))

  (defn start [self stdin]
    "Start the readers, and the writer too if there is stdin."
    (for [reader self.readers]
      (.start reader))
    (when (is-not stdin None)
      (.start (threading.Thread :target self.give :args #(stdin) :daemon True))))

  (defn drained [self until]
    "Wait for the readers until the deadline (monotonic seconds, None = wait fully). Answer = whether both finished reading to EOF."
    (for [reader self.readers]
      (.join reader (if (is until None) None (max 0.0 (- until (time.monotonic))))))
    (not (any (gfor reader self.readers (.is-alive reader)))))

  (defn texts [self]
    "The full stdout and stderr accumulated."
    (with [self.lock]
      #((.getvalue self.out) (.getvalue self.err)))))


(defk stop-child [child pipes process-group stop-grace]
  {:pre [(: child subprocess.Popen) (: pipes ChildPipes) (: process-group bool) (: stop-grace float)] :post [(: % None)]
   :tags {:context "process" :role "foundation"}}
  "To stop a child that timed out: for process-group, SIGTERM to the group -> grace -> SIGKILL (grandchildren stop too), otherwise kill only the child
      (same as subprocess.run). After stopping, wait only during the grace period for the output EOF and the child's end."
  (if process-group
      (do
        (<- (signal-group child.pid signal.SIGTERM))
        (when (not (.drained pipes (+ (time.monotonic) stop-grace)))
          (<- (signal-group child.pid signal.SIGKILL))
          (.drained pipes (+ (time.monotonic) stop-grace)))
        (try
          (.wait child :timeout stop-grace)
          (except [subprocess.TimeoutExpired]
            (<- (signal-group child.pid signal.SIGKILL))
            (.wait child))))
      (do
        (.kill child)
        (.drained pipes (+ (time.monotonic) stop-grace))
        (.wait child)))
  None)


(defk run-watched [argv stdin timeout cwd child-env output-path process-group stop-grace stream-output watch]
  {:pre [(: argv tuple) (: stdin (| str None)) (: timeout (| int float None)) (: cwd (| str None)) (: child-env (| dict None))
         (: output-path (| str None)) (: process-group bool) (: stop-grace float) (: stream-output bool) (: watch ChildWatch)]
   :post [(: % ProcessOutcome)] :tags {:context "process" :role "foundation"}}
  "To start the child with Popen and wait while reading its output on threads (a call that uses process-group or stream-output - header note). The wait is the same as communicate
      = the child's end plus output EOF. If it ends in time and uses process-group, send SIGTERM to the grandchildren left in the group. On timeout, stop it with stop-child and
      make a timeout answer holding the output so far. With stream-output, open output-path first and write the arrived lines on the spot (if it cannot be opened, OSError is raised without
      starting the child - same as when it cannot be appended after the child). The started child stays in the watch until its end is seen (stopped by cancellation of the request -
      header note)."
  (val sink (if (and stream-output (is-not output-path None))
                (open output-path "a" :encoding "utf-8" :errors "surrogateescape")
                None))
  (var outcome None)
  (try
    (var child None)
    (try
      (:= child (subprocess.Popen (list argv)
                                  :stdin (if (is stdin None) None subprocess.PIPE)
                                  :stdout subprocess.PIPE
                                  :stderr subprocess.PIPE
                                  :text True
                                  :encoding "utf-8"
                                  :errors "surrogateescape"
                                  :cwd cwd
                                  :env child-env
                                  :start-new-session process-group))
      (except [error OSError]
        (<- refused ProcessOutcome (not-started-outcome (str error)))
        (:= outcome refused)))
    (when (is-not child None)
      (try
        (<- (watched-child watch child process-group))
        (val pipes (ChildPipes child sink))
        (.start pipes stdin)
        (val until (if (is timeout None) None (+ (time.monotonic) timeout)))
        (val finished (try
                        (do (.wait child :timeout (if (is until None) None (max 0.0 (- until (time.monotonic)))))
                            (.drained pipes until))
                        (except [subprocess.TimeoutExpired] False)))
        (if finished
            (do
              (when process-group
                (<- (signal-group child.pid signal.SIGTERM)))
              (val texts (.texts pipes))
              (:= outcome (ProcessOutcome :exit-code child.returncode :stdout (get texts 0) :stderr (get texts 1))))
            (do
              (<- (stop-child child pipes process-group (float stop-grace)))
              (val partial (.texts pipes))
              (<- timed-out ProcessOutcome (timed-out-outcome (get partial 0) (get partial 1)))
              (:= outcome timed-out)))
        (finally
          (.release watch))))
    (finally
      (when sink
        (.close sink))))
  outcome)


(defk run-communicated [argv stdin timeout cwd child-env watch]
  {:pre [(: argv tuple) (: stdin (| str None)) (: timeout (| int float None)) (: cwd (| str None)) (: child-env (| dict None))
         (: watch ChildWatch)]
   :post [(: % ProcessOutcome)] :tags {:context "process" :role "foundation"}}
  "To run the child once with the same wait as subprocess.run (Popen communicate - on timeout kill only the child and reap it, making a timeout answer holding the output so far)
      (a call that uses neither process-group nor stream-output - header note). The reason for opening Popen without calling subprocess.run is to keep the started
      child in the watch until its end is seen, so the request's cancellation handler can stop the running child."
  (var outcome None)
  (try
    (with [child (subprocess.Popen (list argv)
                                   :stdin (if (is stdin None) None subprocess.PIPE)
                                   :stdout subprocess.PIPE
                                   :stderr subprocess.PIPE
                                   :text True
                                   :encoding "utf-8"
                                   :errors "surrogateescape"
                                   :cwd cwd
                                   :env child-env)]
      (try
        (<- (watched-child watch child False))
        ;; Same as subprocess.run: on timeout, kill the child, reap it and re-raise; on other failures also kill the child and re-raise (reaping happens at the end of the with).
        (val texts (try
                     (.communicate child stdin :timeout timeout)
                     (except [subprocess.TimeoutExpired]
                       (.kill child)
                       (.wait child)
                       (raise))
                     (except [BaseException]
                       (.kill child)
                       (raise))))
        (:= outcome (ProcessOutcome :exit-code (.poll child) :stdout (or (get texts 0) "") :stderr (or (get texts 1) "")))
        (finally
          (.release watch))))
    (except [error subprocess.TimeoutExpired]
      (<- partial-out str (decoded error.stdout))
      (<- partial-err str (decoded error.stderr))
      (<- timed-out ProcessOutcome (timed-out-outcome partial-out partial-err))
      (:= outcome timed-out))
    (except [error OSError]
      (<- refused ProcessOutcome (not-started-outcome (str error)))
      (:= outcome refused)))
  outcome)


(defk run-subprocess [argv stdin timeout cwd env env-mode output-path env-drop process-group stop-grace stream-output [watch None]]
  {:pre [(: argv tuple) (: stdin (| str None)) (: timeout (| int float None)) (: cwd (| str None)) (: env (| tuple None))
         (: env-mode EnvMode) (: output-path (| str None)) (: env-drop tuple) (: process-group bool) (: stop-grace (| int float))
         (: stream-output bool) (: watch (| ChildWatch None))]
   :post [(: % ProcessOutcome)]}
  "To run a child process once into a ProcessOutcome (see child-environment for how env and env-mode are read).
      exit-code does not round returncode. A timeout and a form that cannot be started are returned as values (header note of process_effects.hy). A call that uses process-group or stream-output
      goes to run-watched, otherwise to run-communicated, which waits the same way as subprocess.run. Nothing is appended later to an output-path written by stream-output.
      watch = the rendezvous between the request's cancellation and the child (passed by offloaded-subprocess-handler - header note). None = a request that is not cancelled (make a watch that only
      holds the child)."
  (<- child-env (| dict None) (child-environment env env-mode env-drop))
  (val watching (if (is watch None) (ChildWatch (float stop-grace) None) watch))
  (var outcome None)
  (if (or process-group stream-output)
      (do (<- watched ProcessOutcome (run-watched argv stdin timeout cwd child-env output-path process-group (float stop-grace) stream-output
                                                  watching))
          (:= outcome watched))
      (do (<- communicated ProcessOutcome (run-communicated argv stdin timeout cwd child-env watching))
          (:= outcome communicated)))
  (when (not stream-output)
    (<- (append-output output-path outcome.stdout outcome.stderr)))
  outcome)


(defk start-child-process [argv cwd env env-mode env-drop stdout-path stderr-path process-group [hold-stdin False] [reap-group False]
                           [lifetime ChildLifetime.WITH-STARTER]]
  {:pre [(: argv tuple) (: cwd (| str None)) (: env (| tuple None)) (: env-mode EnvMode) (: env-drop tuple) (: stdout-path (| str None))
         (: stderr-path (| str None)) (: process-group bool) (: hold-stdin bool) (: reap-group bool) (: lifetime ChildLifetime)]
   :post [(: % (| ProcessStarted ProcessNotStarted))] :tags {:context "process" :role "foundation"}}
  "To answer StartProcess with a real child: first open the output file in append mode (if it cannot be opened, do not start), start the child without stdin with Popen,
      put it in STARTED-CHILDREN, and return the pid without waiting for the end. The reason it cannot be started is the OSError text as is (same as RunProcess's cannot-start form)."
  (<- child-env (| dict None) (child-environment env env-mode env-drop))
  (try
    (with [streams (contextlib.ExitStack)]
      (val out (if (is stdout-path None) subprocess.DEVNULL (.enter-context streams (open stdout-path "ab"))))
      (val err (if (is stderr-path None) subprocess.DEVNULL (.enter-context streams (open stderr-path "ab"))))
      (val child (subprocess.Popen (list argv) :stdin (if hold-stdin subprocess.PIPE subprocess.DEVNULL) :stdout out :stderr err :cwd cwd
                                   :env child-env :start-new-session process-group))
      (.add STARTED-CHILDREN child process-group reap-group)
      (match lifetime
        ChildLifetime.WITH-STARTER (.watch LIFELINE child.pid process-group)
        ChildLifetime.OUTLIVES-STARTER None)
      (ProcessStarted :pid child.pid))
    (except [error OSError]
      (ProcessNotStarted :detail (str error)))))


(defk poll-child-process [pid]
  {:pre [(: pid int)] :post [(: % (| ProcessRunning ProcessExited ProcessNotChild))] :tags {:context "process" :role "foundation"}}
  "To answer PollProcess with a real child: a pid not in the table is ProcessNotChild, one still running is ProcessRunning, one that ended is removed from the table and
      ProcessExited (does not wait - Popen.poll)."
  (val found (.find STARTED-CHILDREN pid))
  (if (is found None)
      (ProcessNotChild :pid pid)
      (do
        (val code (.poll (get found 0)))
        (if (is code None)
            (ProcessRunning :pid pid)
            (do (.forget STARTED-CHILDREN pid)
                (ProcessExited :pid pid :exit-code code))))))


(defk stop-child-process [pid stop-grace]
  {:pre [(: pid int) (: stop-grace float)] :post [(: % (| ProcessExited ProcessSignalRefused ProcessNotChild))]
   :tags {:context "process" :role "foundation"}}
  "To answer StopProcess with a real child: a pid not in the table is ProcessNotChild (never signal someone else's process). If running, for process-group
      send SIGTERM to the group, otherwise to the child -> wait stop-grace seconds -> stop with SIGKILL, reap and remove from the table. A child that had already ended is reaped as is.
      If the OS refuses to send SIGKILL (EPERM), ProcessSignalRefused without waiting for the end - the child keeps running so it stays in the table (a refused SIGTERM proceeds to
      SIGKILL after the grace period)."
  (val found (.find STARTED-CHILDREN pid))
  (if (is found None)
      (ProcessNotChild :pid pid)
      (do
        (val child (get found 0))
        (val process-group (get found 1))
        (var killed GroupSignal.GONE)
        (when (is (.poll child) None)
          (<- (child-signal child process-group signal.SIGTERM))
          (try
            (.wait child :timeout stop-grace)
            (except [subprocess.TimeoutExpired]
              (<- sent GroupSignal (child-signal child process-group signal.SIGKILL))
              (:= killed sent)
              (when (!= killed GroupSignal.REFUSED)
                (.wait child)))))
        (if (= killed GroupSignal.REFUSED)
            (ProcessSignalRefused :pid pid)
            (do (.forget STARTED-CHILDREN pid)
                (ProcessExited :pid pid :exit-code child.returncode))))))


(defk child-signal [child process-group number]
  {:pre [(: child subprocess.Popen) (: process-group bool) (: number int)] :post [(: % GroupSignal)]
   :tags {:context "process" :role "foundation"}}
  "To send a signal once to the started child: a child started in a process-group goes to the group (signal-group), otherwise to the child (send-child-signal)."
  (<- sent GroupSignal (if process-group
                           (signal-group child.pid number)
                           (send-child-signal child number)))
  sent)


(defk signal-child-process [pid sent]
  {:pre [(: pid int) (: sent ProcessSignal)] :post [(: % (| ProcessSignalled ProcessSignalRefused ProcessNotChild))] :tags {:context "process" :role "foundation"}}
  "To answer SignalProcess with a real child: a pid not in the table is ProcessNotChild (never signal someone else's process). To a child that had already ended,
      do not send and delivered False (it stays in the table, and PollProcess answers its end and reaps it). If running, a child started in a process-group goes to the group, otherwise to
      the child; send the signal exactly once and delivered True without waiting. If the OS refuses to send (EPERM), ProcessSignalRefused - the child keeps running."
  (val found (.find STARTED-CHILDREN pid))
  (if (is found None)
      (ProcessNotChild :pid pid)
      (do
        (val child (get found 0))
        (val number (match sent ProcessSignal.TERM signal.SIGTERM ProcessSignal.KILL signal.SIGKILL))
        (if (is-not (.poll child) None)
            (ProcessSignalled :pid pid :delivered False)
            (do (<- outcome GroupSignal (child-signal child (get found 1) number))
                (match outcome
                  GroupSignal.REFUSED (ProcessSignalRefused :pid pid)
                  _ (ProcessSignalled :pid pid :delivered True)))))))


(defk send-child-signal [child number]
  {:pre [(: child subprocess.Popen) (: number int)] :post [(: % GroupSignal)] :tags {:context "process" :role "foundation"}}
  "To send a signal to one child not started in a group (via the Popen entry - never sent to the pid of a reaped child). The answer has the same form as signal-group."
  (try
    (do (.send-signal child number) GroupSignal.SENT)
    (except [ProcessLookupError] GroupSignal.GONE)
    (except [PermissionError] GroupSignal.REFUSED)))


(defk write-child-input [pid text]
  {:pre [(: pid int) (: text str)] :post [(: % (| ProcessInputWritten ProcessNotChild))] :tags {:context "process" :role "foundation"}}
  "To answer WriteProcessInput with a real child (#3672): a pid not in the table is ProcessNotChild. For a child that had already ended (it stays in the table, and PollProcess
      answers its end and reaps it), a child not holding the stdin pipe (not hold-stdin), or a pipe with no reader, do not write and delivered False. If running,
      write text as utf-8 and surrogateescape, flush, and delivered True without waiting (same practice as the bytes promise in the header note)."
  (val found (.find STARTED-CHILDREN pid))
  (if (is found None)
      (ProcessNotChild :pid pid)
      (do
        (val child (get found 0))
        (if (or (is-not (.poll child) None) (is child.stdin None))
            (ProcessInputWritten :pid pid :delivered False)
            (try
              (do (.write child.stdin (.encode text "utf-8" "surrogateescape"))
                  (.flush child.stdin)
                  (ProcessInputWritten :pid pid :delivered True))
              ;; A pipe with no reader (BrokenPipeError), or a pipe closed after reaping (ValueError).
              (except [#(BrokenPipeError ValueError)]
                (ProcessInputWritten :pid pid :delivered False)))))))


(defhandler subprocess-handler
  "Answer process effects with real child processes and this process's environment."
  ;; Real child processes and this process's environment (header note).
  (RunProcess [argv stdin timeout cwd env env-mode output-path env-drop process-group stop-grace stream-output]
    (<- outcome (run-subprocess argv stdin timeout cwd env env-mode output-path env-drop process-group stop-grace stream-output))
    (resume outcome))
  (ExecutableAt [path]
    (<- found (os-executable-at path))
    (resume found))
  (ReadEnvironment [names prefixes]
    (<- found tuple (environment-answer (tuple (.items os.environ)) names prefixes))
    (resume found))
  (WorkingDirectory []
    (resume (os.getcwd)))
  (ProcessAlive [pid]
    (<- alive (os-process-alive pid))
    (resume alive))
  (ReadInterpreter []
    (<- facts InterpreterFacts (os-interpreter-facts))
    (resume facts))
  (ReadMachineName []
    (resume (socket.gethostname)))
  (ResolveModule [name]
    (<- found (| ModuleFound ModuleNotFound) (os-module-location name))
    (resume found))
  (StartProcess [argv cwd env env-mode env-drop stdout-path stderr-path process-group hold-stdin reap-group lifetime]
    (<- started (start-child-process argv cwd env env-mode env-drop stdout-path stderr-path process-group hold-stdin reap-group lifetime))
    (resume started))
  (PollProcess [pid]
    (<- seen (poll-child-process pid))
    (resume seen))
  (SignalProcess [pid signal]
    (<- answer (signal-child-process pid signal))
    (resume answer))
  (WriteProcessInput [pid text]
    (<- written (write-child-input pid text))
    (resume written))
  (StopProcess [pid stop-grace]
    (<- stopped (stop-child-process pid (float stop-grace)))
    (resume stopped)))


(defk offloaded-run [argv stdin timeout cwd env env-mode output-path env-drop process-group stop-grace stream-output meter]
  {:pre [(: argv tuple) (: stdin (| str None)) (: timeout (| int float None)) (: cwd (| str None)) (: env (| tuple None))
         (: env-mode EnvMode) (: output-path (| str None)) (: env-drop tuple) (: process-group bool) (: stop-grace (| int float))
         (: stream-output bool) (: meter (| Callable None))]
   :post [(: % ProcessOutcome)] :tags {:context "process" :role "foundation"}}
  "To run one RunProcess on a PROCESS-THREADS thread and wait (the answer of offloaded-subprocess-handler - header note). If the waiting task is cancelled,
      drop the answer (keep-nothing), stop the running child on another thread, name it, and count it if a meter exists (stop-on-cancel)."
  (val watch (ChildWatch (float stop-grace) meter))
  (<- outcome ProcessOutcome
      (offloaded PROCESS-THREADS
                 (fn [] (run-detached (run-subprocess argv stdin timeout cwd env env-mode output-path env-drop process-group stop-grace
                                                      stream-output watch)))
                 keep-nothing
                 (fn [] (run-detached (stop-on-cancel watch)))))
  outcome)


(defhandler metered-offloaded-subprocess-handler [#^ (| (get Callable #(... object)) None) meter]
  "Run a real child process (the same implementation as subprocess-handler) with 1 thread per call (header note). It needs scheduled outside. If the waiting task is
      cancelled, stop the running child and count the stopped child in the meter's counter (a responder that answers CountMetric - None = do not count)."
  ;; Why it remains an argument: counting the stopped child happens outside the requesting run (a new VM on another thread after cancellation), and neither the requesting run's responder stack nor Ask
  ;; reaches it - the meter responder is supplied by the caller that installs the handler (header note).
  (RunProcess [argv stdin timeout cwd env env-mode output-path env-drop process-group stop-grace stream-output]
    (<- outcome ProcessOutcome (offloaded-run argv stdin timeout cwd env env-mode output-path env-drop process-group stop-grace
                                              stream-output meter))
    (resume outcome))
  (ExecutableAt [path]
    (<- found (os-executable-at path))
    (resume found))
  (ReadEnvironment [names prefixes]
    (<- found tuple (environment-answer (tuple (.items os.environ)) names prefixes))
    (resume found))
  (WorkingDirectory []
    (resume (os.getcwd)))
  (ProcessAlive [pid]
    (<- alive (os-process-alive pid))
    (resume alive))
  (ReadInterpreter []
    (<- facts InterpreterFacts (os-interpreter-facts))
    (resume facts))
  (ReadMachineName []
    (resume (socket.gethostname)))
  (ResolveModule [name]
    (<- found (| ModuleFound ModuleNotFound) (os-module-location name))
    (resume found))
  ;; Starting and polling do not wait, so answer on the spot. Stopping waits only during the grace period, so run it on a thread.
  (StartProcess [argv cwd env env-mode env-drop stdout-path stderr-path process-group hold-stdin reap-group lifetime]
    (<- started (start-child-process argv cwd env env-mode env-drop stdout-path stderr-path process-group hold-stdin reap-group lifetime))
    (resume started))
  (PollProcess [pid]
    (<- seen (poll-child-process pid))
    (resume seen))
  (SignalProcess [pid signal]
    (<- answer (signal-child-process pid signal))
    (resume answer))
  ;; Writing does not wait (a short line - header note), so answer on the spot.
  (WriteProcessInput [pid text]
    (<- written (write-child-input pid text))
    (resume written))
  (StopProcess [pid stop-grace]
    (<- stopped (offloaded PROCESS-THREADS (fn [] (run-detached (stop-child-process pid (float stop-grace)))) keep-nothing))
    (resume stopped)))


;; The form without a meter (header note - a child stopped by cancellation is not counted, only logged).
(val offloaded-subprocess-handler (metered-offloaded-subprocess-handler None))
