;;; Contract test for stop signals - the real one (os-signal-stop-handler) and the fake (scripted-stop-handler), which answer the same effect StopRequested,
;;; pass the same deftests. Interpreter assembly is in stop_contract_handlers.hy.
;;;
;;; Only the means of raising the signal (SendStop - the real one sends a real signal to the process, the fake uses RaiseStop) differs per interpreter, and the reader-side properties are checked by shared
;;; deftests:
;;;   * StopRequested is None while there is no signal
;;;   * after a signal, a reason string (same as the real box: "signal <number>")
;;;   * the first reason is kept (a second signal does not change it)
;;;   * a state handler is needed outside (without it there is no place for the session value, and it fails loudly)
;;; Properties of only the real one (does not answer RaiseStop) are in test_stop_signal.hy.
(require doeff-hy.macros [defk deftest <-])
(import signal)
(import pytest)
(import doeff [run with_handlers])
(import doeff [UnhandledEffect])
(import doeff_core_effects [AwaitStop StopBell StopRequested])
(import doeff_core_effects [Cancel Race Spawn Wait])
(import stop_contract_handlers [SendStop StopHandlerUnderTest signal-reason])


(defk ask-twice []
  {:pre [] :post [(: % tuple)] :tags {:context "stop-signal-test" :role "program"}}
  "Read StopRequested twice (a Program run against the counterexample with no state handler outside)."
  (<- first (| str None) (StopRequested))
  (<- second (| str None) (StopRequested))
  #(first second))


(deftest test-no-stop-before-any-signal
  {:interpreters ["os-signal" "scripted"]}
  (<- before (| str None) (StopRequested))
  (<- again (| str None) (StopRequested))
  (assert (is before None) (.format "StopRequested before the signal was {!r}" before))
  (assert (is again None) (.format "StopRequested read twice before the signal was {!r}" again)))


(deftest test-a-signal-is-read-as-its-reason
  {:interpreters ["os-signal" "scripted"]}
  (<- before (| str None) (StopRequested))
  (<- (SendStop signal.SIGTERM))
  (<- after (| str None) (StopRequested))
  (<- wanted str (signal-reason signal.SIGTERM))
  (assert (is before None) (.format "StopRequested before the signal was {!r}" before))
  (assert (= after wanted) (.format "StopRequested after SIGTERM was {!r} (expected {!r})" after wanted)))


(deftest test-the-first-reason-is-kept
  {:interpreters ["os-signal" "scripted"]}
  (<- (StopRequested))
  (<- (SendStop signal.SIGINT))
  (<- (SendStop signal.SIGTERM))
  (<- after (| str None) (StopRequested))
  (<- later (| str None) (StopRequested))
  (<- wanted str (signal-reason signal.SIGINT))
  (assert (= after wanted) (.format "StopRequested after SIGINT then SIGTERM was {!r} (keeps the first, {!r})" after wanted))
  (assert (= later wanted) (.format "StopRequested read once more was {!r}" later)))


(deftest test-a-state-handler-must-be-outside
  {:interpreters ["os-signal" "scripted"]}
  (<- handler (StopHandlerUnderTest))
  (with [(pytest.raises UnhandledEffect :match "stop-handler/")]
    (run (with_handlers [handler] (ask-twice)))))


;; --- AwaitStop (the entry that waits until a stop) -------------------------------------------------------------------
;; A loop that sleeps during quiet periods races its own wait (table change, deadline) against AwaitStop, and notices a stop every few seconds without asking StopRequested.
;; The real one and the fake have the same properties: answers immediately to a stop that already came, wakes on a stop that comes while waiting, and a cancelled wait does not hinder later stops.

(defk await-stop-once []
  {:pre [] :post [(: % str)] :tags {:context "stop-signal-test" :role "program"}}
  "Wait for AwaitStop once (the body of a task that is Spawned and handed to Race)."
  (<- reason str (AwaitStop))
  reason)


(defk quiet-then [value]
  {:pre [(: value str)] :post [(: % str)] :tags {:context "stop-signal-test" :role "program"}}
  "Another wait that finishes before the stop (stands in for the quiet loop's own wait)."
  value)


(deftest test-await-stop-answers-a-stop-already-requested
  {:interpreters ["os-signal" "scripted"]}
  ;; The real one installs its receiver when the stop-signal entry is first called, so ask once before sending the signal (same as the test above).
  (<- (StopRequested))
  (<- (SendStop signal.SIGTERM))
  (<- reason str (AwaitStop))
  (<- wanted str (signal-reason signal.SIGTERM))
  (assert (= reason wanted) (.format "AwaitStop after the stop was {!r} (expected {!r})" reason wanted)))


(deftest test-await-stop-wakes-when-the-stop-comes
  {:interpreters ["os-signal" "scripted"]}
  (<- (StopRequested))
  (<- waiter (Spawn (await-stop-once)))
  ;; Wait for one other task and run the scheduler, put the waiter to sleep in AwaitStop, then send the signal (to see that it wakes).
  (<- step (Spawn (quiet-then "parked")))
  (<- (Wait step))
  (<- (SendStop signal.SIGINT))
  (<- reason str (Wait waiter))
  (<- wanted str (signal-reason signal.SIGINT))
  (assert (= reason wanted) (.format "AwaitStop woken by a stop during the wait was {!r} (expected {!r})" reason wanted)))


(deftest test-a-cancelled-wait-does-not-keep-a-later-stop-away
  {:interpreters ["os-signal" "scripted"]}
  ;; One iteration of the quiet loop: its own wait finishes first and cancels the stop wait. A later stop reaches the next wait and StopRequested.
  (<- (StopRequested))
  (<- waiter (Spawn (await-stop-once)))
  (<- other (Spawn (quiet-then "changed")))
  (<- first (Race waiter other))
  (<- (Cancel waiter))
  (assert (= first "changed") first)
  (<- (SendStop signal.SIGTERM))
  (<- asked (| str None) (StopRequested))
  (<- later str (AwaitStop))
  (<- wanted str (signal-reason signal.SIGTERM))
  (assert (= asked wanted) (.format "StopRequested after the cancel was {!r}" asked))
  (assert (= later wanted) (.format "AwaitStop after the cancel was {!r}" later)))


(deftest test-the-stop-bell-is-one-future-a-lost-race-leaves-ringable
  {:interpreters ["os-signal" "scripted"]}
  ;; Several iterations of the quiet loop: each iteration races the same bell against its own wait, and its own wait finishes first (the bell is not a task, so there is nothing to cancel).
  ;; A later stop rings that same bell.
  (<- (StopRequested))
  (<- bell (StopBell))
  (<- other (Spawn (quiet-then "changed")))
  (<- first (Race bell other))
  (<- again (StopBell))
  (<- second-other (Spawn (quiet-then "changed again")))
  (<- second (Race again second-other))
  (assert (= #(first second) #("changed" "changed again")) #(first second))
  (assert (= again.promise-id bell.promise-id) (.format "StopBell of the same session is a different Future: {!r} {!r}" bell again))
  (<- (SendStop signal.SIGTERM))
  (<- (Wait bell))
  (<- asked (| str None) (StopRequested))
  (<- wanted str (signal-reason signal.SIGTERM))
  (assert (= asked wanted) (.format "StopRequested after the bell rang was {!r}" asked)))


(deftest test-the-stop-bell-after-a-stop-has-rung
  {:interpreters ["os-signal" "scripted"]}
  (<- (StopRequested))
  (<- (SendStop signal.SIGINT))
  (<- bell (StopBell))
  (<- other (Spawn (quiet-then "changed")))
  (<- first (Race bell other))
  (assert (!= first "changed") (.format "StopBell after the stop is not full (race answer {!r})" first))
  (<- (Wait other))
  None)
