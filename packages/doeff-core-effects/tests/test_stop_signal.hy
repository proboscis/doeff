(require doeff-hy.macros [defk defhandler deftest <-])

;;; Properties of only the real stop-signal handler (os-signal-stop-handler). The properties on which the real one and the fake (scripted-stop-handler) must give the same answers
;;; (None before the signal, the reason after it, the first reason is kept, a state handler is needed outside) are in the contract test test_stop_signal_contract.hy.

(import pytest)
(import doeff [run with_handlers])
(import doeff [UnhandledEffect])
(import doeff_core_effects [state])
(import doeff_core_effects [AwaitStop RaiseStop])
(import doeff_core_effects [CreatePromise SchedulerDeadlockError Spawn Wait scheduled])
(import doeff_core_effects [os-signal-stop-handler scripted-stop-handler])


(defk raise-under-os []
  {:pre [] :post [(: % None)] :tags {:context "stop-signal-test" :role "program"}}
  "A Program that raises RaiseStop under the real handler."
  (<- (RaiseStop "scenario"))
  None)


(deftest test-counterexample-raise-stop-is-not-answered-by-the-os-handler
  ;; RaiseStop is only on the scripted side - the real signal handler does not answer it (a scripted stop cannot be triggered in production).
  (with [(pytest.raises UnhandledEffect :match "RaiseStop")]
    (run (with_handlers [(state) os-signal-stop-handler] (raise-under-os)))))


;; --- Counterexample for AwaitStop -----------------------------------------------------------------------------
;; With a handler that does not wake sleeping waits on a stop signal (it only remembers the reason on RaiseStop), a task sleeping in AwaitStop never wakes after the stop,
;; and the scheduler halts in a wait deadlock. scripted-stop-handler completes the promise on RaiseStop precisely to avoid producing this shape.

(defhandler forgetful-stop-handler
  "Counterexample: remembers the reason on RaiseStop but does not wake waits sleeping in AwaitStop (the wait sleeps on a different promise)."
  (session var raised None)
  (RaiseStop [reason]
    (:= raised reason)
    (resume None))
  (AwaitStop []
    (when (is-not raised None)
      (return (resume raised)))
    (<- never (CreatePromise))
    (<- reason str (Wait never.future))
    (resume reason)))


(defk await-stop-once []
  {:pre [] :post [(: % str)] :tags {:context "stop-signal-test" :role "program"}}
  "Body of a task that waits for AwaitStop once."
  (<- reason str (AwaitStop))
  reason)


(defk stop-while-waiting []
  {:pre [] :post [(: % str)] :tags {:context "stop-signal-test" :role "program"}}
  "Raise a stop while a task is sleeping in AwaitStop, then wait for that task's answer."
  (<- waiter (Spawn (await-stop-once)))
  (<- step (Spawn (raise-under-os)))
  (<- (Wait step))
  (<- reason str (Wait waiter))
  reason)


(deftest test-counterexample-a-stop-that-does-not-wake-the-wait-stalls-the-run
  (with [(pytest.raises SchedulerDeadlockError)]
    (run (scheduled (with_handlers [(state) forgetful-stop-handler] (stop-while-waiting)))))
  ;; With a correct fake, the same Program wakes up with the stop reason.
  (assert (= (run (scheduled (with_handlers [(state) scripted-stop-handler] (stop-while-waiting)))) "scenario")))
