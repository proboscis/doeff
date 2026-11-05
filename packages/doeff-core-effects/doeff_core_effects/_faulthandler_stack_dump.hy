;;; Real handler for the watchdog that writes the stacks of all threads, faulthandler-stack-dump-handler: answers ArmStackDump, DisarmStackDump and
;;; ReadStackDumps with CPython's faulthandler watchdog and a ledger within one run.
;;;
;;; Why: when the shared execution flow stalls, the call that caused the stall no longer remains in any stack once the stall ends. If the stacks of all threads are written
;;; to the process log (sys.stderr) during the stall, it can be identified later. faulthandler.dump_traceback_later counts the deadline in a C thread and
;;; that thread writes once the deadline passes, so it can write even while the stalled thread holds the GIL or the stop of all threads under free-threading (a Python-thread
;;; watchdog would stall too). There is no repeat, so each armed watchdog writes only once.
;;;
;;; Contract: there is one faulthandler watchdog per process (re-arming removes the previous one) -- do not install this handler twice in one process. The write target is
;;; sys.stderr at the time of arming. The deadline judgement (the answer of ReadStackDumps) is counted the same way as the memory handler (stack-dumps-at in stack_dump_effects.hy),
;;; using the caller's clock (the same scale as time.monotonic). The remaining seconds passed to faulthandler are at + seconds - the current time.monotonic (if already past,
;;; the minimum seconds -- write immediately). An outer state handler is required.
(require doeff-hy.macros [defhandler <- val var])
(val MODULE-TAGS {:context "stack-dump" :role "foundation"})
(import faulthandler)
(import sys)
(import time)
(import doeff_core_effects._stack_dump_effects [ArmStackDump DisarmStackDump ReadStackDumps StackDumpLedger stack-dumps-at])

;; The minimum remaining seconds faulthandler accepts (0 or less is not accepted).
(val MINIMUM-SECONDS 0.001)


(defhandler faulthandler-stack-dump-handler
  "Answers the watchdog effects by actually writing through the faulthandler watchdog (see the header comment)."
  (session var ledger (StackDumpLedger :deadline None :written #()))
  (ArmStackDump [at seconds]
    (<- settled StackDumpLedger (stack-dumps-at ledger at))
    (val deadline (+ at seconds))
    (faulthandler.dump-traceback-later (max MINIMUM-SECONDS (- deadline (time.monotonic))) :repeat False :file sys.stderr :exit False)
    (:= ledger (StackDumpLedger :deadline deadline :written settled.written))
    (resume None))
  (DisarmStackDump [at]
    (<- settled StackDumpLedger (stack-dumps-at ledger at))
    (faulthandler.cancel-dump-traceback-later)
    (:= ledger (StackDumpLedger :deadline None :written settled.written))
    (resume None))
  (ReadStackDumps [at]
    (<- settled StackDumpLedger (stack-dumps-at ledger at))
    (:= ledger settled)
    (resume settled.written)))
