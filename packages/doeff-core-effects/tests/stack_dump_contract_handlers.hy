;;; Interpreter (composition root) for the watchdog contract tests that write the stacks of all threads -- runs the same contract Program, swapping only the watchdog handler and the clock.
;;;
;;;
;;;   faulthandler-stack-dump  real: faulthandler-stack-dump-handler (a C thread of faulthandler counts the deadline in real time and writes) and
;;;                            doeff-time's real-clock sync-time-handler
;;;   memory-stack-dump        fake: memory-stack-dump-handler (ledger only, no writing) and doeff-time's virtual-clock sim-time-handler
;;;
;;; The contract Program reads time with doeff-time's GetMonotonic and waits with Delay (only the clock handler differs per interpreter).
;;; The real one sets sys.stderr to a temporary file only while running (the test reads the text the watchdog writes via WrittenText and does not pollute test output). After the run it
;;; disarms the watchdog, restores sys.stderr and removes the temporary dir.
;;; The consumer is doeff_interpreter in conftest.py (names in deftest :interpreters -> INTERPRETERS).
(require doeff-hy.macros [defk defhandler defeffect <- val])
(import functools [partial])
(import faulthandler)
(import pathlib [Path])
(import sys)
(import tempfile)
(import datetime [datetime timezone])
(import doeff [Program with_handlers])
(import doeff_core_effects [state])
(import doeff_core_effects [faulthandler-stack-dump-handler])
(import doeff_core_effects [memory-stack-dump-handler])
(import doeff_time [sim-time-handler sync-time-handler])

(val FAULTHANDLER-STACK-DUMP "faulthandler-stack-dump")
(val MEMORY-STACK-DUMP "memory-stack-dump")
;; Start time of the virtual clock.
(val SIM-START (datetime 2026 10 2 :tzinfo timezone.utc))


(defeffect WrittenText
  "Read the text the real watchdog wrote to sys.stderr (only the real interpreter answers)."
  {:fields [] :answer str :tags {:context "stack-dump-test" :role "foundation"}})


(defhandler written-log [#^ str path]
  "Answer WrittenText with the text of the temporary file that the real interpreter set as sys.stderr."
  ;; Why it stays an argument: path = a temporary file created each time the interpreter runs (not a setting read with Ask).
  (WrittenText []
    (resume (.read-text (Path path) :encoding "utf-8"))))


(defk under-faulthandler [program]
  {:pre [(: program Program)] :post [(: % "answer of the contract Program (type varies per Program)")]
   :tags {:context "stack-dump-test" :role "foundation"}}
  "Run program under the real handler and the real clock (see header note -- sets sys.stderr to a temporary file, and after finishing disarms the watchdog and restores it)."
  (val folder (tempfile.TemporaryDirectory))
  (val path (str (/ (Path folder.name) "stderr.log")))
  (val log (open path "w" :encoding "utf-8"))
  (val saved sys.stderr)
  (setattr sys "stderr" log)
  (try
    (<- answer (with_handlers [(state) (sync-time-handler) (written-log path) faulthandler-stack-dump-handler] program))
    answer
    (finally
      (faulthandler.cancel-dump-traceback-later)
      (setattr sys "stderr" saved)
      (.close log)
      (.cleanup folder))))


(defk under-memory [program]
  {:pre [(: program Program)] :post [(: % "answer of the contract Program (type varies per Program)")]
   :tags {:context "stack-dump-test" :role "foundation"}}
  "Run program under the memory handler and the virtual clock."
  (<- answer (with_handlers [(state) (sim-time-handler :start-time SIM-START) memory-stack-dump-handler] program))
  answer)


(val INTERPRETERS {FAULTHANDLER-STACK-DUMP under-faulthandler
                   MEMORY-STACK-DUMP under-memory})
