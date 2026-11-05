;;; Properties of only the real answerer of the all-thread stack watchdog (faulthandler-stack-dump-handler): once the deadline passes, it
;;; really writes every thread's stack once to the sys.stderr that was current when it was armed, and writes nothing if disarmed before the deadline. Contract properties live in test_stack_dump_contract.hy.
;;; Waits in real time (about 0.5 s per test; the wait after the deadline is wide enough for the write to finish even if the watchdog thread wakes late under machine load).
(require doeff-hy.macros [deftest <- val])
(import doeff_core_effects [ArmStackDump DisarmStackDump])
(import doeff_time [Delay GetMonotonic])
(import stack_dump_contract_handlers [WrittenText])

;; Watchdog deadline in seconds, and seconds to wait after the deadline for the write to finish.
(val WATCH 0.1)
(val SETTLE 0.4)


(deftest test-a-passed-deadline-writes-every-thread-stack-once
  {:interpreters ["faulthandler-stack-dump"]}
  (<- start float (GetMonotonic))
  (<- (ArmStackDump :at start :seconds WATCH))
  (<- (Delay SETTLE))
  (<- text str (WrittenText))
  ;; The faulthandler watchdog writes one "Timeout (...)!" header line per dump, followed by a "(most recent call first)" stack for each thread.
  (assert (= (.count text "Timeout (") 1) text)
  (assert (in "most recent call first" text) text))


(deftest test-a-disarmed-watch-writes-nothing
  {:interpreters ["faulthandler-stack-dump"]}
  (<- start float (GetMonotonic))
  (<- (ArmStackDump :at start :seconds WATCH))
  (<- off float (GetMonotonic))
  (<- (DisarmStackDump :at off))
  (<- (Delay SETTLE))
  (<- text str (WrittenText))
  (assert (= text "") text))
