;;; Failure case for registering a wait on the stop-signal box (stop_signal_handlers.StopBox) (a miss found in unit 4 of #3871).
;;;
;;; AwaitStop checks that "there is no reason yet", then creates and registers a wait. If the signal arrives in between, the signal receiver sets only the reason with no wait present,
;;; and a wait registered afterwards is never satisfied by anyone (the worker's 60-second wait was not released by SIGTERM). Registration satisfies the wait
;;; on the spot if the reason is already set, and queues it otherwise -- leaving no gap for a miss between it and the signal receiver.
(require doeff-hy.macros [deftest val])
(import doeff_core_effects._stop_signal_handlers [StopBox])


(defclass Recorder []
  "Stand-in for a wait: remembers the reason received by complete."
  (defn __init__ [self]
    (setv self.reasons #()))
  (defn #^ None complete [self #^ str reason]
    (setv self.reasons (+ self.reasons #(reason)))
    None))


(deftest test-a-wait-parked-after-the-signal-is-completed-at-once
  (val box (StopBox))
  (.receive box 15 None)
  (val waiter (Recorder))
  (.park box waiter)
  (assert (= waiter.reasons #("signal 15")) waiter.reasons)
  (assert (= box.waiters #()) box.waiters))


(deftest test-a-wait-parked-before-the-signal-is-completed-by-the-signal-once
  (val box (StopBox))
  (val waiter (Recorder))
  (.park box waiter)
  (assert (= waiter.reasons #()) waiter.reasons)
  (.receive box 15 None)
  (assert (= waiter.reasons #("signal 15")) waiter.reasons))
