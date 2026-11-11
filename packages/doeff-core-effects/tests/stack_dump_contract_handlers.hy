;;; Interpreter (composition root) for the watchdog contract tests that write the stacks of all threads -- runs the same contract Program, swapping only the watchdog handler and the clock.
;;;
;;;
;;;   faulthandler-stack-dump  real: faulthandler-stack-dump-handler (a C thread of faulthandler counts the deadline in real time and writes) and
;;;                            doeff-time's real-clock sync-time-handler
;;;   memory-stack-dump        fake: memory-stack-dump-handler (ledger only, no writing) and doeff-time's virtual-clock sim-time-handler
;;;   watched-stack-dump       watched-stack-dump-handler with a watch that keeps the deadlines handed to it (read with WatchedDeadlines) and the virtual clock
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
(import doeff_core_effects [watched-stack-dump-handler])
(import doeff_time [sim-time-handler sync-time-handler])

(val FAULTHANDLER-STACK-DUMP "faulthandler-stack-dump")
(val MEMORY-STACK-DUMP "memory-stack-dump")
(val WATCHED-STACK-DUMP "watched-stack-dump")
;; Start time of the virtual clock.
(val SIM-START (datetime 2026 10 2 :tzinfo timezone.utc))


(defeffect WrittenText
  "Read the text the real watchdog wrote to sys.stderr (only the real interpreter answers)."
  {:fields [] :answer str :tags {:context "stack-dump-test" :role "foundation"}})


(defeffect WatchedDeadlines
  "Read the deadlines (None = disarmed) handed to the watch, oldest first (only the watched interpreter answers)."
  {:fields [] :answer tuple :tags {:context "stack-dump-test" :role "foundation"}})


(defhandler watched-log [#^ list handed]
  "Answer WatchedDeadlines with the deadlines the watch of the watched interpreter kept."
  ;; Why it stays an argument: handed = the list the watch of the same run appends to (made each time the interpreter runs, not a setting read with Ask).
  (WatchedDeadlines []
    (resume (tuple handed))))


(defk kept-watch [handed due]
  {:pre [(: handed list) (: due (| float None))] :post [(: % None)] :tags {:context "stack-dump-test" :role "foundation"}}
  "The watch of the watched interpreter: keep the deadline handed to it."
  (.append handed due)
  None)


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


(defk under-watched [program]
  {:pre [(: program Program)] :post [(: % "answer of the contract Program (type varies per Program)")]
   :tags {:context "stack-dump-test" :role "foundation"}}
  "Run program under watched-stack-dump-handler with a keeping watch and the virtual clock."
  (val handed [])
  (<- answer (with_handlers [(state) (sim-time-handler :start-time SIM-START) (watched-log handed)
                             (watched-stack-dump-handler (partial kept-watch handed))] program))
  answer)


(val INTERPRETERS {FAULTHANDLER-STACK-DUMP under-faulthandler
                   MEMORY-STACK-DUMP under-memory
                   WATCHED-STACK-DUMP under-watched})
