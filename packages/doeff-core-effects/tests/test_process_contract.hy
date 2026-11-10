;;; Child-process contract tests -- the real one (subprocess-handler) and the fake (scripted-process-handler), which answer the same effects (RunProcess, ExecutableAt, ReadEnvironment, WorkingDirectory),
;;; pass the same deftests. The interpreter assembly and the contract world are in
;;; process_contract_handlers.hy. The real one uses short real commands (/bin/sh -c and sleep -- about 1 second in total); the fake gets the same answers from a script.
;;;
;;;   * returncode is not rounded (a child ended by a signal is negative); stdout / stderr are separate, as text
;;;   * stdin reaches the child
;;;   * bytes that are not utf-8 travel as a reversible string (utf-8 with surrogateescape) -- both the child's output and stdin give the original bytes when re-encoded
;;;   * child environment: env None = inherit the caller's environment; REPLACE (default) = exactly the given set; EXTEND = inherit and add (for the same name, the added one wins);
;;;     env-drop removes names from what EXTEND inherits. ReadEnvironment returns only the names that exist, in the order of names
;;;   * the child runs in the cwd dir
;;;   * a child that cannot be started is an answer (exit-code 127, started False, the OSError message) -- cwd is rejected first, the command after
;;;   * a timeout is an answer (exit-code 124, timed-out True); within the time it is a normal answer
;;;   * stdout then stderr are appended to the end of output-path (the answer also carries the output). An output-path that cannot be appended raises OSError
;;;   * ExecutableAt separates startable commands from unstartable ones; a dir (even with the same name as a command) and a missing path are False; WorkingDirectory is the absolute path of an existing dir
;;;   * ReadMachineName answers a non-empty machine name
;;;   * ProcessAlive separates a live pid (init = 1) from the pid of a finished child; a pid of 0 or less is not alive
;;;   * process-group stops a grandchild sent to the background after finishing within the time, and on timeout stops the group together with the grandchild. The output-path of stream-output
;;;     also holds what the child stopped by the timeout had emitted
;;;   * children that return as soon as they are started: StartProcess returns a pid without waiting, and PollProcess can ask until it finishes (a child that answered its finish is
;;;     forgotten); StopProcess stops a running child with SIGTERM (-15); stopping the group stops the grandchild too; a shape that cannot be started is ProcessNotStarted (output
;;;     file, cwd, command); a pid that was not started is left alone; output flows to a file, so the child does not stall even beyond the pipe capacity
;;;   * offloaded-subprocess-handler, which runs the real one on a thread, also returns the same answers as the real one (passes the same deftests)
;;; Properties only the real one has (WorkingDirectory is its own process's working dir) and properties only the fake has (a working dir per job, running a script from a script,
;;; a script receiving env None as None) are in test_process_file_effects.hy.
(require doeff-hy.macros [defk deftest <- val var])
(import contextlib)
(import unittest [mock])
(import os)
(import signal)
(import sys)
(import doeff_core_effects [MakeDirectory PathKind PathStat ReadText StatPath WriteText])
(import doeff_core_effects [EnvEntry EnvMode ExecutableAt ProcessAlive ProcessOutcome ReadEnvironment RunProcess WorkingDirectory StartProcess PollProcess StopProcess ProcessStarted ProcessNotStarted ProcessRunning ProcessExited ProcessNotChild SignalProcess ProcessSignal ProcessSignalled ProcessSignalRefused WriteProcessInput ProcessInputWritten ReadInterpreter ReadMachineName ResolveModule InterpreterFacts ModuleFound ModuleNotFound environment-mapping])
(import process_contract_handlers [BIG-OUTPUT BIG-OUTPUT-TEXT CAT ContractRoot ENV-PROBE FIRST-THEN-WAIT KILLED LEFT-BEHIND LEFT-BEHIND-THEN-WAIT NOT-UTF-8
                                   NOT-UTF-8-BYTES OUT-ERR OUT-ERR-EXIT OWN-PID PWD TWO-LINES
                                   PROBE-MODULE NAMESPACE-MODULE MISSING-MODULE])

(val MISSING-COMMAND "/nonexistent/doeff-command")
(val GIVEN-ENV #((EnvEntry :name "DOEFF_SHADOWED" :value "added") (EnvEntry :name "DOEFF_ADDED" :value "added")))


(defk shell [script #** options]
  {:pre [(: script str) (: options dict)] :post [(: % ProcessOutcome)] :tags {:context "process-test" :role "program"}}
  "Run /bin/sh -c script once (options are the fields of RunProcess)."
  (<- outcome ProcessOutcome (RunProcess :argv #("/bin/sh" "-c" script) #** options))
  outcome)


(defk refused [detail]
  {:pre [(: detail str)] :post [(: % ProcessOutcome)] :tags {:context "process-test" :role "judgment"}}
  "The expected answer for a child that cannot be started (written alone on the contract side -- does not borrow the answerer's functions)."
  (ProcessOutcome :exit-code 127 :stdout "" :stderr "" :started False :start-error detail))


(deftest test-the-exit-code-and-the-output-are-kept-raw
  {:interpreters ["subprocess" "offloaded-subprocess" "scripted-process"]}
  (<- failed ProcessOutcome (shell OUT-ERR-EXIT))
  (<- killed ProcessOutcome (shell KILLED))
  (assert (= failed (ProcessOutcome :exit-code 3 :stdout "out\n" :stderr "err\n")) failed)
  (assert (= killed (ProcessOutcome :exit-code -9 :stdout "" :stderr "")) (.format "answer of a child ended by signal 9 {}" killed)))


(deftest test-stdin-is-given-to-the-child
  {:interpreters ["subprocess" "offloaded-subprocess" "scripted-process"]}
  (<- echoed ProcessOutcome (shell CAT :stdin "input\nline 2"))
  (assert (= echoed (ProcessOutcome :exit-code 0 :stdout "input\nline 2" :stderr "")) echoed))


(deftest test-bytes-that-are-not-utf-8-survive-the-round-trip
  {:interpreters ["subprocess" "offloaded-subprocess" "scripted-process"]}
  ;; Non-utf-8 bytes the child emitted come back as a reversible string (broken bytes become surrogate characters) -- re-encoding gives the original bytes (so a user that passes a git diff to
  ;; patch-id can keep the bytes).
  (<- printed ProcessOutcome (shell NOT-UTF-8))
  (assert (= printed.exit-code 0) printed)
  (assert (= (.encode printed.stdout "utf-8" "surrogateescape") NOT-UTF-8-BYTES) (.format "bytes of the child output {!r}" printed.stdout))
  ;; A reversible string placed in stdin reaches the child as the original bytes and comes back unchanged (valid utf-8 and NUL are mixed in too).
  (val given (.decode b"\xfe\x00\xff\xe3\x81\x82\n" "utf-8" "surrogateescape"))
  (<- echoed ProcessOutcome (shell CAT :stdin given))
  (assert (= echoed (ProcessOutcome :exit-code 0 :stdout given :stderr "")) echoed))


(deftest test-the-child-environment-follows-the-env-mode
  {:interpreters ["subprocess" "offloaded-subprocess" "scripted-process"]}
  (<- inherited ProcessOutcome (shell ENV-PROBE))
  (<- replaced ProcessOutcome (shell ENV-PROBE :env GIVEN-ENV))
  (<- extended ProcessOutcome (shell ENV-PROBE :env GIVEN-ENV :env-mode EnvMode.EXTEND))
  (<- dropped ProcessOutcome (shell ENV-PROBE :env GIVEN-ENV :env-mode EnvMode.EXTEND :env-drop #("DOEFF_INH*" "DOEFF_ADDED")))
  (assert (= inherited.stdout "inherited|parent|") (.format "child environment with env None {}" inherited))
  (assert (= replaced.stdout "|added|added") (.format "child environment with the default (REPLACE) {}" replaced))
  (assert (= extended.stdout "inherited|added|added") (.format "child environment with EXTEND {}" extended))
  ;; env-drop removes only inherited names (it does not remove added names).
  (assert (= dropped.stdout "|added|added") (.format "child environment with EXTEND and env-drop {}" dropped)))


(deftest test-the-own-environment-is-read-by-name
  {:interpreters ["subprocess" "offloaded-subprocess" "scripted-process"]}
  (<- seen tuple (ReadEnvironment #("DOEFF_MISSING" "DOEFF_SHADOWED" "DOEFF_INHERITED")))
  (assert (= seen #((EnvEntry :name "DOEFF_SHADOWED" :value "parent") (EnvEntry :name "DOEFF_INHERITED" :value "inherited"))) seen))



(deftest test-the-own-environment-is-read-by-prefix
  ;; prefixes (#2472) also pick up names that start with a prefix -- they follow the names part in name order, and a name already in names is not emitted twice.
  {:interpreters ["subprocess" "offloaded-subprocess" "scripted-process"]}
  (<- seen tuple (ReadEnvironment #("DOEFF_SHADOWED") :prefixes #("DOEFF_INHERIT" "DOEFF_SHADOW")))
  (assert (= seen #((EnvEntry :name "DOEFF_SHADOWED" :value "parent") (EnvEntry :name "DOEFF_INHERITED" :value "inherited"))) seen)
  (<- none tuple (ReadEnvironment #() :prefixes #("DOEFF_NO_SUCH_")))
  (assert (= none #()) none))


(deftest test-the-whole-own-environment-is-a-mapping
  ;; environment-mapping (#3012) turns the answer of ReadEnvironment with prefix "" into a name -> value mapping -- the one place for callers that pass the whole environment as the base of a child process env,
  ;; so they do not reference os.environ. All the caller's environment names (including the 2 of the contract world) are included.
  {:interpreters ["subprocess" "offloaded-subprocess" "scripted-process"]}
  (<- mapping (get dict #(str str)) (environment-mapping))
  (assert (= #((.get mapping "DOEFF_INHERITED") (.get mapping "DOEFF_SHADOWED")) #("inherited" "parent")) mapping))

(deftest test-the-child-runs-in-the-given-directory
  {:interpreters ["subprocess" "offloaded-subprocess" "scripted-process"]}
  (<- root str (ContractRoot))
  (<- shown ProcessOutcome (shell PWD :cwd root))
  (assert (= shown (ProcessOutcome :exit-code 0 :stdout (+ root "\n") :stderr "")) shown))


(deftest test-a-child-that-cannot-start-is-an-answer
  {:interpreters ["subprocess" "offloaded-subprocess" "scripted-process"]}
  (<- root str (ContractRoot))
  (<- (WriteText (+ root "/file") "not a dir"))
  (<- no-command ProcessOutcome (RunProcess :argv #(MISSING-COMMAND)))
  (<- no-cwd ProcessOutcome (shell PWD :cwd (+ root "/none")))
  (<- file-cwd ProcessOutcome (shell PWD :cwd (+ root "/file")))
  (<- neither ProcessOutcome (RunProcess :argv #(MISSING-COMMAND) :cwd (+ root "/none")))
  (<- want-no-command ProcessOutcome (refused (.format "[Errno 2] No such file or directory: {!r}" MISSING-COMMAND)))
  (<- want-no-cwd ProcessOutcome (refused (.format "[Errno 2] No such file or directory: {!r}" (+ root "/none"))))
  (<- want-file-cwd ProcessOutcome (refused (.format "[Errno 20] Not a directory: {!r}" (+ root "/file"))))
  (assert (= no-command want-no-command) (.format "answer for a missing command {}" no-command))
  (assert (= no-cwd want-no-cwd) (.format "answer for a missing cwd {}" no-cwd))
  (assert (= file-cwd want-file-cwd) (.format "answer when a file is the cwd {}" file-cwd))
  ;; With neither command nor cwd, it is rejected for the cwd (a real child moves to the cwd before exec).
  (assert (= neither want-no-cwd) (.format "answer with neither command nor cwd {}" neither)))


(deftest test-a-timeout-is-an-answer
  {:interpreters ["subprocess" "offloaded-subprocess" "scripted-process"]}
  (<- slow ProcessOutcome (RunProcess :argv #("sleep" "5") :timeout 0.2))
  (<- quick ProcessOutcome (RunProcess :argv #("sleep" "0") :timeout 5.0))
  (assert (= slow (ProcessOutcome :exit-code 124 :stdout "" :stderr "" :timed-out True)) (.format "answer on timeout {}" slow))
  (assert (= quick (ProcessOutcome :exit-code 0 :stdout "" :stderr "")) (.format "answer when finished within the time {}" quick)))


(deftest test-the-output-is-appended-to-the-output-path
  {:interpreters ["subprocess" "offloaded-subprocess" "scripted-process"]}
  (<- root str (ContractRoot))
  (val log (+ root "/out.log"))
  (<- first ProcessOutcome (shell OUT-ERR :output-path log))
  (<- second ProcessOutcome (shell OUT-ERR-EXIT :output-path log))
  (<- logged str (ReadText log))
  (assert (= first (ProcessOutcome :exit-code 0 :stdout "out\n" :stderr "err\n")) first)
  (assert (= second.exit-code 3) second)
  (assert (= logged "out\nerr\nout\nerr\n") (.format "output appended to output-path {!r}" logged)))


(deftest test-an-output-path-that-cannot-be-written-raises
  {:interpreters ["subprocess" "offloaded-subprocess" "scripted-process"]}
  (<- root str (ContractRoot))
  (val log (+ root "/none/out.log"))
  (var raised None)
  (try
    (<- (shell OUT-ERR :output-path log))
    (except [error OSError]
      (:= raised (str error))))
  (assert (= raised (.format "[Errno 2] No such file or directory: {!r}" log))
          (.format "message of the exception raised for an output-path that cannot be appended {!r}" raised)))


(deftest test-executables-are-told-apart
  {:interpreters ["subprocess" "offloaded-subprocess" "scripted-process"]}
  (<- runnable bool (ExecutableAt :path "/bin/sh"))
  (<- missing bool (ExecutableAt :path MISSING-COMMAND))
  (assert runnable "/bin/sh cannot be started")
  (assert (not missing) (.format "{} can be started" MISSING-COMMAND)))


(deftest test-a-directory-or-a-missing-path-is-not-executable
  {:interpreters ["subprocess" "offloaded-subprocess" "scripted-process"]}
  ;; A dir is not a startable file even with the execute bit -- a dir with the same name as the command, and the root of the search path, are both False. A missing path is also False.
  (<- root str (ContractRoot))
  (val named-dir (+ root "/sh"))
  (<- (MakeDirectory named-dir))
  (<- root-seen bool (ExecutableAt :path root))
  (<- named-dir-seen bool (ExecutableAt :path named-dir))
  (<- missing-seen bool (ExecutableAt :path (+ root "/none")))
  (assert (= #(root-seen named-dir-seen missing-seen) #(False False False))
          (.format "answers of ExecutableAt (root of the search path, dir with the command's name, missing path) {}" #(root-seen named-dir-seen missing-seen))))


(deftest test-the-own-interpreter-is-read
  {:interpreters ["subprocess" "offloaded-subprocess" "scripted-process"]}
  ;; Facts of the interpreter: prefix is the absolute path of an existing dir; pid is a live process.
  (<- facts InterpreterFacts (ReadInterpreter))
  (<- seen PathStat (StatPath facts.prefix))
  (<- alive bool (ProcessAlive facts.pid))
  (assert (os.path.isabs facts.prefix) facts)
  (assert (= seen.kind PathKind.DIRECTORY) (.format "kind of prefix {}: {}" facts.prefix seen.kind))
  (assert alive (.format "pid {} is not alive" facts.pid)))


(deftest test-the-own-machine-name-is-read
  {:interpreters ["subprocess" "offloaded-subprocess" "scripted-process"]}
  ;; Machine name: the answer is a non-empty string. That the real one equals socket.gethostname and that the script answers the machine-name of ProcessScript
  ;; are properties of only one side (test_process_file_effects.hy).
  (<- name str (ReadMachineName))
  (assert (and (isinstance name str) (> (len name) 0)) (.format "answer for the machine name {!r}" name)))


(deftest test-a-module-name-is-resolved-to-its-place-without-importing-it
  {:interpreters ["subprocess" "offloaded-subprocess" "scripted-process"]}
  ;; Search locations that import resolves (#2347): a file module is the file's path; a package without __init__ has no file, only the dirs to search; an unresolvable name is by value.
  (<- root str (ContractRoot))
  (<- probe (| ModuleFound ModuleNotFound) (ResolveModule PROBE-MODULE))
  (<- namespace (| ModuleFound ModuleNotFound) (ResolveModule NAMESPACE-MODULE))
  (<- missing (| ModuleFound ModuleNotFound) (ResolveModule MISSING-MODULE))
  (assert (= probe (ModuleFound :name PROBE-MODULE :origin (+ root "/" PROBE-MODULE ".py") :search-locations #())) probe)
  (assert (= namespace (ModuleFound :name NAMESPACE-MODULE :origin None :search-locations #((+ root "/" NAMESPACE-MODULE)))) namespace)
  (assert (= missing (ModuleNotFound :name MISSING-MODULE)) missing))


(deftest test-the-own-working-directory-is-an-existing-directory
  {:interpreters ["subprocess" "offloaded-subprocess" "scripted-process"]}
  (<- here str (WorkingDirectory))
  (<- seen PathStat (StatPath here))
  (assert (os.path.isabs here) here)
  (assert (= seen.kind PathKind.DIRECTORY) (.format "kind of working dir {}: {}" here seen.kind)))


(defk gone-soon [pid]
  {:pre [(: pid int)] :post [(: % bool)] :tags {:context "process-test" :role "program"}}
  "Whether the process at pid died within 2 seconds -- a stopped grandchild looks alive only until init reaps it (signal 0 reaches a zombie too), so re-check every
   0.05 seconds."
  (var alive True)
  (var tries 0)
  (while (and alive (< tries 40))
    (<- seen bool (ProcessAlive pid))
    (:= alive seen)
    (when alive
      (<- (RunProcess :argv #("sleep" "0.05")))
      (:= tries (+ tries 1))))
  (not alive))


(deftest test-whether-a-process-is-alive-is-told
  {:interpreters ["subprocess" "offloaded-subprocess" "scripted-process"]}
  (<- init bool (ProcessAlive 1))
  (<- finished ProcessOutcome (shell OWN-PID))
  (<- gone bool (ProcessAlive (int (.strip finished.stdout))))
  (<- zero bool (ProcessAlive 0))
  (<- negative bool (ProcessAlive -1))
  (assert init "answer says init (pid 1) is not alive")
  (assert (not gone) (.format "answer says the finished sh (pid {}) is alive" (.strip finished.stdout)))
  (assert (= #(zero negative) #(False False)) (.format "answers for a pid of 0 or less {}" #(zero negative))))


(deftest test-a-group-stops-what-the-child-left-behind
  {:interpreters ["subprocess" "offloaded-subprocess" "scripted-process"]}
  (<- finished ProcessOutcome (shell LEFT-BEHIND :process-group True))
  (assert (= finished.exit-code 0) finished)
  (<- stopped bool (gone-soon (int (.strip finished.stdout))))
  (assert stopped (.format "the grandchild sent to the background (pid {}) is still alive after the group cleanup" (.strip finished.stdout))))


(deftest test-a-timeout-stops-the-whole-group
  {:interpreters ["subprocess" "offloaded-subprocess" "scripted-process"]}
  (<- slow ProcessOutcome (shell LEFT-BEHIND-THEN-WAIT :process-group True :timeout 0.5 :stop-grace 2.0))
  (assert (and slow.timed-out (= slow.exit-code 124)) (.format "answer on timeout {}" slow))
  (<- stopped bool (gone-soon (int (.strip slow.stdout))))
  (assert stopped (.format "the grandchild of the group stopped by the timeout (pid {}) is alive" (.strip slow.stdout))))


(deftest test-the-streamed-output-path-keeps-what-a-stopped-child-printed
  {:interpreters ["subprocess" "offloaded-subprocess" "scripted-process"]}
  (<- root str (ContractRoot))
  (val log (+ root "/streamed.log"))
  (<- whole ProcessOutcome (shell TWO-LINES :output-path log :stream-output True))
  (<- stopped ProcessOutcome (shell FIRST-THEN-WAIT :output-path log :stream-output True :process-group True :timeout 0.5
                                    :stop-grace 2.0))
  (<- logged str (ReadText log))
  (assert (= whole (ProcessOutcome :exit-code 0 :stdout "one\ntwo\n" :stderr "")) whole)
  (assert (= stopped (ProcessOutcome :exit-code 124 :stdout "first\n" :stderr "" :timed-out True)) (.format "answer on timeout {}" stopped))
  (assert (= logged "one\ntwo\nfirst\n") (.format "output-path written while streaming {!r}" logged)))


;; ---- Children that return as soon as they are started (StartProcess, PollProcess, StopProcess) ---------------------------------------------

(defk exited-soon [pid]
  {:pre [(: pid int)] :post [(: % (| ProcessExited ProcessRunning ProcessNotChild))] :tags {:context "process-test" :role "program"}}
  "Ask the started child every 0.05 seconds until it finishes, within 5 seconds -- a real child just after starting may still be running. Answer = the answer of the last PollProcess."
  (var seen (ProcessRunning :pid pid))
  (var tries 0)
  (while (and (isinstance seen ProcessRunning) (< tries 100))
    (<- polled (PollProcess pid))
    (:= seen polled)
    (when (isinstance seen ProcessRunning)
      (<- (RunProcess :argv #("sleep" "0.05")))
      (:= tries (+ tries 1))))
  seen)


(defk first-line-of [path]
  {:pre [(: path str)] :post [(: % str)] :tags {:context "process-test" :role "program"}}
  "Re-read the file every 0.05 seconds within 2 seconds until its first line is written, and return that line -- the output of a child that returns as soon as it is started may not exist yet when it returns."
  (var text "")
  (var tries 0)
  (while (and (not-in "\n" text) (< tries 40))
    (<- read str (ReadText path))
    (:= text read)
    (when (not-in "\n" text)
      (<- (RunProcess :argv #("sleep" "0.05")))
      (:= tries (+ tries 1))))
  (get (.split text "\n") 0))


(deftest test-a-started-child-is-polled-until-it-exits
  {:interpreters ["subprocess" "offloaded-subprocess" "scripted-process"]}
  (<- root str (ContractRoot))
  (<- started (StartProcess :argv #("/bin/sh" "-c" OUT-ERR-EXIT) :stdout-path (+ root "/out") :stderr-path (+ root "/err")))
  (assert (isinstance started ProcessStarted) started)
  (<- exited (exited-soon started.pid))
  (<- out str (ReadText (+ root "/out")))
  (<- err str (ReadText (+ root "/err")))
  (<- again (PollProcess started.pid))
  (assert (= exited (ProcessExited :pid started.pid :exit-code 3)) exited)
  (assert (= #(out err) #("out\n" "err\n")) (.format "output files of the child {!r}" #(out err)))
  ;; A child that answered its finish is reaped and forgotten -- asking again does not find a started child.
  (assert (= again (ProcessNotChild :pid started.pid)) again))


(deftest test-a-running-child-is-stopped
  {:interpreters ["subprocess" "offloaded-subprocess" "scripted-process"]}
  (<- started (StartProcess :argv #("sleep" "30")))
  (assert (isinstance started ProcessStarted) started)
  (<- running (PollProcess started.pid))
  (<- stopped (StopProcess :pid started.pid :stop-grace 2.0))
  (<- again (StopProcess :pid started.pid :stop-grace 2.0))
  (assert (= running (ProcessRunning :pid started.pid)) running)
  (assert (= stopped (ProcessExited :pid started.pid :exit-code -15)) (.format "answer of a child stopped by SIGTERM {}" stopped))
  (assert (= again (ProcessNotChild :pid started.pid)) again))



;; ---- A child holding the standard-input pipe, and a child that stops what remains of a finished group (hold-stdin and reap-group of StartProcess -- #2471) ----------------------
;; Consumer = the worker's child process in doeff-cluster (the shim stops the job's group on EOF of standard input, and reaps grandchildren of a finished job's group).

(deftest test-a-child-holding-stdin-keeps-running-until-it-is-collected
  ;; The scripted world has no standard-input pipe, so only the real answerer. A hold-stdin child keeps running without reading EOF; a child without one (DEVNULL) ends at once on EOF.
  {:interpreters ["subprocess" "offloaded-subprocess"]}
  (<- held (StartProcess :argv #("/bin/sh" "-c" "cat >/dev/null; exit 5") :hold-stdin True))
  (<- free (StartProcess :argv #("/bin/sh" "-c" "cat >/dev/null; exit 5")))
  (<- (RunProcess :argv #("sleep" "0.3")))
  (<- held-seen (PollProcess held.pid))
  (<- free-seen (exited-soon free.pid))
  (assert (= held-seen (ProcessRunning :pid held.pid)) (.format "the child holding standard input read EOF: {}" held-seen))
  (assert (= free-seen (ProcessExited :pid free.pid :exit-code 5)) free-seen)
  (<- stopped (StopProcess :pid held.pid :stop-grace 2.0))
  (assert (isinstance stopped ProcessExited) stopped))


(deftest test-collecting-a-reap-group-child-stops-what-it-left-behind
  {:interpreters ["subprocess" "offloaded-subprocess" "scripted-process"]}
  (<- root str (ContractRoot))
  (val out (+ root "/pid"))
  (<- started (StartProcess :argv #("/bin/sh" "-c" LEFT-BEHIND) :stdout-path out :process-group True :reap-group True))
  (assert (isinstance started ProcessStarted) started)
  (<- grandchild str (first-line-of out))
  (<- exited (exited-soon started.pid))
  (assert (= exited (ProcessExited :pid started.pid :exit-code 0)) exited)
  (<- gone bool (gone-soon (int grandchild)))
  (assert gone (.format "the grandchild sent to the background (pid {}) is still alive after the reap-group child was reaped" grandchild)))

;; ---- Send a signal without waiting (SignalProcess -- #2461) ------------------------------------------------------------------------------
;; Consumer = the worker's per-stage stopping in doeff-cluster (send TERM every beat, KILL if it does not stop; the finish is confirmed with PollProcess).

(deftest test-a-signalled-child-ends-and-is-collected-by-poll
  {:interpreters ["subprocess" "offloaded-subprocess" "scripted-process"]}
  (for [#(sent code) [#(ProcessSignal.TERM -15) #(ProcessSignal.KILL -9)]]
    (<- started (StartProcess :argv #("sleep" "30")))
    (assert (isinstance started ProcessStarted) started)
    (<- answer (SignalProcess :pid started.pid :signal sent))
    (assert (= answer (ProcessSignalled :pid started.pid :delivered True)) #(sent answer))
    ;; It only sends and does not wait -- PollProcess answers the finish and reaps.
    (<- exited (exited-soon started.pid))
    (assert (= exited (ProcessExited :pid started.pid :exit-code code)) #(sent exited))
    (<- again (SignalProcess :pid started.pid :signal sent))
    (assert (= again (ProcessNotChild :pid started.pid)) again)))


(deftest test-a-signal-the-os-refuses-is-answered-as-refused-and-the-child-keeps-running
  ;; A machine where the OS refuses the send (EPERM -- on 2026-11-01 on node-c, AppArmor denied signals between processes inside a container). A refused
  ;; signal is not answered as "sent" -- the answer is ProcessSignalRefused, and the child keeps running. Counterexample = the form (before the fix) that swallows PermissionError and
  ;; answers delivered True turns the assertions below red. Both a child started as a group (os.killpg) and a non-group child (the Popen handle -> os.kill).
  {:interpreters ["subprocess" "offloaded-subprocess"]}
  (for [group [True False]]
    (<- started (StartProcess :argv #("sleep" "30") :process-group group))
    (assert (isinstance started ProcessStarted) started)
    (with [_ (mock.patch.object os "killpg" :side-effect (PermissionError 1 "Operation not permitted"))
           _ (mock.patch.object os "kill" :side-effect (PermissionError 1 "Operation not permitted"))]
      (<- answer (SignalProcess :pid started.pid :signal ProcessSignal.KILL)))
    (assert (= answer (ProcessSignalRefused :pid started.pid)) #(group answer))
    (<- polled (PollProcess started.pid))
    (assert (= polled (ProcessRunning :pid started.pid)) #(group polled))
    (<- (StopProcess :pid started.pid :stop-grace 2.0))))

(deftest test-a-stop-whose-kill-the-os-refuses-answers-refused-without-waiting-and-keeps-the-child
  ;; StopProcess on a machine where the OS refuses the send (same as the check above): neither SIGTERM nor SIGKILL arrives. After the refused SIGKILL it does not wait for the child to finish
  ;; (waiting would mean the stop call never returns), and the answer is ProcessSignalRefused -- the child stays in the table running, and PollProcess answers ProcessRunning.
  ;; Counterexample = the form (before the fix) that waits for the child after a refused SIGKILL never returns and is red; the form that sends to a non-group child with Popen terminate is red with PermissionError.
  {:interpreters ["subprocess" "offloaded-subprocess"]}
  (for [group [True False]]
    (<- started (StartProcess :argv #("sleep" "30") :process-group group))
    (assert (isinstance started ProcessStarted) started)
    (with [_ (mock.patch.object os "killpg" :side-effect (PermissionError 1 "Operation not permitted"))
           _ (mock.patch.object os "kill" :side-effect (PermissionError 1 "Operation not permitted"))]
      (<- answer (StopProcess :pid started.pid :stop-grace 0.2)))
    (assert (= answer (ProcessSignalRefused :pid started.pid)) #(group answer))
    (<- polled (PollProcess started.pid))
    (assert (= polled (ProcessRunning :pid started.pid)) #(group polled))
    (<- stopped (StopProcess :pid started.pid :stop-grace 2.0))
    (assert (isinstance stopped ProcessExited) #(group stopped))))



(deftest test-an-ended-child-is-not-signalled-and-its-end-is-kept-for-poll
  {:interpreters ["subprocess" "offloaded-subprocess" "scripted-process"]}
  (<- started (StartProcess :argv #("/bin/sh" "-c" OUT-ERR-EXIT)))  ; a script that prints and ends with 3
  (assert (isinstance started ProcessStarted) started)
  ;; Wait for the real child to finish (asking would reap it, so wait without asking).
  (<- (RunProcess :argv #("sleep" "0.3")))
  (<- answer (SignalProcess :pid started.pid :signal ProcessSignal.TERM))
  (<- exited (PollProcess started.pid))
  (assert (= answer (ProcessSignalled :pid started.pid :delivered False)) answer)
  (assert (= exited (ProcessExited :pid started.pid :exit-code 3)) exited))


(deftest test-a-pid-that-is-not-a-started-child-is-not-signalled
  {:interpreters ["subprocess" "offloaded-subprocess" "scripted-process"]}
  ;; Do not send to a pid that was not started (init = 1) -- do not stop other people's processes.
  (<- answer (SignalProcess :pid 1 :signal ProcessSignal.KILL))
  (<- init bool (ProcessAlive 1))
  (assert (= answer (ProcessNotChild :pid 1)) answer)
  (assert init "answer says init (pid 1) is not alive"))

;; ---- Write to the held standard-input pipe (WriteProcessInput -- #3672) -------------------------------------------------------------------
;; Consumer = the worker in doeff-cluster (sends the job's retreat notice line to the shim's standard input -- the shim relays it to the job's notice pipe).

(deftest test-a-line-written-to-a-held-stdin-reaches-the-child
  ;; The scripted world has no standard-input pipe, so only the real answerer. A hold-stdin child can read the written line on the spot (does not wait for the child to end).
  {:interpreters ["subprocess" "offloaded-subprocess"]}
  (<- root str (ContractRoot))
  (val out (+ root "/line"))
  (<- started (StartProcess :argv #("/bin/sh" "-c" "read line; echo \"got $line\"; cat >/dev/null") :stdout-path out :hold-stdin True))
  (assert (isinstance started ProcessStarted) started)
  (<- written (WriteProcessInput :pid started.pid :text "retired\n"))
  (<- line str (first-line-of out))
  (assert (= written (ProcessInputWritten :pid started.pid :delivered True)) written)
  (assert (= line "got retired") line)
  (<- stopped (StopProcess :pid started.pid :stop-grace 2.0))
  (assert (isinstance stopped ProcessExited) stopped))


(deftest test-input-is-not-written-without-a-held-stdin-or-after-the-child-ended
  {:interpreters ["subprocess" "offloaded-subprocess" "scripted-process"]}
  ;; Do not write to a child that does not hold the pipe (DEVNULL).
  (<- free (StartProcess :argv #("sleep" "30")))
  (<- unheld (WriteProcessInput :pid free.pid :text "retired\n"))
  (assert (= unheld (ProcessInputWritten :pid free.pid :delivered False)) unheld)
  (<- (StopProcess :pid free.pid :stop-grace 2.0))
  ;; Do not write to a finished child; leave its finish for PollProcess.
  (<- ended (StartProcess :argv #("/bin/sh" "-c" OUT-ERR-EXIT) :hold-stdin True))
  (<- (RunProcess :argv #("sleep" "0.3")))
  (<- late (WriteProcessInput :pid ended.pid :text "retired\n"))
  (<- exited (PollProcess ended.pid))
  (assert (= late (ProcessInputWritten :pid ended.pid :delivered False)) late)
  (assert (= exited (ProcessExited :pid ended.pid :exit-code 3)) exited)
  ;; Do not write to a pid that was not started (init = 1).
  (<- stranger (WriteProcessInput :pid 1 :text "retired\n"))
  (assert (= stranger (ProcessNotChild :pid 1)) stranger))


(deftest test-stopping-a-group-stops-what-the-child-left-behind
  {:interpreters ["subprocess" "offloaded-subprocess" "scripted-process"]}
  (<- root str (ContractRoot))
  (val out (+ root "/pid"))
  (<- started (StartProcess :argv #("/bin/sh" "-c" LEFT-BEHIND-THEN-WAIT) :stdout-path out :process-group True))
  (assert (isinstance started ProcessStarted) started)
  (<- grandchild str (first-line-of out))
  (<- stopped (StopProcess :pid started.pid :stop-grace 2.0))
  (assert (= stopped (ProcessExited :pid started.pid :exit-code -15)) (.format "answer of the child whose group was stopped {}" stopped))
  (<- gone bool (gone-soon (int grandchild)))
  (assert gone (.format "the grandchild the child sent to the background (pid {}) is still alive after the group was stopped" grandchild)))


(deftest test-a-child-that-cannot-be-started-is-an-answer
  {:interpreters ["subprocess" "offloaded-subprocess" "scripted-process"]}
  (<- root str (ContractRoot))
  (<- no-command (StartProcess :argv #(MISSING-COMMAND)))
  (<- no-cwd (StartProcess :argv #("/bin/sh" "-c" PWD) :cwd (+ root "/none")))
  (<- no-output (StartProcess :argv #("/bin/sh" "-c" OUT-ERR) :stdout-path (+ root "/none/out")))
  (assert (= no-command (ProcessNotStarted :detail (.format "[Errno 2] No such file or directory: {!r}" MISSING-COMMAND))) no-command)
  (assert (= no-cwd (ProcessNotStarted :detail (.format "[Errno 2] No such file or directory: {!r}" (+ root "/none")))) no-cwd)
  (assert (= no-output (ProcessNotStarted :detail (.format "[Errno 2] No such file or directory: {!r}" (+ root "/none/out")))) no-output))


(deftest test-a-pid-that-is-not-a-started-child-is-not-touched
  {:interpreters ["subprocess" "offloaded-subprocess" "scripted-process"]}
  ;; Answer without sending a signal to a pid that was not started (init = 1) -- do not stop other people's processes.
  (<- polled (PollProcess 1))
  (<- stopped (StopProcess :pid 1 :stop-grace 0.1))
  (<- init bool (ProcessAlive 1))
  (assert (= #(polled stopped) #((ProcessNotChild :pid 1) (ProcessNotChild :pid 1))) #(polled stopped))
  (assert init "answer says init (pid 1) is not alive"))


(deftest test-a-large-output-does-not-stop-the-child
  {:interpreters ["subprocess" "offloaded-subprocess" "scripted-process"]}
  ;; Output flows to a file, so even far beyond the pipe capacity the child ends without stalling.
  (<- root str (ContractRoot))
  (<- started (StartProcess :argv #("/bin/sh" "-c" BIG-OUTPUT) :stdout-path (+ root "/big")))
  (assert (isinstance started ProcessStarted) started)
  (<- exited (exited-soon started.pid))
  (<- big str (ReadText (+ root "/big")))
  (assert (= exited (ProcessExited :pid started.pid :exit-code 0)) exited)
  (assert (= (len big) (len BIG-OUTPUT-TEXT)) (.format "length of the child output {}" (len big))))


;; ---- When the process that started it ends, its child ends too (lifetime of StartProcess) ------------------------------------
;; The starter is another process (lifeline_starter.py -- StartProcess a child with the real answerer), and the check stops it from outside or lets it end normally.
;; Even if the starter ends without running cleanup (StopProcess), neither the child nor a grandchild the child started in another session remains. Only a child whose lifetime is written OUTLIVES-STARTER
;; remains. The scripted world has no other process, so only the real answerer. So that no process remains even when a check is red, the leftovers are stopped before the assertions.

(val STARTER (os.path.join (os.path.dirname (os.path.abspath __file__)) "lifeline_starter.py"))
;; How the starter is started: a form started with python, and a form started from the Hy entry point (sys.executable becomes the hy launcher -- same as the doeff-cluster worker).
(val PYTHON-STARTER #(sys.executable STARTER))
(val HY-STARTER #((os.path.join (os.path.dirname sys.executable) "hy")
                  (os.path.join (os.path.dirname (os.path.abspath __file__)) "lifeline_starter_hy.hy")))


(defk killed-leftovers [pids]
  {:pre [(: pids tuple)] :post [(: % None)] :tags {:context "process-test" :role "program"}}
  "SIGKILL those of the pids the check found that are still alive -- so a red check does not leave the processes it started (they are not this check's children, so
   SignalProcess answers ProcessNotChild)."
  (for [pid pids]
    (with [(contextlib.suppress ProcessLookupError)]
      (os.kill pid signal.SIGKILL)))
  None)


(defk starter-and-child [launch mode root child-out]
  {:pre [(: launch tuple) (: mode str) (: root str) (: child-out str)] :post [(: % tuple)] :tags {:context "process-test" :role "program"}}
  "Start the starter with launch (how to start) and mode, and wait for and read the child pid the starter wrote. Answer = #(starter-pid child-pid)."
  (val out (+ root "/child-" mode))
  ;; first-line-of re-reads an existing file, so create it empty before the starter writes.
  (<- (WriteText out ""))
  (when (!= child-out "-") (<- (WriteText child-out "")))
  (<- started (StartProcess :argv #(#* launch mode out child-out)))
  (assert (isinstance started ProcessStarted) started)
  (<- child str (first-line-of out))
  #(started.pid (int child)))


(deftest test-a-child-ends-when-its-starter-is-killed
  {:interpreters ["subprocess" "offloaded-subprocess"]}
  (<- root str (ContractRoot))
  (<- pids tuple (starter-and-child PYTHON-STARTER "hold" root "-"))
  (val starter (get pids 0))
  (val child (get pids 1))
  (<- (SignalProcess :pid starter :signal ProcessSignal.KILL))
  (<- (exited-soon starter))
  (<- gone bool (gone-soon child))
  (<- (killed-leftovers #(child)))
  (assert gone (.format "the child (pid {}) is still alive after the starter was SIGKILLed" child)))


(deftest test-a-child-ends-when-its-starter-exits-without-stopping-it
  {:interpreters ["subprocess" "offloaded-subprocess"]}
  (<- root str (ContractRoot))
  (<- pids tuple (starter-and-child PYTHON-STARTER "exit" root "-"))
  (val starter (get pids 0))
  (val child (get pids 1))
  (<- exited (exited-soon starter))
  (<- gone bool (gone-soon child))
  (<- (killed-leftovers #(child)))
  (assert (= exited (ProcessExited :pid starter :exit-code 0)) exited)
  (assert gone (.format "the child (pid {}) is still alive after the starter ended without calling StopProcess" child)))


(deftest test-a-grandchild-in-another-session-ends-with-the-first-starter
  {:interpreters ["subprocess" "offloaded-subprocess"]}
  ;; The child (starter hold) further StartProcesses a grandchild in a new session. SIGKILLing the topmost starter leaves neither the child nor the grandchild.
  (<- root str (ContractRoot))
  (val grand-out (+ root "/grandchild"))
  (<- pids tuple (starter-and-child PYTHON-STARTER "hold" root grand-out))
  (val starter (get pids 0))
  (val child (get pids 1))
  (<- grandchild str (first-line-of grand-out))
  (<- (SignalProcess :pid starter :signal ProcessSignal.KILL))
  (<- (exited-soon starter))
  (<- child-gone bool (gone-soon child))
  (<- grandchild-gone bool (gone-soon (int grandchild)))
  (<- (killed-leftovers #(child (int grandchild))))
  (assert child-gone (.format "the child (pid {}) is still alive after the topmost starter was SIGKILLed" child))
  (assert grandchild-gone (.format "the grandchild in another session (pid {}) is still alive after the topmost starter was SIGKILLed" grandchild)))


(deftest test-a-child-that-outlives-its-starter-is-left-running
  {:interpreters ["subprocess" "offloaded-subprocess"]}
  (<- root str (ContractRoot))
  (<- pids tuple (starter-and-child PYTHON-STARTER "outlive" root "-"))
  (val starter (get pids 0))
  (val child (get pids 1))
  (<- exited (exited-soon starter))
  (<- gone bool (gone-soon child))
  (<- (killed-leftovers #(child)))
  (assert (= exited (ProcessExited :pid starter :exit-code 0)) exited)
  (assert (not gone) (.format "the OUTLIVES-STARTER child (pid {}) was stopped when the starter ended" child)))


(deftest test-a-child-ends-when-its-starter-started-from-hy-is-killed
  {:interpreters ["subprocess" "offloaded-subprocess"]}
  ;; The Hy entry point rewrites sys.executable to the hy launcher. Even if the starter is like that, the watcher wakes, and SIGKILLing the starter ends the child.
  (<- root str (ContractRoot))
  (<- pids tuple (starter-and-child HY-STARTER "hold" root "-"))
  (val starter (get pids 0))
  (val child (get pids 1))
  (<- (SignalProcess :pid starter :signal ProcessSignal.KILL))
  (<- (exited-soon starter))
  (<- gone bool (gone-soon child))
  (<- (killed-leftovers #(child)))
  (assert gone (.format "the child (pid {}) is still alive after the starter started from the Hy entry point was SIGKILLed" child)))
