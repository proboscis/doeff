;;; scripted-process-handler: an I/O-free handler for the generic child-process effects (process_effects.hy). It does not start real child processes;
;;; it answers with a script (ScriptedCommand) per argv[0] name (basename). A script is a Program and may read and write its working area through the file system effects (file_effects.hy)
;;; (the outer file handler -- usually memory-file-handler -- receives them). It knows no business logic: the caller supplies the script contents.
;;;
;;;   RunProcess        With env-mode EXTEND, the script receives everything: the ProcessScript env inherited plus the additions. A missing cwd or an unknown command gives started False (exit-code 127) -- it cannot be started at the same place, in the same order, with the same message as a real subprocess
;;;                     (not-started-outcome and start-refusal in process_effects.hy). output-path appends the script's output to the end of that file, and if it cannot append,
;;;                     it raises OSError just like the real handler. The timeout is left to the script (the script may return a timed-out-outcome answer).
;;;                     When env is None the script also receives None (a marker meaning inherit the caller's environment -- reading the inherited contents is the script's side).
;;;                     process-group, stop-grace and stream-output are passed to the script's request as they are (groups and the passage of time do not exist in the script's
;;;                     world -- expressing how it was stopped in the answer is the script's side). For stream-output with an output-path, the output is appended after the script finishes
;;;                     (the file contents after completion are the same as the real handler, which writes in arrival order).
;;;                     The contract with the real handler is tests/test_process_contract.hy.
;;;   ProcessAlive      Whether the pid is in the ProcessScript alive set (table of live pids). A pid of 0 or less is not alive, same as the real handler.
;;;   StartProcess      Opens the output files first (appends nothing -- if it cannot open, ProcessNotStarted with the same message as the real handler), then runs the script once with timeout 0.
;;;                    An answer of a shape that cannot start is ProcessNotStarted. A timed-out answer = a child that keeps running until stopped; anything else =
;;;                     a child that finished immediately (its exit code is put in the table). The script's output (up to that point if timed out) is appended to the end of each file,
;;;                     and pids are handed out starting from SCRIPTED-FIRST-PID.
;;;   PollProcess       A pid not in the table = ProcessNotChild; a running child = ProcessRunning; a finished child = ProcessExited (removed from the table).
;;;   StopProcess       A pid not in the table = ProcessNotChild; a running child ends as if by SIGTERM (SCRIPTED-STOPPED-CODE = -15); a finished child gives its
;;;                     exit code. All are removed from the table. Groups and grace periods do not exist in the script's world.
;;;   SignalProcess     A pid not in the table = ProcessNotChild; a running child ends as if by that signal (TERM = -15, KILL = -9 -- the same negative
;;;                     values as the real handler. PollProcess reports the end and reaps it); nothing is sent to a finished child (delivered False) (#2461).
;;;   WriteProcessInput A pid not in the table = ProcessNotChild; a child started with hold-stdin and still running = written (delivered True -- there is no pipe in the script's world,
;;;                     and the contents are kept nowhere); otherwise (a finished child, or a child without hold-stdin) = not written (delivered False) (#3672).
;;;   ExecutableAt      The kind is StatPath of the working area (the file handler) -- a path missing from the working area is an executable file if its name (basename) is in the script, otherwise nothing.
;;;                     Permission to execute is the name being in the script. The decision is the same executable-file-answer as the real handler (a dir is False even if its name is in the script).
;;;   ReadEnvironment   From the ProcessScript env.
;;;   ReadInterpreter   The ProcessScript interpreter.
;;;   ReadMachineName   The ProcessScript machine-name.
;;;   ResolveModule     Looks up by name in the ProcessScript modules table. A name not in the table is ModuleNotFound (#2347).
;;;   WorkingDirectory  Each time it is asked, it creates a new empty dir (<work-root>/job-<n>) and answers with it -- standing in for the worker creating an empty working dir per job and starting the child in it
;;;                     (in a simulation that runs tasks in the same VM, it is asked once per task).
;;; Ordering: put the file handler outside this handler. The home of session values (the state of doeff_core_effects) is needed further outside.
;;;
;;; scripted-command-peer = an outside world of only the named commands: only for RunProcess whose argv[0] name (basename) is in the script, it answers with the same
;;; answer as scripted-process-handler (scripted-answer -- refusing a missing cwd, appending to output-path), and passes other child-process calls and process effects (reading environment variables, the question
;;; whether something is executable, working dir, questions about started children ...) to the outside. Use it layered inside the real handler or another handler on the outside (scripted-process-handler answers all of the process
;;; effects, so putting it inside an outer environment handler would hide that handler's answers). env is passed to the script as requested (the inherited environment is held by the outer handler; this peer
;;; holds no environment). The outside needs a file handler, same as scripted-process-handler.
(require doeff-hy.macros [defhandler defk <- val var])
(val MODULE-TAGS {:context "process" :role "foundation"})
(require doeff-hy.record [defrecord])
(import errno)
(import fnmatch)
(import posixpath)
(import collections.abc [Callable])
(import dataclasses [dataclass])
(import doeff [Program])
(import doeff_core_effects._process_effects [EnvEntry EnvMode ProcessOutcome RunProcess ExecutableAt ReadEnvironment WorkingDirectory
                                            ProcessAlive StartProcess PollProcess StopProcess ProcessStarted ProcessNotStarted
                                            ProcessRunning ProcessExited ProcessNotChild SignalProcess ProcessSignal ProcessSignalled
                                            WriteProcessInput ProcessInputWritten
                                            ReadInterpreter ReadMachineName ResolveModule InterpreterFacts ModuleFound ModuleNotFound
                                            not-started-outcome start-refusal executable-file-answer environment-answer])
(import doeff_core_effects._file_effects [PathKind PathStat StatPath MakeDirectory AppendText FileFailed])


(defrecord ScriptedCommand
  "One script command (name = basename of argv[0]; run = a Program answering (run commands request) -> ProcessOutcome. commands is the whole script table --
   a script that runs another command, like timeout, looks it up with run-scripted)."
  (#^ str name)
  ;; A string annotation because the type contains itself (ScriptedCommand) (the name does not exist yet when the class is built).
  (#^ "Callable[[tuple[ScriptedCommand, ...], RunProcess], Program[ProcessOutcome, object]]" run))


(defrecord ProcessScript
  "The world handed to scripted-process-handler (commands = tuple of ScriptedCommand; env = environment variables of this process; work-root = the parent under which a per-job working dir is
   created; alive = table of live pids -- the answer of ProcessAlive; interpreter = facts about this process's interpreter -- the answer of ReadInterpreter;
   machine-name = name of the machine this process runs on -- the answer of ReadMachineName; modules = table of module locations that import resolves -- the answer of ResolveModule. A name not in the table is ModuleNotFound). The elements of alive are pids (int) -- checked
   at construction (a value whose element type differs from the field type frozenset[int] is not silently held)."
  {:check [(all (gfor pid alive (isinstance pid int)))]}
  (#^ (get tuple #(ScriptedCommand ...)) commands)
  (setv #^ (get tuple #(EnvEntry ...)) env #())
  (setv #^ str work-root "/work/jobs")
  (setv #^ (get frozenset int) alive (frozenset))
  (setv #^ InterpreterFacts interpreter (InterpreterFacts :prefix "/" :pid 1))
  (setv #^ str machine-name "scripted-machine")
  (setv #^ (get tuple #(ModuleFound ...)) modules #()))


(defk scripted-child-env [inherited env env-mode env-drop]
  {:pre [(: inherited (of tuple EnvEntry ...)) (: env (| (of tuple EnvEntry ...) None)) (: env-mode EnvMode) (: env-drop (of tuple str ...))]
   :post [(: % (| (of tuple EnvEntry ...) None))]}
  "Builds the child environment passed to the script: None and REPLACE keep the env as given (the earlier behavior); EXTEND is everything made by removing the names matching the env-drop
   type from the script world's inherited environment and adding env (the same name is won by env -- the same rule as child-environment of the real subprocess-handler)."
  (match #(env env-mode)
    #(None _) None
    #(_ EnvMode.EXTEND) (do (val added (sfor e env e.name))
                            (+ (tuple (gfor e inherited :if (not (or (in e.name added)
                                                                     (any (gfor p env-drop (fnmatch.fnmatchcase e.name p)))))
                                            e))
                               env))
    _ env))


(defk run-scripted [commands request]
  {:pre [(: commands (of tuple ScriptedCommand ...)) (: request RunProcess)] :post [(: % ProcessOutcome)]}
  "Runs one command with the script (a missing cwd or an unknown command is a shape that cannot start). Calling another command from a script also calls this.
   Refuses in the same order as a real subprocess: first cwd (ENOENT if missing, ENOTDIR if not a dir), then the command (ENOENT)."
  (when (is-not request.cwd None)
    (<- stat (StatPath request.cwd))
    (val refused-errno (match stat
                         (FileFailed) errno.ENOENT
                         (PathStat :kind PathKind.DIRECTORY) None
                         (PathStat :kind PathKind.MISSING) errno.ENOENT
                         _ errno.ENOTDIR))
    (when (is-not refused-errno None)
      (<- cwd-refusal str (start-refusal refused-errno request.cwd))
      (<- no-cwd ProcessOutcome (not-started-outcome cwd-refusal))
      (return no-cwd)))
  (val name (posixpath.basename (get request.argv 0)))
  (val found (lfor c commands :if (= c.name name) c))
  (when (not found)
    (<- command-refusal str (start-refusal errno.ENOENT (get request.argv 0)))
    (<- missing ProcessOutcome (not-started-outcome command-refusal))
    (return missing))
  (<- outcome ProcessOutcome ((. (get found 0) run) commands request))
  outcome)


(defk scripted-answer [commands request]
  {:pre [(: commands (of tuple ScriptedCommand ...)) (: request RunProcess)] :post [(: % ProcessOutcome)] :tags {:context "process" :role "program"}}
  "Builds the script answer for one RunProcess (the one point of the answer shared by scripted-process-handler and scripted-command-peer): runs with run-scripted, and
   for a request with output-path appends the script output to the end of that file. An output-path that cannot be appended to raises an exception, like the real handler (the real handler's open after the child
   raises OSError, and no answer is returned)."
  (<- outcome ProcessOutcome (run-scripted commands request))
  (when (is-not request.output-path None)
    (<- appended (AppendText request.output-path (+ outcome.stdout outcome.stderr)))
    (when (isinstance appended FileFailed)
      (raise (OSError appended.detail))))
  outcome)


;; The start of pids of children started in the script world (a size that does not mix with real pids), and the exit code of a stopped child (a child ended by SIGTERM -- the same negative value as the real handler).
(val SCRIPTED-FIRST-PID 50000)
(val SCRIPTED-STOPPED-CODE -15)
;; The marker of a child in the script world that keeps running until stopped (a value in the table of started children -- a finished child holds its exit code).
(val SCRIPTED-RUNNING "running")


(defk scripted-open-outputs [paths]
  {:pre [(: paths (of tuple (| str None) ...))] :post [(: % (| ProcessNotStarted None))] :tags {:context "process" :role "program"}}
  "Opens the StartProcess output files before starting the child, in append mode (appends nothing) -- the real handler opens before Popen, and does not start if it cannot open.
   Answer = the reason for the first file that could not be opened (the same message as the real OSError), or None."
  (var refused None)
  (for [path paths]
    (when (and (is refused None) (is-not path None))
      (<- appended (AppendText path ""))
      (when (isinstance appended FileFailed)
        (:= refused (ProcessNotStarted :detail appended.detail)))))
  refused)


(defk scripted-append-outputs [stdout-path stderr-path outcome]
  {:pre [(: stdout-path (| str None)) (: stderr-path (| str None)) (: outcome ProcessOutcome)] :post [(: % None)]
   :tags {:context "process" :role "program"}}
  "Appends the script child's stdout and stderr to the end of their respective files (None is discarded -- the real DEVNULL)."
  (for [#(path text) #(#(stdout-path outcome.stdout) #(stderr-path outcome.stderr))]
    (when (and (is-not path None) text)
      (<- appended (AppendText path text))
      (when (isinstance appended FileFailed)
        (raise (OSError appended.detail)))))
  None)


(defk scripted-executable-at [commands path]
  {:pre [(: commands (of tuple ScriptedCommand ...)) (: path str)] :post [(: % bool)] :tags {:context "process" :role "judgment"}}
  "Answers ExecutableAt in the script world: the kind is StatPath of the working area (the outer file handler) -- a path missing from the working area is an executable file if its name (basename) is in the script
   (the script stands in for that command's file), otherwise nothing. Permission to execute is the name being in the script. The decision is the same executable-file-answer as the real handler."
  (val named (any (gfor c commands (= c.name (posixpath.basename path)))))
  (<- seen (| PathStat FileFailed) (StatPath path))
  (val kind (match seen
              (PathStat :kind PathKind.MISSING) (if named PathKind.FILE PathKind.MISSING)
              (PathStat :kind found) found
              (FileFailed) PathKind.MISSING))
  (<- answer bool (executable-file-answer kind named))
  answer)


(defhandler scripted-process-handler [#^ ProcessScript script]
  "A simulated handler that answers the process effects with script SCRIPT (commands, environment, work root, live pids, interpreter, machine name, modules)."
  ;; Why it stays an argument: the script table and environment are values that differ per scenario (not configuration but the simulated world itself).
  (session var jobs 0)
  ;; Table of children started that return immediately (StartProcess) (pid -> exit code or SCRIPTED-RUNNING), and the next pid to hand out.
  (session val started {})
  ;; Pids of children started with hold-stdin (children for which WriteProcessInput answers as written -- #3672).
  (session var held (frozenset))
  (session var next-pid SCRIPTED-FIRST-PID)
  (RunProcess [argv stdin timeout cwd env env-mode output-path env-drop process-group stop-grace stream-output]
    ;; The env the script sees is aligned with the full set of the child's environment variables (EXTEND adds to the script world's environment script.env minus env-drop -- the same as the real subprocess-handler).
    (<- child-env (| tuple None) (scripted-child-env script.env env env-mode env-drop))
    (<- outcome ProcessOutcome (scripted-answer script.commands (RunProcess :argv argv :stdin stdin :timeout timeout :cwd cwd :env child-env
                                                                             :output-path output-path :process-group process-group
                                                                             :stop-grace stop-grace :stream-output stream-output)))
    (resume outcome))
  (ExecutableAt [path]
    (<- found bool (scripted-executable-at script.commands path))
    (resume found))
  (ReadEnvironment [names prefixes]
    (<- found tuple (environment-answer (tuple (gfor e script.env #(e.name e.value))) names prefixes))
    (resume found))
  (WorkingDirectory []
    (:= jobs (+ jobs 1))
    (val work (posixpath.join script.work-root (.format "job-{}" jobs)))
    (<- made (MakeDirectory work))
    (when (isinstance made FileFailed)
      (raise (RuntimeError (.format "Cannot create working dir: {}" made.detail))))
    (resume work))
  (ProcessAlive [pid]
    (resume (and (> pid 0) (in pid script.alive))))
  (ReadInterpreter []
    (resume script.interpreter))
  (ReadMachineName []
    (resume script.machine-name))
  (ResolveModule [name]
    (resume (next (gfor m script.modules :if (= m.name name) m) (ModuleNotFound :name name))))
  ;; hold-stdin and reap-group (#2471) do not exist in the script world (no stdin pipe or group) -- accepted without changing behavior. Only the pid of a hold-stdin child is
  ;; remembered (children for which WriteProcessInput answers as written -- #3672).
  (StartProcess [argv cwd env env-mode env-drop stdout-path stderr-path process-group hold-stdin reap-group]
    (<- refused (| ProcessNotStarted None) (scripted-open-outputs #(stdout-path stderr-path)))
    (if (is-not refused None)
        (resume refused)
        (do
          (<- child-env (| tuple None) (scripted-child-env script.env env env-mode env-drop))
          ;; Run the script once with timeout 0: a timed-out answer = a child that keeps running until stopped; anything else = a child that finished immediately.
          (<- outcome ProcessOutcome (run-scripted script.commands (RunProcess :argv argv :cwd cwd :env child-env :timeout 0.0
                                                                                :process-group process-group)))
          (if (not outcome.started)
              (resume (ProcessNotStarted :detail outcome.start-error))
              (do
                (<- (scripted-append-outputs stdout-path stderr-path outcome))
                (val pid next-pid)
                (:= next-pid (+ next-pid 1))
                (setv (get started pid) (if outcome.timed-out SCRIPTED-RUNNING outcome.exit-code))
                (when hold-stdin
                  (:= held (| held (frozenset #(pid)))))
                (resume (ProcessStarted :pid pid)))))))
  (PollProcess [pid]
    (match (.get started pid)
      None (resume (ProcessNotChild :pid pid))
      state :if (= state SCRIPTED-RUNNING) (resume (ProcessRunning :pid pid))
      code (do (del (get started pid))
               (resume (ProcessExited :pid pid :exit-code code)))))
  (StopProcess [pid stop-grace]
    (match (.get started pid)
      None (resume (ProcessNotChild :pid pid))
      state (do (del (get started pid))
                (resume (ProcessExited :pid pid :exit-code (if (= state SCRIPTED-RUNNING) SCRIPTED-STOPPED-CODE state))))))
  (SignalProcess [pid signal]
    (match (.get started pid)
      None (resume (ProcessNotChild :pid pid))
      state :if (= state SCRIPTED-RUNNING) (do (setv (get started pid) (match signal ProcessSignal.TERM -15 ProcessSignal.KILL -9))  ; same negative value as when a real child ends by a signal
                                              (resume (ProcessSignalled :pid pid :delivered True)))
      _ (resume (ProcessSignalled :pid pid :delivered False))))
  (WriteProcessInput [pid text]
    (match (.get started pid)
      None (resume (ProcessNotChild :pid pid))
      state (resume (ProcessInputWritten :pid pid :delivered (and (= state SCRIPTED-RUNNING) (in pid held)))))))


;; The types of effects that scripted-command-peer answers -- so that users reading the types to pass from inside to outside when this peer is placed on the outside (such as a list of barriers) read them from the same
;; one place as the peer's answers (when the types the peer answers increase, add them here in the same change).
(val SCRIPTED-COMMAND-EFFECTS #(RunProcess))


(defhandler scripted-command-peer [#^ (get tuple #(ScriptedCommand ...)) commands]
  "A simulated handler that answers with the same answer as scripted-process-handler only for RunProcess whose argv[0] name is in the script COMMANDS, and passes other effects to the outer handler."
  ;; To layer an outside world of only the named commands inside the outer process handler (scripted-command-peer in the header comment): only for RunProcess whose argv[0] name (basename) is in the
  ;; script, answer with the same answer as scripted-process-handler, and pass other calls and process effects to the outside.
  ;; Why it stays an argument: the script table is a value that differs per scenario (not configuration but the simulated world itself).
  (RunProcess [argv stdin timeout cwd env env-mode output-path env-drop process-group stop-grace stream-output]
    :when (and argv (in (posixpath.basename (get argv 0)) (frozenset (gfor c commands c.name))))
    (<- outcome ProcessOutcome (scripted-answer commands effect))
    (resume outcome)))
