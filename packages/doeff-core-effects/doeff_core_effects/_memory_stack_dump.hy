;;; In-memory handler memory-stack-dump-handler for the watchdog that writes the stacks of all threads -- answers ArmStackDump, DisarmStackDump and
;;; ReadStackDumps with a ledger (a session value) inside one run.
;;;
;;; Why: in simulations and tests we want to check that it writes "only when stuck, once per stall" with the same contract as the real one (faulthandler_stack_dump.hy), but
;;; we do not want to touch the real-time watchdog thread or the process log. The only difference is whether it writes; how deadlines are counted (stack-dumps-at in stack_dump_effects.hy)
;;; is the same as the real one. A state handler is required outside.
(require doeff-hy.macros [defhandler <- var val])
(val MODULE-TAGS {:context "stack-dump" :role "foundation"})
(import doeff_core_effects._stack_dump_effects [ArmStackDump DisarmStackDump ReadStackDumps StackDumpLedger stack-dumps-at])


(defhandler memory-stack-dump-handler
  "Answer the watchdog effects with only a ledger, without writing (see header note)."
  (session var ledger (StackDumpLedger :deadline None :written #()))
  (ArmStackDump [at seconds]
    (<- settled StackDumpLedger (stack-dumps-at ledger at))
    (:= ledger (StackDumpLedger :deadline (+ at seconds) :written settled.written))
    (resume None))
  (DisarmStackDump [at]
    (<- settled StackDumpLedger (stack-dumps-at ledger at))
    (:= ledger (StackDumpLedger :deadline None :written settled.written))
    (resume None))
  (ReadStackDumps [at]
    (<- settled StackDumpLedger (stack-dumps-at ledger at))
    (:= ledger settled)
    (resume settled.written)))
