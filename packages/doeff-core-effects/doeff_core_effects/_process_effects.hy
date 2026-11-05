;;; Generic child-process effects (consumers = #796 daily full verification, #795 webapp assembly, and the driver layer of doeff-agents).
;;; A foundation vocabulary with no business terms, at the same level as HttpRequest (http_effects.hy). The handler is swapped per mechanism:
;;;   subprocess-handler            Real child processes and os.environ (os_process.hy). The driver-io-handler of doeff-agents calls the same implementation
;;;   offloaded-subprocess-handler  Runs the same implementation as the real one, one thread per call (os_process.hy) -- other tasks of the
;;;                                 scheduler keep running while waiting for a child (RunProcess calls placed side by side run concurrently with Spawn / Gather; the tick keeps turning during the child).
;;;                                 Needs scheduled outside. When a waiting task is cancelled, the running child is stopped (#2847 -- stop-grace below).
;;;                                 metered-offloaded-subprocess-handler is the form that counts the stopped children with a meter (pass it the meter's handler)
;;;   scripted-process-handler      No I/O -- a script per command name, plus fixed environment variables and a per-job working dir (scripted_process.hy)
;;;
;;; RunProcess, ExecutableAt and ProcessOutcome were moved from io_effects of doeff-agents (one definition -- io_effects re-exports the same types). The fields
;;; added when moving all have default values, so the behavior of current users does not change:
;;;   env of RunProcess (None = inherit the caller's environment; a tuple of EnvEntry = as per env-mode), output-path (None = do not append; a path = append the child's output to the
;;;   end of that file -- the answer also carries stdout / stderr).
;;;   env-mode of RunProcess = EnvMode, a closed type for how the env tuple is treated:
;;;     REPLACE (default)  the tuple is all of the child's environment variables (the previous behavior)
;;;     EXTEND             inherit the caller's environment and add the tuple (for the same name, the tuple wins)
;;;   When env is None, env-mode is not read (the caller's whole environment is inherited).
;;;   env-drop of RunProcess = a tuple of name patterns (fnmatch -- like `UV_*`) to remove from the caller's environment inherited under EXTEND. Default #()
;;;   = remove nothing. Not read under REPLACE or env None (REPLACE inherits nothing, so there is nothing to remove). Used to keep the caller's venv and tool settings out of the child.
;;;   started of ProcessOutcome (False = could not be started -- the OSError as a value) and start-error (its reason). exit-code keeps the child's returncode unrounded
;;;   (a negative value = signal, 137, etc.). A timeout is timed-out True (exit-code 124); when the child cannot be started, exit-code 127.
;;;   process-group, stop-grace and stream-output of RunProcess (how to run the commands of the landing-queue window gate):
;;;     process-group  True = run the child in a new session (its own process group). On timeout: SIGTERM to the group -> wait stop-grace seconds ->
;;;                    SIGKILL (grandchildren the child started are stopped too). Even after finishing in time, SIGTERM is sent to children left in the group (grandchildren that went to the background).
;;;                    Default False = the previous behavior (on timeout only the child is stopped)
;;;     stop-grace     grace seconds from SIGTERM to SIGKILL when stopping a child (default 10.0): on a process-group timeout, and in offloaded-subprocess-handler
;;;                    when a waiting task is cancelled (regardless of process-group -- sent to the group if it is one, otherwise to the child; #2847)
;;;     stream-output  True = run while writing the child's output to output-path in arrival order (the output of a child stopped on timeout also stays in the file -- the red evidence
;;;                    the runner's log points to). Default False = append stdout then stderr after the child ends (the previous behavior). Not read when output-path is None
;;;   Every way of waiting is the same as subprocess communicate = the child's exit plus EOF of the output (if a background grandchild holds the output, wait until the deadline).
;;;   ProcessAlive = whether the process pid is alive. A pid of 0 or less is not alive (never turned into a signal to a group).
;;;
;;; Convention for strings and bytes: stdin of RunProcess and stdout / stderr of ProcessOutcome are strings that read and write the child's bytes as utf-8 with
;;; surrogateescape (reversible -- the same practice as Python's os.fsdecode). Valid utf-8 stays an ordinary string; broken bytes are represented by
;;; surrogate characters (U+DC80 to U+DCFF). Users who need bytes (for example passing a git diff to patch-id) restore the original bytes with `.encode "utf-8" "surrogateescape"`,
;;; and when passing bytes, turn them into a string by the same practice and put it in stdin. When shown outside (JSON, log), the caller reads them with replacement.
;;;
;;;   RunProcess        Run a child process once and wait for its end. Answer = ProcessOutcome.
;;;   ExecutableAt      Whether an executable file exists at that path. Answer = bool. A symlink is judged by what it points to; a dir, a missing path, or a non-file is False even with the execute bit
;;;                     (the judgment is in one place, executable-file-answer -- the real handler and the no-I/O handler call the same function).
;;;   ReadEnvironment   The names of names among this process's environment variables. Answer = a tuple of EnvEntry for only those present (in the order of names). prefixes (#2472;
;;;                     default #()) = also picks up names starting with any of them (LC_* etc. -- a family whose names are not known beforehand). The picked ones follow those of names, in name order.
;;;                     The real handler and the script build the answer with the same function, environment-answer.
;;;   WorkingDirectory  This process's working dir (absolute path).
;;;   ProcessAlive      Whether the process pid is alive. Answer = bool (real = whether signal 0 can be sent; a process we merely lack permission to signal is alive).
;;;   ReadInterpreter   Facts about this process's Python interpreter. Answer = InterpreterFacts (prefix = absolute path of sys.prefix resolved through symlinks;
;;;                     pid) (consumer = the entry check of doeff-cluster: reads the root above the venv and the pid to put in the report).
;;;   ReadMachineName   The name of the machine this process runs on. Answer = a name (str -- real = socket.gethostname as is; script = machine-name of ProcessScript)
;;;                     (consumer = the leader's self-name of the merge-queue controller
;;;                     <machine name>/<pid>/<random>. The pid is read from ReadInterpreter).
;;;   ResolveModule     Resolve a module name to the place this process's import would resolve it (without importing -- for a dotted name, importing the parent package
;;;                     is the same as importlib.util.find_spec). Answer = ModuleFound (origin = absolute path of the file, None for a module with no file [a package without
;;;                     __init__, built-in, frozen] / search-locations = a tuple of absolute dir paths to search for submodules) or
;;;                     ModuleNotFound (cannot be resolved, or the name is broken). Paths are resolved through symlinks (#2347 -- to confirm which tree's code is running).
;;;
;;; Children that return as soon as they are started (consumer = the dispatch arm of the merge-queue controller. The tick does not wait for the child):
;;;   StartProcess      Start a child and return without waiting for its end. Answer = ProcessStarted (pid) or ProcessNotStarted (a reason text -- the output file cannot be opened,
;;;                     cwd is missing, or the command is missing. The same text as the could-not-start form of RunProcess, and the output file is checked first). The child has no standard input; standard output and
;;;                     standard error go to the end of the files stdout-path and stderr-path (None = discarded). Not a pipe -- if left unread, the child stops at about 64 KB.
;;;                     env, env-mode, env-drop, cwd and process-group are the same as RunProcess.
;;;                     hold-stdin (#2471; default False): make the child's standard input a pipe whose writing side the handler holds -- the child reads EOF when the handler's process dies
;;;                     (the worker shim of doeff-cluster stops the job's group on EOF -- so no job remains even if the worker dies by kill -9).
;;;                     Closed when PollProcess / StopProcess reaps the child. reap-group (#2471; default False): when reaping the end of a child started with process-group,
;;;                     send SIGKILL to the processes left in that group (grandchildren that went to the background).
;;;                     lifetime (#3866; closed type ChildLifetime; default WITH-STARTER): when the process that started it ends (including an end by SIGKILL that runs no cleanup),
;;;                     the child ends too -- a process-group child along with its whole group, otherwise only that process. The real handler
;;;                     starts one watcher per process (doeff_lifeline of doeff-core-effects) at the first StartProcess and tells it the children started.
;;;                     The watcher learns of the starter's end by EOF on a pipe: TERM -> 10 seconds grace -> KILL. Children of OUTLIVES-STARTER are
;;;                     not told to it (they keep running after the starting process).
;;;   PollProcess       Ask about a started child just once, without waiting. Answer = ProcessRunning, ProcessExited (exit code -- reaped when answered, and the pid is forgotten), or
;;;                     ProcessNotChild (a pid that is not a child this handler started).
;;;   StopProcess       Stop a started child and reap it: SIGTERM to the group if process-group, otherwise to the child -> wait stop-grace seconds -> SIGKILL.
;;;                     Answer = ProcessExited or ProcessNotChild (no signal is sent to someone else's process). A child that had already ended is reaped as is.
;;;                     If the OS refuses to send SIGKILL (EPERM), ProcessSignalRefused without waiting for the end -- the child stays in the table still running (waiting for the end after a
;;;                     refused KILL would make the stop call never return).
;;;   SignalProcess     Send a signal (TERM or KILL of ProcessSignal) to a started child just once and return without waiting (#2461 -- consumer = how the doeff-cluster
;;;                     worker stops its children: send TERM every tick, send KILL at the next stage if it does not stop, and confirm the end with PollProcess).
;;;                     A child started with process-group gets it at the group, otherwise at the child (same as StopProcess). Answer = ProcessSignalled (delivered =
;;;                     whether it was sent -- not sent to a child that had already ended; reaping is PollProcess) or ProcessSignalRefused (the OS refused the send -- EPERM. The child
;;;                     keeps running. Example = a machine where AppArmor denies signals inside a container) or ProcessNotChild (no signal to someone else's process).
;;;   WriteProcessInput Write text to the standard-input pipe of a child started with hold-stdin and return without waiting (#3672 -- consumer = the doeff-cluster worker sends the shim
;;;                     the line announcing the job's withdrawal). Answer = ProcessInputWritten (delivered = whether it was written -- not written to a child that had ended, a child not holding a pipe
;;;                     [not hold-stdin], or a pipe with no reader) or ProcessNotChild (a pid not started here). Only short lines may be written
;;;                     (beyond the pipe capacity of about 64 KB, the write waits until the reader reads).
;;;   The real handler keeps one table of started children per process (a child is an OS per-process resource -- the same child can be asked about even after the handler is restacked).
;;;   AwaitProcessExit  Wait until a started child ends (#3871 -- consumer = the wait between rounds of the doeff-cluster worker. Wakes at the child's end). Answer =
;;;                     ProcessEnded (ended -- not reaped; the exit code is answered by a later PollProcess) or ProcessNotChild. The handler is separate from
;;;                     subprocess-handler (process-exit-handler in process_exit.hy -- how the fd is opened differs per machine: Linux = pidfd, macOS = kqueue). Needs
;;;                     await-handler and scheduled outside. If the waiting task is cancelled, the waiting resource (the fd that becomes readable on exit) is closed.
;;;
;;; The answers for a timeout and the could-not-start form (timed-out-outcome, not-started-outcome), and the could-not-start reason text (start-refusal -- the same form as the OSError text), are
;;; built only once, here. The real (os_process.hy) and no-I/O (scripted_process.hy) handlers call the same functions (answering in the same form -- contract test
;;; tests/test_process_contract.hy).
(require doeff-hy.macros [defk val])
(require doeff-hy.record [defrecord defenum])
(import os)
(import dataclasses [dataclass])
(import enum [StrEnum])
(import doeff [EffectBase])
(import doeff_core_effects._file_effects [PathKind])

;; exit-code for a timeout (same as coreutils timeout), and exit-code when it cannot be started (same as a shell).
(val TIMED-OUT-CODE 124)
(val NOT-STARTED-CODE 127)


;; How RunProcess treats the env tuple (header note): REPLACE = all of the child's environment variables; EXTEND = inherit the caller's environment and add.
(defenum EnvMode REPLACE EXTEND)
(setv EnvMode.__doc__ "How RunProcess treats env: REPLACE = all of the child's environment variables; EXTEND = inherit the caller's environment and add.")


;; What happens to a child started by StartProcess after the starting process ends (header note): WITH-STARTER (default) = when the starting
;; process ends (including SIGKILL), the child ends too; OUTLIVES-STARTER = the child keeps running even after the starting process ends (written only in the one place of a user
;; that has a reason -- the landing window of merge-queue).
(defenum ChildLifetime WITH-STARTER OUTLIVES-STARTER)
(setv ChildLifetime.__doc__ "What happens to a child started by StartProcess after the starting process ends: WITH-STARTER = ends together (default); OUTLIVES-STARTER = keeps running.")


(defrecord EnvEntry
  "One environment variable (name and value)."
  (#^ str name)
  (#^ str value))


(defclass [(dataclass :frozen True :kw-only True)] ProcessOutcome []
  "The result of one child process run. The judgment (interpreting success or failure) is the caller's, so it does not raise (header note). stdout / stderr are reversible strings of the child's bytes
   (utf-8 with surrogateescape -- the strings and bytes convention in the header note)."
  #^ int exit-code
  #^ str stdout
  #^ str stderr
  #^ bool timed-out
  (setv timed-out False)
  #^ bool started
  (setv started True)
  #^ str start-error
  (setv start-error ""))


;; Each effect writes its answer type in the base EffectBase[answer] (ADR-DOE-API-001 R2 -- the answer record is placed before the effect that answers it).
(defclass [(dataclass :frozen True :kw-only True)] RunProcess [(get EffectBase ProcessOutcome)]
  "Run a child process once and wait for its end (header note). The exit code is returned as a value."
  #^ (get tuple #(str ...)) argv
  #^ (| str None) stdin
  (setv stdin None)
  #^ (| float None) timeout
  (setv timeout None)
  #^ (| str None) cwd
  (setv cwd None)
  #^ (| (get tuple #(EnvEntry ...)) None) env
  (setv env None)
  #^ EnvMode env-mode
  (setv env-mode EnvMode.REPLACE)
  #^ (get tuple #(str ...)) env-drop
  (setv env-drop #())
  #^ (| str None) output-path
  (setv output-path None)
  #^ bool process-group
  (setv process-group False)
  #^ float stop-grace
  (setv stop-grace 10.0)
  #^ bool stream-output
  (setv stream-output False))


(defclass [(dataclass :frozen True :kw-only True)] ExecutableAt [(get EffectBase bool)]
  "Whether an executable file exists at that path."
  #^ str path)


(defclass [(dataclass :frozen True)] ReadEnvironment [(get EffectBase (get tuple #(EnvEntry ...)))]
  "Read the names of names among this process's environment variables, plus the names starting with any of prefixes (header note)."
  (#^ (get tuple #(str ...)) names)
  (setv #^ (get tuple #(str ...)) prefixes #()))


(defk environment-answer [present names prefixes]
  {:pre [(: present (get tuple #((get tuple #(str str)) ...))) (: names (get tuple #(str ...))) (: prefixes (get tuple #(str ...)))]
   :post [(: % (get tuple #(EnvEntry ...)))] :tags {:context "process" :role "judgment"}}
  "To let the real handler (os.environ) and the script (env of ProcessScript) build the answer of ReadEnvironment by the same rule (#2472): present = the sequence of present environment variables as
   #(name value). Answer = those of names that are present in order, followed by names starting with any of prefixes (those not in names) in name order -- the same name only once."
  (val values (dict present))
  (val named (tuple (gfor name names :if (in name values) (EnvEntry :name name :value (get values name)))))
  (val prefixed (tuple (gfor name (sorted values)
                             :if (and (not-in name names) (any (gfor p prefixes (.startswith name p))))
                             (EnvEntry :name name :value (get values name)))))
  (+ named prefixed))


(defk environment-mapping []
  {:pre [] :post [(: % (get dict #(str str)))] :tags {:context "process" :role "program"}}
  "To receive all of this process's environment variables as a name -> value mapping (the source of a child process's env, for callers that pass the whole environment to resolve settings).
   Does not read os.environ directly but asks ReadEnvironment with the prefix \"\" (matches every name) -- the one that answers is the outer
   foundation handler (real = subprocess-handler of os_process; script = scripted-process-handler of scripted_process)."
  (<- entries (get tuple #(EnvEntry ...)) (ReadEnvironment #() #("")))
  (dfor entry entries entry.name entry.value))


(defclass [(dataclass :frozen True)] WorkingDirectory [(get EffectBase str)]
  "This process's working dir (header note).")


(defclass [(dataclass :frozen True)] ProcessAlive [(get EffectBase bool)]
  "Whether the process pid is alive (header note)."
  (#^ int pid))


(defrecord InterpreterFacts
  "Facts about this process's Python interpreter (prefix = absolute path of sys.prefix resolved through symlinks; pid = this process's id)."
  (#^ str prefix)
  (#^ int pid))


(defclass [(dataclass :frozen True)] ReadInterpreter [(get EffectBase InterpreterFacts)]
  "Read facts about this process's Python interpreter (header note). Answer = InterpreterFacts.")


(defclass [(dataclass :frozen True)] ReadMachineName [(get EffectBase str)]
  "Read the name of the machine this process runs on (header note). Answer = a name (str).")


(defrecord ModuleFound
  "The place of a module that import resolved (origin = absolute path of the file, or None -- a module with no file; search-locations = a tuple of absolute dir paths to search for
   submodules -- empty if it is not a package)."
  (#^ str name)
  (#^ (| str None) origin)
  (#^ (get tuple #(str ...)) search-locations))


(defrecord ModuleNotFound
  "The name of a module that import cannot resolve (missing, a broken name, or the parent package cannot be read)."
  (#^ str name))


(defclass [(dataclass :frozen True)] ResolveModule [(get EffectBase (| ModuleFound ModuleNotFound))]
  "Resolve a module name to the place this process's import would resolve it (header note). Answer = ModuleFound or ModuleNotFound."
  (#^ str name))


(defrecord ProcessStarted
  "A child was started (pid = the mark of the child to ask about with PollProcess and StopProcess)."
  (#^ int pid))


(defrecord ProcessNotStarted
  "A child could not be started (detail = a reason in the same form as the OSError text -- the output file cannot be opened, cwd is missing, or the command is missing)."
  (#^ str detail))


(defclass [(dataclass :frozen True :kw-only True)] StartProcess [(get EffectBase (| ProcessStarted ProcessNotStarted))]
  "Start a child process and return without waiting for its end (header note). Answer = ProcessStarted or ProcessNotStarted."
  #^ (get tuple #(str ...)) argv
  #^ (| str None) cwd
  (setv cwd None)
  #^ (| (get tuple #(EnvEntry ...)) None) env
  (setv env None)
  #^ EnvMode env-mode
  (setv env-mode EnvMode.REPLACE)
  #^ (get tuple #(str ...)) env-drop
  (setv env-drop #())
  #^ (| str None) stdout-path
  (setv stdout-path None)
  #^ (| str None) stderr-path
  (setv stderr-path None)
  #^ bool process-group
  (setv process-group False)
  #^ bool hold-stdin
  (setv hold-stdin False)
  #^ bool reap-group
  (setv reap-group False)
  #^ ChildLifetime lifetime
  (setv lifetime ChildLifetime.WITH-STARTER))


(defrecord ProcessRunning
  "The child is still running."
  (#^ int pid))


(defrecord ProcessExited
  "The child ended and was reaped (exit-code = returncode unrounded -- a negative value = signal). After this, that pid is ProcessNotChild."
  (#^ int pid)
  (#^ int exit-code))


(defrecord ProcessNotChild
  "A pid that is not a child this handler started (never started, or already answered as ended and reaped) -- so that no signal is sent to someone else's process."
  (#^ int pid))


(defclass [(dataclass :frozen True)] PollProcess [(get EffectBase (| ProcessRunning ProcessExited ProcessNotChild))]
  "Ask about the status of a child started by StartProcess just once, without waiting (header note). Answer = ProcessRunning, ProcessExited or ProcessNotChild."
  (#^ int pid))


(defrecord ProcessSignalRefused
  "Answer of SignalProcess, StopProcess and SignalWarmChild: tried to send to a running child, but the OS refused the send (EPERM -- a permission check or an LSM
   denial). The child keeps running, so the caller does not count it as 'sent' or 'stopped'."
  (#^ int pid))


(defclass [(dataclass :frozen True :kw-only True)] StopProcess [(get EffectBase (| ProcessExited ProcessSignalRefused ProcessNotChild))]
  "Stop a child started by StartProcess and reap it (header note). Answer = ProcessExited, ProcessSignalRefused or ProcessNotChild."
  #^ int pid
  #^ float stop-grace
  (setv stop-grace 10.0))


(defrecord ProcessEnded
  "The awaited process ended (answer of AwaitProcessExit and AwaitWarmChildExit -- not reaped. The exit code is answered by PollProcess and PollWarmChild)."
  (#^ int pid))


(defclass [(dataclass :frozen True)] AwaitProcessExit [(get EffectBase (| ProcessEnded ProcessNotChild))]
  "Wait until a child started by StartProcess ends (header note). Answer = ProcessEnded or ProcessNotChild."
  (#^ int pid))


;; Closed type of the signal sent by SignalProcess (#2461): TERM = ask it to stop (the child can clean up and end); KILL = force it to stop.
(defenum ProcessSignal TERM KILL)
(setv ProcessSignal.__doc__ "The signal sent by SignalProcess: TERM = ask it to stop; KILL = force it to stop.")


(defrecord ProcessSignalled
  "Answer of SignalProcess: delivered = sent to a running child (True); not sent because the child had already ended (False -- PollProcess answers
   the end and reaps it). Whether the child ended after sending is asked with PollProcess."
  (#^ int pid)
  (#^ bool delivered))


(defclass [(dataclass :frozen True :kw-only True)] SignalProcess [(get EffectBase (| ProcessSignalled ProcessSignalRefused ProcessNotChild))]
  "Send a signal just once to a child started by StartProcess and return without waiting (header note). Answer = ProcessSignalled, ProcessSignalRefused or ProcessNotChild."
  #^ int pid
  #^ ProcessSignal signal)


(defrecord ProcessInputWritten
  "Answer of WriteProcessInput: delivered = written to the child's standard-input pipe (True); not written (False -- a child that had ended, a child not holding a pipe,
   or a pipe with no reader. PollProcess answers the end and reaps it)."
  (#^ int pid)
  (#^ bool delivered))


(defclass [(dataclass :frozen True :kw-only True)] WriteProcessInput [(get EffectBase (| ProcessInputWritten ProcessNotChild))]
  "Write text to the standard-input pipe of a child started with hold-stdin and return without waiting (header note). Answer = ProcessInputWritten or ProcessNotChild."
  #^ int pid
  #^ str text)


(defk timed-out-outcome [stdout stderr]
  {:pre [(: stdout str) (: stderr str)] :post [(: % ProcessOutcome)] :tags {:context "process" :role "judgment"}}
  "To build the answer for a timeout (carries the output so far in stdout / stderr; exit-code 124; timed-out True)."
  (ProcessOutcome :exit-code TIMED-OUT-CODE :stdout stdout :stderr stderr :timed-out True :started True :start-error ""))


(defk not-started-outcome [detail]
  {:pre [(: detail str)] :post [(: % ProcessOutcome)] :tags {:context "process" :role "judgment"}}
  "To build the answer for the could-not-start form (exit-code 127; started False; reason in detail)."
  (ProcessOutcome :exit-code NOT-STARTED-CODE :stdout "" :stderr "" :timed-out False :started False :start-error detail))


(defk start-refusal [error-number path]
  {:pre [(: error-number int) (: path str)] :post [(: % str)] :tags {:context "process" :role "judgment"}}
  "To build the could-not-start reason text in the same form as the OSError text the real subprocess raises (\"[Errno 2] No such file or directory: '/x'\")."
  (str (OSError error-number (os.strerror error-number) path)))


(defk executable-file-answer [kind runnable]
  {:pre [(: kind PathKind) (: runnable bool)] :post [(: % bool)] :tags {:context "process" :role "judgment"}}
  "To decide the answer of ExecutableAt: True only when the kind of what it points to is file and execution is permitted (runnable). A dir, a missing path, and other
   kinds are False even if runnable (the execute bit of a dir means 'may enter', not a file that can be started)."
  (and (= kind PathKind.FILE) runnable))
