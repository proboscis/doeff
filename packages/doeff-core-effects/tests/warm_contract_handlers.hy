;;; Interpreters for the warm-child contract tests (composition root) - run the same contract Program, swapping only the warm-child handler.
;;;
;;;   os-warm         real: os-warm-process-handler (unix socket and /proc) + fake warm children started inside the test (fake_warm_child.py - one separate process per
;;;                   socket) + subprocess-handler (the sleep between re-asks)
;;;   scripted-warm   fake: scripted-warm-process-handler (no I/O - WarmScript) + scripted-process-handler (scripted sleep) + memory storage
;;;
;;; The contract world is prepared in the same shape for every interpreter (the answer of WarmPaths):
;;;   * 4 sockets: accepting, refusing (refusal text REFUSAL), silent, missing
;;;   * 3 entries: EXIT-3 (ends immediately with exit code 3), SLEEPER (runs until stopped), VANISH (takes child A down with it and disappears without writing the exit file)
;;;     real = put the module files in a temp dir created for each run / fake = write the same endings in WarmScript's runs
;;; Used via doeff_interpreter in conftest.py (the names in deftest's :interpreters -> INTERPRETERS).
(require doeff-hy.macros [defk defhandler <- val var])
(require doeff-hy.record [defrecord])
(import dataclasses [dataclass])
(import os)
(import subprocess)
(import sys)
(import tempfile)
(import doeff [EffectBase Program with_handlers])
(import doeff_core_effects [state])
(import doeff_core_effects [MemoryFiles])
(import doeff_core_effects [memory-file-handler])
(import doeff_core_effects [subprocess-handler])
(import doeff_core_effects [ProcessOutcome RunProcess])
(import doeff_core_effects [ScriptedCommand ProcessScript scripted-process-handler])
(import doeff_core_effects [os-warm-process-handler])
(import doeff_core_effects [WarmScript WarmSocket WarmSocketAnswer WarmRun scripted-warm-process-handler])

(val OS-WARM "os-warm")
(val SCRIPTED-WARM "scripted-warm")
(val MEMORY-ROOT "/warm-root")
;; Refusal text of the refusing socket (the fake warm child and the script answer the same text).
(val REFUSAL "The warm child does not accept requests right now (refusal in the contract test)")
;; Names of the contract entries (real = modules in a temp dir / fake = WarmScript's runs).
(val EXIT-3 "warm_entry_exit3")
(val SLEEPER "warm_entry_sleep")
(val VANISH "warm_entry_vanish")
;; Contents of the real entries (the module child B runs inside the same process). VANISH takes the parent's child A down with SIGKILL - the exit file is never written.
(val ENTRY-SOURCES {EXIT-3 "import sys\nprint('out')\nsys.exit(3)\n"
                    SLEEPER "import time\ntime.sleep(30)\n"
                    VANISH "import os, signal\nos.kill(os.getppid(), signal.SIGKILL)\n"})
(val FAKE-WARM-CHILD (os.path.join (os.path.dirname (os.path.abspath __file__)) "fake_warm_child.py"))


(defrecord WarmPaths
  "The storage of the contract world (root = the dir holding the exit files and logs, and 4 sockets - see the header comment)."
  (#^ str root)
  (#^ str accepting)
  (#^ str refusing)
  (#^ str silent)
  (#^ str missing))


(defclass [(dataclass :frozen True)] WarmWorldPaths [EffectBase]
  "An effect that asks for the storage of the contract world (WarmPaths).")


(defhandler warm-world-paths [#^ WarmPaths paths]
  ;; Why it stays an argument: the storage is a value decided on the assembly side per interpreter (the real one creates a temp dir for each run).
  (WarmWorldPaths []
    (resume paths)))


(defk paths-under [root]
  {:pre [(: root str)] :post [(: % WarmPaths)] :tags {:context "warm-test" :role "judgment"}}
  "Decide the contract storage under root (the same name for real and fake)."
  (WarmPaths :root root
             :accepting (os.path.join root "accepting.sock")
             :refusing (os.path.join root "refusing.sock")
             :silent (os.path.join root "silent.sock")
             :missing (os.path.join root "missing.sock")))


(defk under-os-warm [program]
  {:pre [(: program Program)] :post [(: % "The answer of the contract Program (the type depends on the Program)")] :tags {:context "warm-test" :role "foundation"}}
  "Run program under the real os-warm-process-handler. The root is a fresh temp dir; put the entry modules there and start a fake warm child per socket
   (sockets marked missing are not started) - wait until each has read the one line announcing it started listening (do not wait on time). Stop them after the run."
  (var answer None)
  (with [directory (tempfile.TemporaryDirectory)]
    (val root (os.path.realpath directory))
    (for [#(entry source) (.items ENTRY-SOURCES)]
      (with [f (open (os.path.join root (+ entry ".py")) "w" :encoding "utf-8")] (.write f source)))
    (<- paths WarmPaths (paths-under root))
    (val starts #(#(paths.accepting "accept") #(paths.refusing (+ "refuse:" REFUSAL)) #(paths.silent "silent")))
    (val children (lfor #(socket-path mode) starts
                        (subprocess.Popen [sys.executable FAKE-WARM-CHILD socket-path mode] :stdout subprocess.PIPE :text True)))
    (try
      (for [child children]
        (val first (if (is child.stdout None) "" (.readline child.stdout)))
        (when (!= (.strip first) "ready")
          (raise (RuntimeError (.format "The fake warm child did not start: {!r}" first)))))
      ;; The real handler keeps a table of input-pipe write ends in the session, so state goes outside (in production, the worker's session-store).
      (<- ran (with_handlers [(state) (warm-world-paths paths) subprocess-handler os-warm-process-handler] program))
      (:= answer ran)
      (finally
        (for [child children]
          (.kill child)
          (.wait child)))))
  answer)


(defk scripted-sleep [commands request]
  {:pre [(: commands (of tuple ScriptedCommand ...)) (: request RunProcess)] :post [(: % ProcessOutcome)]
   :tags {:context "warm-test" :role "program"}}
  "Scripted sleep: the passage of time does not exist in the script's world, so it returns immediately."
  (ProcessOutcome :exit-code 0 :stdout "" :stderr ""))


(val SCRIPT (WarmScript :sockets #((WarmSocket :path (+ MEMORY-ROOT "/accepting.sock"))
                                   (WarmSocket :path (+ MEMORY-ROOT "/refusing.sock") :answer WarmSocketAnswer.REFUSES :refusal REFUSAL)
                                   (WarmSocket :path (+ MEMORY-ROOT "/silent.sock") :answer WarmSocketAnswer.SILENT))
                        ;; Ends the same way as the real entries: EXIT-3 runs twice then 3 / SLEEPER until stopped / VANISH disappears without writing the exit file.
                        :runs #((WarmRun :entry EXIT-3 :polls 2 :exit-code 3)
                                (WarmRun :entry SLEEPER :polls 1000000)
                                (WarmRun :entry VANISH :polls 1 :writes-exit False))))


(defk under-scripted-warm [program]
  {:pre [(: program Program)] :post [(: % "The answer of the contract Program (the type depends on the Program)")] :tags {:context "warm-test" :role "foundation"}}
  "Run program under the fake scripted-warm-process-handler. From the outside in: state, storage, memory storage, scripted sleep, warm-child script."
  (<- paths WarmPaths (paths-under MEMORY-ROOT))
  (<- answer (with_handlers [(state) (warm-world-paths paths) (memory-file-handler (MemoryFiles :dirs #(MEMORY-ROOT)))
                             (scripted-process-handler (ProcessScript :commands #((ScriptedCommand :name "sleep" :run scripted-sleep))))
                             (scripted-warm-process-handler SCRIPT)]
               program))
  answer)


(val INTERPRETERS {OS-WARM under-os-warm
                   SCRIPTED-WARM under-scripted-warm})
