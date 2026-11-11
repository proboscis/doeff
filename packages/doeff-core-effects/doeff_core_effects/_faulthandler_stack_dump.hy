;;; Real handler for the watchdog that writes the stacks of all threads, faulthandler-stack-dump-handler: answers ArmStackDump, DisarmStackDump and
;;; ReadStackDumps with CPython's faulthandler watchdog and a ledger within one run (watched-stack-dump-handler of memory_stack_dump.hy with the watch faulthandler-watch).
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
(require doeff-hy.macros [defk val])
(val MODULE-TAGS {:context "stack-dump" :role "foundation"})
(import faulthandler)
(import sys)
(import time)
(import doeff_core_effects._memory_stack_dump [watched-stack-dump-handler])

;; The minimum remaining seconds faulthandler accepts (0 or less is not accepted).
(val MINIMUM-SECONDS 0.001)


(defk faulthandler-watch [due]
  {:pre [(: due (| float None))] :post [(: % None)] :tags {:context "stack-dump" :role "foundation"}}
  "Hand the deadline due to the faulthandler watchdog (None = cancel it -- see the header comment)."
  (if (is due None)
      (faulthandler.cancel-dump-traceback-later)
      (faulthandler.dump-traceback-later (max MINIMUM-SECONDS (- due (time.monotonic))) :repeat False :file sys.stderr :exit False))
  None)


(val faulthandler-stack-dump-handler (watched-stack-dump-handler faulthandler-watch))
