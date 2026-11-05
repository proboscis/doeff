;;; Interpreters (composition root) for the child-process contract tests - they run the Program of the same contract, swapping only the child-process handler.
;;;
;;;   subprocess             real: subprocess-handler (a real child process and os.environ) + os-file-handler (temporary dir)
;;;   offloaded-subprocess   the real one run on a thread: offloaded-subprocess-handler + os-file-handler (temporary dir)
;;;   scripted-process       fake: scripted-process-handler (no I/O - a script) + memory-file-handler + state
;;;
;;; The contract's world is prepared in the same shape for each interpreter:
;;;   * the root of the store (the answer of ContractRoot): real = a fresh temporary dir on every run (a real path) / fake = MEMORY-ROOT of the memory store
;;;   * the caller's environment (INHERITED): real = placed in os.environ only while running, and restored to the previous values afterwards / fake = the env of ProcessScript
;;;   * the modules that import resolves (ResolveModule): real = only while running, place a file module (PROBE-MODULE.py) and a package dir without __init__
;;;     (NAMESPACE-MODULE) in the root, add the root to the head of sys.path, and remove it afterwards / fake = write the same layout into the modules of ProcessScript
;;;   * the interpreter (ReadInterpreter): real = the process of this test / fake = the interpreter of ProcessScript (prefix = root, pid 1 = a live pid)
;;;   * commands: real = /bin/sh and sleep. fake = scripted sh and sleep; for each `sh -c <statement>` statement the contract uses, write "how a real sh would answer" in
;;;     SHELL-ANSWERS (the script builds the answer from the same fields of the request - env, cwd, stdin - that the real one builds its answer from)
;;; The user is doeff_interpreter in conftest.py (the names of deftest's :interpreters -> INTERPRETERS).
(require doeff-hy.macros [defk defhandler <- val var])
(import collections.abc [Callable])
(import dataclasses [dataclass])
(import importlib)
(import os)
(import sys)
(import tempfile)
(import doeff [EffectBase Program with_handlers])
(import doeff_core_effects [state])
(import doeff_core_effects [MemoryFiles])
(import doeff_core_effects [memory-file-handler])
(import doeff_core_effects [os-file-handler])
(import doeff_core_effects [subprocess-handler offloaded-subprocess-handler])
(import doeff_core_effects [EnvEntry ProcessOutcome RunProcess InterpreterFacts ModuleFound timed-out-outcome])
(import doeff_core_effects [ScriptedCommand ProcessScript scripted-process-handler])

(val SUBPROCESS "subprocess")
(val OFFLOADED-SUBPROCESS "offloaded-subprocess")
(val SCRIPTED-PROCESS "scripted-process")
(val MEMORY-ROOT "/contract-root")

;; The names of the modules that import resolves in the contract's world (ResolveModule - header note). No name overlaps with names in other tests.
(val PROBE-MODULE "doeff_contract_probe_module")
(val NAMESPACE-MODULE "doeff_contract_probe_namespace")
(val MISSING-MODULE "doeff_contract_probe_missing")
;; The caller's environment in the contract's world (inherited by the child, read by ReadEnvironment).
(val INHERITED #((EnvEntry :name "DOEFF_INHERITED" :value "inherited") (EnvEntry :name "DOEFF_SHADOWED" :value "parent")))

;; The statements the contract runs with `/bin/sh -c <statement>`.
(val OUT-ERR-EXIT "echo out; echo err >&2; exit 3")
(val OUT-ERR "echo out; echo err >&2")
(val KILLED "kill -9 $$")
(val CAT "cat")
(val PWD "pwd")
(val ENV-PROBE "printf '%s|%s|%s' \"$DOEFF_INHERITED\" \"$DOEFF_SHADOWED\" \"$DOEFF_ADDED\"")
;; Output with bytes that are not utf-8 (0xff) mixed in - checks the contract that the child's bytes come back as a reversible string.
(val NOT-UTF-8 "printf 'ok\\377\\n'")
(val NOT-UTF-8-BYTES b"ok\xff\n")
(val PROBED-NAMES #("DOEFF_INHERITED" "DOEFF_SHADOWED" "DOEFF_ADDED"))
;; Statements for the process group, the output of the streams, and whether a pid is alive. A background grandchild points its output to /dev/null (a grandchild holding the output
;; would delay EOF and make the end of the child wait until the deadline - the way of waiting follows the contract of communicate).
(val OWN-PID "echo $$")
(val LEFT-BEHIND "sleep 30 >/dev/null 2>&1 & echo $!")
(val LEFT-BEHIND-THEN-WAIT "sleep 30 >/dev/null 2>&1 & echo $!; sleep 30")
(val TWO-LINES "echo one; echo two")
(val FIRST-THEN-WAIT "echo first; sleep 30")
;; Output that greatly exceeds the pipe capacity (about 64 KB) (2,000,000 bytes = "a\n" one million times) - checks that the output of a child that returns right after being started flows to a file, and the child does not stall.
(val BIG-OUTPUT "yes a | head -c 2000000")
(val BIG-OUTPUT-TEXT (* "a\n" 1000000))
;; The pids of the script's world (answered by the scripted sh - not in the live table ALIVE, so they are dead) and the table of live pids (1 = init - alive in the real world too).
(val SCRIPTED-OWN-PID 4242)
(val SCRIPTED-LEFT-BEHIND 4343)
(val SCRIPTED-WAITED-BEHIND 4444)
(val ALIVE (frozenset [1]))


(defclass [(dataclass :frozen True)] ContractRoot [EffectBase]
  "An effect that asks for the root of the contract's store (the absolute path of an existing dir).")


(defhandler contract-root [root]
  ;; Why it stays an argument: the root is a value decided by the assembling side for each interpreter (the real one is a temporary dir created on every run).
  (ContractRoot []
    (resume root)))


(defk child-env [request]
  {:pre [(: request RunProcess)] :post [(: % dict)] :tags {:context "process-test" :role "judgment"}}
  "The child's environment for a request the script received (if env is None it inherits the caller's environment INHERITED - the same as a real child inheriting os.environ)."
  (val entries (match request.env
                  None INHERITED
                  given given))
  (dfor e entries e.name e.value))


(defk exit-3-with-output [request]
  {:pre [(: request RunProcess)] :post [(: % ProcessOutcome)] :tags {:context "process-test" :role "judgment"}}
  "The answer of a real sh for OUT-ERR-EXIT."
  (ProcessOutcome :exit-code 3 :stdout "out\n" :stderr "err\n"))


(defk exit-0-with-output [request]
  {:pre [(: request RunProcess)] :post [(: % ProcessOutcome)] :tags {:context "process-test" :role "judgment"}}
  "The answer of a real sh for OUT-ERR."
  (ProcessOutcome :exit-code 0 :stdout "out\n" :stderr "err\n"))


(defk killed-by-signal-9 [request]
  {:pre [(: request RunProcess)] :post [(: % ProcessOutcome)] :tags {:context "process-test" :role "judgment"}}
  "The answer of a real sh for KILLED (the returncode of a child ended by a signal stays negative)."
  (ProcessOutcome :exit-code -9 :stdout "" :stderr ""))


(defk echo-stdin [request]
  {:pre [(: request RunProcess)] :post [(: % ProcessOutcome)] :tags {:context "process-test" :role "judgment"}}
  "The answer of a real sh for CAT (outputs the child's stdin as is)."
  (ProcessOutcome :exit-code 0 :stdout (or request.stdin "") :stderr ""))


(defk print-cwd [request]
  {:pre [(: request RunProcess)] :post [(: % ProcessOutcome)] :tags {:context "process-test" :role "judgment"}}
  "The answer of a real sh for PWD (the child's working dir)."
  (match request.cwd
    None (raise (ValueError "the contract's PWD is run with cwd passed"))
    cwd (ProcessOutcome :exit-code 0 :stdout (+ cwd "\n") :stderr "")))


(defk print-probed-env [request]
  {:pre [(: request RunProcess)] :post [(: % ProcessOutcome)] :tags {:context "process-test" :role "judgment"}}
  "The answer of a real sh for ENV-PROBE (joins PROBED-NAMES of the child's environment with | - a name that is absent is empty)."
  (<- env dict (child-env request))
  (ProcessOutcome :exit-code 0 :stdout (.join "|" (gfor name PROBED-NAMES (.get env name ""))) :stderr ""))


(defk print-not-utf-8 [request]
  {:pre [(: request RunProcess)] :post [(: % ProcessOutcome)] :tags {:context "process-test" :role "judgment"}}
  "The answer of a real sh for NOT-UTF-8 (the child's bytes NOT-UTF-8-BYTES read as a string with utf-8 and surrogateescape) - so that the contract confirms the script also
   answers with the same reversible string as the real one."
  (ProcessOutcome :exit-code 0 :stdout (.decode NOT-UTF-8-BYTES "utf-8" "surrogateescape") :stderr ""))


(defk print-own-pid [request]
  {:pre [(: request RunProcess)] :post [(: % ProcessOutcome)] :tags {:context "process-test" :role "judgment"}}
  "The answer of a real sh for OWN-PID (the pid of the finished sh - a pid that is not alive in the script's world)."
  (ProcessOutcome :exit-code 0 :stdout (.format "{}\n" SCRIPTED-OWN-PID) :stderr ""))


(defk print-left-behind [request]
  {:pre [(: request RunProcess)] :post [(: % ProcessOutcome)] :tags {:context "process-test" :role "judgment"}}
  "The answer of a real sh for LEFT-BEHIND (the pid of the grandchild sent to the background - with a process-group it is stopped after the end)."
  (ProcessOutcome :exit-code 0 :stdout (.format "{}\n" SCRIPTED-LEFT-BEHIND) :stderr ""))


(defk outlive-the-limit [stdout request]
  {:pre [(: stdout str) (: request RunProcess)] :post [(: % ProcessOutcome)] :tags {:context "process-test" :role "judgment"}}
  "The answer of a statement that sleeps 30 seconds - the contract always runs it with a short timeout, so, like the real one, it becomes a timeout answer carrying stdout (timed-out-outcome)."
  (match request.timeout
    limit :if (and (is-not limit None) (< limit 30)) (do (<- timed-out ProcessOutcome (timed-out-outcome stdout ""))
                                                       timed-out)
    _ (raise (ValueError "the contract runs a statement that sleeps 30 seconds with a short timeout"))))


(defk print-and-outlive [request]
  {:pre [(: request RunProcess)] :post [(: % ProcessOutcome)] :tags {:context "process-test" :role "judgment"}}
  "The answer of a real sh for LEFT-BEHIND-THEN-WAIT (outputs the grandchild's pid, then keeps sleeping, and is stopped by the timeout)."
  (<- answer ProcessOutcome (outlive-the-limit (.format "{}\n" SCRIPTED-WAITED-BEHIND) request))
  answer)


(defk print-two-lines [request]
  {:pre [(: request RunProcess)] :post [(: % ProcessOutcome)] :tags {:context "process-test" :role "judgment"}}
  "The answer of a real sh for TWO-LINES."
  (ProcessOutcome :exit-code 0 :stdout "one\ntwo\n" :stderr ""))


(defk print-first-and-outlive [request]
  {:pre [(: request RunProcess)] :post [(: % ProcessOutcome)] :tags {:context "process-test" :role "judgment"}}
  "The answer of a real sh for FIRST-THEN-WAIT (outputs one line, then keeps sleeping, and is stopped by the timeout)."
  (<- answer ProcessOutcome (outlive-the-limit "first\n" request))
  answer)


(defk print-big-output [request]
  {:pre [(: request RunProcess)] :post [(: % ProcessOutcome)] :tags {:context "process-test" :role "judgment"}}
  "The answer of a real sh for BIG-OUTPUT (2,000,000 bytes of output)."
  (ProcessOutcome :exit-code 0 :stdout BIG-OUTPUT-TEXT :stderr ""))


;; What the scripted sh answers for each `-c <statement>` statement (how a real sh would answer).
(val SHELL-ANSWERS {OUT-ERR-EXIT exit-3-with-output
                    OUT-ERR exit-0-with-output
                    KILLED killed-by-signal-9
                    CAT echo-stdin
                    PWD print-cwd
                    ENV-PROBE print-probed-env
                    NOT-UTF-8 print-not-utf-8
                    OWN-PID print-own-pid
                    LEFT-BEHIND print-left-behind
                    LEFT-BEHIND-THEN-WAIT print-and-outlive
                    TWO-LINES print-two-lines
                    FIRST-THEN-WAIT print-first-and-outlive
                    BIG-OUTPUT print-big-output})


(defk shell-script [commands request]
  {:pre [(: commands tuple) (: request RunProcess)] :post [(: % ProcessOutcome)] :tags {:context "process-test" :role "judgment"}}
  "The scripted command sh: answers `sh -c <statement>` with the statement's answer in SHELL-ANSWERS (a statement not in the contract's world is a mistake of the test)."
  (match (tuple (cut request.argv 1 None))
    #("-c" script) :if (in script SHELL-ANSWERS) (do (<- answer ProcessOutcome ((get SHELL-ANSWERS script) request))
                                                     answer)
    other (raise (ValueError (.format "sh arguments not in the contract's world: {!r}" other)))))


(defk sleep-script [commands request]
  {:pre [(: commands tuple) (: request RunProcess)] :post [(: % ProcessOutcome)] :tags {:context "process-test" :role "judgment"}}
  "The scripted command sleep: if the seconds exceed the timeout, a timeout answer like the real one (timed-out-outcome); otherwise it ends with 0."
  (val seconds (float (get request.argv 1)))
  (match request.timeout
    limit :if (and (is-not limit None) (< limit seconds)) (do (<- timed-out ProcessOutcome (timed-out-outcome "" ""))
                                                timed-out)
    _ (ProcessOutcome :exit-code 0 :stdout "" :stderr "")))


(val SCRIPT (ProcessScript :commands #((ScriptedCommand :name "sh" :run shell-script) (ScriptedCommand :name "sleep" :run sleep-script))
                           :env INHERITED
                           :work-root (+ MEMORY-ROOT "/jobs")
                           :alive ALIVE
                           :interpreter (InterpreterFacts :prefix MEMORY-ROOT :pid 1)
                           :modules #((ModuleFound :name PROBE-MODULE :origin (+ MEMORY-ROOT "/" PROBE-MODULE ".py") :search-locations #())
                                      (ModuleFound :name NAMESPACE-MODULE :origin None
                                                   :search-locations #((+ MEMORY-ROOT "/" NAMESPACE-MODULE))))))


(defk restore-environment [saved]
  {:pre [(: saved dict)] :post [(: % None)] :tags {:context "process-test" :role "foundation"}}
  "Restore the names in os.environ to the values in saved (None = was absent)."
  (for [#(name value) (.items saved)]
    (match value
      None (.pop os.environ name None)
      _ (.update os.environ {name value})))
  None)


(defk under-real-process [process-handler program]
  {:pre [(: process-handler Callable) (: program Program)] :post [(: % "the answer of the contract's Program (the type varies per Program)")]
   :tags {:context "process-test" :role "foundation"}}
  "Run the program under process-handler, the responder for real child processes. The root is a fresh temporary dir (a real path); the caller's environment INHERITED is placed in os.environ only
   while running, and restored to the previous values afterwards. The modules that import resolves are placed in the root only while running, and the root is added to the head of sys.path (header note)."
  (var answer None)
  (with [directory (tempfile.TemporaryDirectory)]
    (val root (os.path.realpath directory))
    (val saved (dfor e INHERITED e.name (.get os.environ e.name)))
    (.update os.environ (dfor e INHERITED e.name e.value))
    (with [f (open (os.path.join root (+ PROBE-MODULE ".py")) "w" :encoding "utf-8")] (.write f ""))
    (os.mkdir (os.path.join root NAMESPACE-MODULE))
    (.insert sys.path 0 root)
    (importlib.invalidate-caches)
    (try
      (<- ran (with_handlers [(contract-root root) os-file-handler process-handler] program))
      (:= answer ran)
      (finally
        (.remove sys.path root)
        (<- (restore-environment saved)))))
  answer)


(defk under-subprocess [program]
  {:pre [(: program Program)] :post [(: % "the answer of the contract's Program (the type varies per Program)")]
   :tags {:context "process-test" :role "foundation"}}
  "Run the program under the real subprocess-handler (under-real-process)."
  (<- answer (under-real-process subprocess-handler program))
  answer)


(defk under-offloaded-subprocess [program]
  {:pre [(: program Program)] :post [(: % "the answer of the contract's Program (the type varies per Program)")]
   :tags {:context "process-test" :role "foundation"}}
  "Run the program under offloaded-subprocess-handler, which runs the real one on a thread (under-real-process - conftest stacks the scheduled)."
  (<- answer (under-real-process offloaded-subprocess-handler program))
  answer)


(defk under-scripted-process [program]
  {:pre [(: program Program)] :post [(: % "the answer of the contract's Program (the type varies per Program)")]
   :tags {:context "process-test" :role "foundation"}}
  "Run the program under the fake scripted-process-handler. From the outside in: state, the root's responder, the memory store (only the root exists), the script."
  (<- answer (with_handlers [(state) (contract-root MEMORY-ROOT) (memory-file-handler (MemoryFiles :dirs #(MEMORY-ROOT)))
                             (scripted-process-handler SCRIPT)]
               program))
  answer)


(val INTERPRETERS {SUBPROCESS under-subprocess
                   OFFLOADED-SUBPROCESS under-offloaded-subprocess
                   SCRIPTED-PROCESS under-scripted-process})
