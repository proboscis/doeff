;;; Watchdog effects that write the stacks of all threads to the log -- when a shared flow of execution (the scheduler, the event loop of the Await bridge) stalls, they leave evidence naming the blocking call while the stall is still in progress (#2734). A foundation vocabulary with no business terms.
;;;
;;;   ArmStackDump     Arm a watchdog that writes the stacks of all threads of the process exactly once unless it is re-armed by at + seconds. Re-arming discards the previous watchdog
;;;                    (a process has one watchdog). at = the instant of arming (seconds of the monotonic clock -- the same scale as time.monotonic). Answer = None.
;;;   DisarmStackDump  Disarm the armed watchdog (at = the instant of disarming). Answer = None.
;;;   ReadStackDumps   Read the instants (deadline instants) at which watchdogs wrote, oldest first (at = the instant of reading -- a watchdog whose deadline has passed by that instant also counts as written).
;;;
;;; Why the instant rides on the effect: the real and the memory handlers decide whether the deadline has passed on the same scale (the caller's clock), with the same counting, stack-dumps-at.
;;; The memory handler has no clock (the caller under a virtual clock passes the instant).
;;;
;;; Handlers: faulthandler-stack-dump-handler (faulthandler_stack_dump.hy -- the real one. The deadline is counted by the C thread of CPython's faulthandler, and when the deadline passes that thread writes to sys.stderr -- it can write even while the stalled thread holds the GIL or the stop of all threads under free-threading)
;;; and memory-stack-dump-handler (memory_stack_dump.hy -- only counts, never writes).
;;; The two pass the same contract test (tests/test_stack_dump_contract.hy).
(require doeff-hy.macros [defeffect defk])
(require doeff-hy.record [defrecord])
(import dataclasses [dataclass])  ; named by the defrecord expansion


(defeffect ArmStackDump
  "Arm a watchdog that writes the stacks of all threads once unless re-armed by at + seconds (see the header note)."
  {:fields [(: at float) (: seconds float)]
   :answer None
   :tags {:context "stack-dump" :role "foundation"}})


(defeffect DisarmStackDump
  "Disarm the armed watchdog (see the header note)."
  {:fields [(: at float)]
   :answer None
   :tags {:context "stack-dump" :role "foundation"}})


(defeffect ReadStackDumps
  "Read the instants at which watchdogs wrote, oldest first (see the header note)."
  {:fields [(: at float)]
   :answer (get tuple #(float ...))
   :tags {:context "stack-dump" :role "foundation"}})


(defrecord StackDumpLedger
  "The watchdog ledger: deadline = the deadline instant of the armed watchdog (None if none is armed), written = the instants at which it wrote (oldest first)."
  {:tags {:context "stack-dump" :role "foundation"}}
  (#^ (| float None) deadline)
  (#^ (get tuple #(float ...)) written))


(defk stack-dumps-at [ledger at]
  {:pre [(: ledger StackDumpLedger) (: at float)] :post [(: % StackDumpLedger)] :tags {:context "stack-dump" :role "foundation"}}
  "Produces the ledger as of instant at -- a watchdog whose deadline has passed has written once at its deadline instant and been removed (the counting shared by the real and the memory handlers)."
  (if (and (is-not ledger.deadline None) (> at ledger.deadline))
      (StackDumpLedger :deadline None :written (+ ledger.written #(ledger.deadline)))
      ledger))
