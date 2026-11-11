;;; Contract tests for the watchdog that writes the stacks of all threads -- the real one (faulthandler-stack-dump-handler) and the fake (memory-stack-dump-handler),
;;; both answering the same effects (ArmStackDump, DisarmStackDump, ReadStackDumps), pass the same deftest. The interpreter assembly is in
;;; stack_dump_contract_handlers.hy.
;;;
;;; Properties checked:
;;;   * a watchdog that is not re-armed writes exactly once at the deadline (reading again after the deadline does not add more -- no repeat)
;;;   * a watchdog that is re-armed before the deadline and then disarmed does not write
;;; Properties specific to the real one (it really writes all thread stacks to sys.stderr) are in test_faulthandler_stack_dump.hy.
;;; The real one waits in real time (about 0.5 s each). The re-arm before the deadline uses a margin that does not cross the deadline even if load on the machine stretches the wait (deadline 1 s, wait 0.1 s).
(require doeff-hy.macros [deftest <- val])
(import doeff_core_effects [ArmStackDump DisarmStackDump ReadStackDumps])
(import doeff_time [Delay GetMonotonic])
(import stack_dump_contract_handlers [WatchedDeadlines])

;; Deadline seconds of a watchdog that is not re-armed, and wait seconds that cross the deadline.
(val SHORT-WATCH 0.1)
(val PAST-DEADLINE 0.3)
;; Deadline seconds of a watchdog that is re-armed, and wait seconds before the deadline.
(val LONG-WATCH 1.0)
(val BEFORE-DEADLINE 0.1)


(deftest test-a-watch-not-rearmed-writes-once-at-its-deadline
  {:interpreters ["faulthandler-stack-dump" "memory-stack-dump" "watched-stack-dump"]}
  (<- start float (GetMonotonic))
  (<- (ArmStackDump :at start :seconds SHORT-WATCH))
  (<- (Delay PAST-DEADLINE))
  (<- later float (GetMonotonic))
  (<- written tuple (ReadStackDumps :at later))
  (assert (= written #((+ start SHORT-WATCH))) (.format "time written by the watchdog past its deadline is {!r} (armed at {!r})" written start)))


(deftest test-a-watch-writes-only-once
  {:interpreters ["faulthandler-stack-dump" "memory-stack-dump" "watched-stack-dump"]}
  (<- start float (GetMonotonic))
  (<- (ArmStackDump :at start :seconds SHORT-WATCH))
  (<- (Delay PAST-DEADLINE))
  (<- first-read float (GetMonotonic))
  (<- first tuple (ReadStackDumps :at first-read))
  (<- (Delay PAST-DEADLINE))
  (<- second-read float (GetMonotonic))
  (<- second tuple (ReadStackDumps :at second-read))
  (assert (= (len first) 1) (.format "time written by the watchdog past its deadline is {!r}" first))
  (assert (= second first) (.format "the watchdog wrote again after it had written: {!r} -> {!r}" first second)))


(deftest test-a-watch-rearmed-then-disarmed-before-its-deadline-writes-nothing
  {:interpreters ["faulthandler-stack-dump" "memory-stack-dump" "watched-stack-dump"]}
  (<- start float (GetMonotonic))
  (<- (ArmStackDump :at start :seconds LONG-WATCH))
  (<- (Delay BEFORE-DEADLINE))
  (<- again float (GetMonotonic))
  (<- (ArmStackDump :at again :seconds LONG-WATCH))
  (<- (Delay BEFORE-DEADLINE))
  (<- off float (GetMonotonic))
  (<- (DisarmStackDump :at off))
  (<- (Delay PAST-DEADLINE))
  (<- later float (GetMonotonic))
  (<- written tuple (ReadStackDumps :at later))
  (assert (= written #()) (.format "a watchdog re-armed before the deadline and then disarmed wrote: {!r}" written)))


(deftest test-the-watch-is-handed-each-deadline-and-none-on-disarm
  {:interpreters ["watched-stack-dump"]}
  (<- start float (GetMonotonic))
  (<- (ArmStackDump :at start :seconds LONG-WATCH))
  (<- (Delay BEFORE-DEADLINE))
  (<- again float (GetMonotonic))
  (<- (ArmStackDump :at again :seconds LONG-WATCH))
  (<- (DisarmStackDump :at again))
  (<- handed tuple (WatchedDeadlines))
  (assert (= handed #((+ start LONG-WATCH) (+ again LONG-WATCH) None)) (.format "deadlines handed to the watch are {!r}" handed)))
