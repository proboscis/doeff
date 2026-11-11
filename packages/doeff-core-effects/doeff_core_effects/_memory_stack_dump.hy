;;; Ledger handlers for the watchdog that writes the stacks of all threads -- answer ArmStackDump, DisarmStackDump and ReadStackDumps with a ledger
;;; (a session value) inside one run.
;;;
;;;   watched-stack-dump-handler  factory: counts with the ledger and hands each new deadline (None = disarmed) to watch, a function of the deadline that returns
;;;                               a program answering None -- the real watchdog (faulthandler_stack_dump.hy, or a watchdog an outside repo keeps) is only the watch
;;;   memory-stack-dump-handler   the ledger only, without writing (watch does nothing)
;;;
;;; Why: in simulations and tests we want to check that it writes "only when stuck, once per stall" with the same contract as the real one (faulthandler_stack_dump.hy), but
;;; we do not want to touch the real-time watchdog thread or the process log. The only difference is whether it writes; how deadlines are counted (stack-dumps-at in stack_dump_effects.hy)
;;; is the same as the real one, because both are built from watched-stack-dump-handler. A state handler is required outside.
(require doeff-hy.macros [defhandler defk <- var val])
(val MODULE-TAGS {:context "stack-dump" :role "foundation"})
(import collections.abc [Callable])
(import doeff_core_effects._stack_dump_effects [ArmStackDump DisarmStackDump ReadStackDumps StackDumpLedger stack-dumps-at])


(defhandler watched-stack-dump-handler [#^ Callable watch]
  "Answer the watchdog effects with a ledger and hand each new deadline (None = disarmed) to watch (see header note)."
  ;; Why it stays an argument: watch is the real watchdog of the assembling side (a thread or faulthandler that writes outside the run), not a setting read with Ask.
  (session var ledger (StackDumpLedger :deadline None :written #()))
  (ArmStackDump [at seconds]
    (<- settled StackDumpLedger (stack-dumps-at ledger at))
    (val deadline (+ at seconds))
    (<- (watch deadline))
    (:= ledger (StackDumpLedger :deadline deadline :written settled.written))
    (resume None))
  (DisarmStackDump [at]
    (<- settled StackDumpLedger (stack-dumps-at ledger at))
    (<- (watch None))
    (:= ledger (StackDumpLedger :deadline None :written settled.written))
    (resume None))
  (ReadStackDumps [at]
    (<- settled StackDumpLedger (stack-dumps-at ledger at))
    (:= ledger settled)
    (resume settled.written)))


(defk unwatched [due]
  {:pre [(: due (| float None))] :post [(: % None)] :tags {:context "stack-dump" :role "foundation"}}
  "The watch of the memory handler: it writes nothing."
  None)


(val memory-stack-dump-handler (watched-stack-dump-handler unwatched))
