;;; Tests for the generic compute effects (compute_effects.hy).
;;;   - The real one (thread-pool-compute-handler) and the no-I/O one (inline-compute-handler) give the same answer to the same scenario: a value, a failing program, an impure
;;;     program (does not reach the caller's handler and becomes ComputeFailed).
;;;   - The real one keeps running the scheduler's other tasks during a computation (it does not jam even if the computation waits for another task's signal).
;;;   - For the real one, the pool decides how many run at the same time.
;;;   - Load reading (ReadComputeLoad): for the real one, it answers the number of jobs waiting for a pool thread (queued), the number running (running), and the cumulative totals of the number started, seconds waited and seconds run,
;;;     and removes jobs cancelled before starting from the queue. The no-I/O one holds no queue so everything is 0. Counterexample: the earlier form had no gauge for the wait when the pool was blocked by a large computation,
;;;     and the wait was mixed into the per-request interval seconds and could not be told apart (doeff-records ADR-DOE-RECORDS-002 R6).
(require doeff-hy.macros [defk <- val])
(require doeff-hy.handle [defhandler])
(import threading)
(import time)
(import concurrent.futures [ThreadPoolExecutor])
(import dataclasses [dataclass])
(import typing [TypeVar])
(import doeff [run with_handlers EffectBase Program])
(import doeff_core_effects [scheduled Spawn Gather Cancel])
(import doeff_core_effects [Compute Computed ComputeFailed ComputeLoad ReadComputeLoad])
(import doeff_core_effects [inline-compute-handler])
(import doeff_core_effects [thread-pool-compute-handler])
(import pytest)


(defclass [(dataclass :frozen True)] Outside [EffectBase]
  "An effect that has no destination if issued from inside a computation (the scenario of an impure program).")


(defhandler outside-answers
  ;; A responder that exists only on the caller side -- to check that effects inside a computation do not reach here.
  (Outside [] (resume "the caller answered")))


(defk doubled [n]
  {:pre [(: n int)] :post [(: % int)]}
  "The scenario of a pure computation."
  (* 2 n))


(defk broken []
  {:pre [] :post [(: % int)]}
  "The scenario of a failing computation."
  (raise (ValueError "broken computation")))


(defk impure []
  {:pre [] :post [(: % str)]}
  "The scenario of a computation that issues an effect (impure)."
  (<- answer (Outside))
  answer)


(defk three-answers []
  {:pre [] :post [(: % tuple)]}
  "Fire the same 3 scenarios with Compute."
  (<- value (Compute (doubled 21)))
  (<- failed (Compute (broken)))
  (<- escaped (Compute (impure)))
  #(value failed escaped))


;; The answer type of the program that on runs (differs per scenario).
(val T (TypeVar "T"))


(defn #^ T on [#^ list handlers #^ (get Program T) program]
  "To run program once with a scheduler under a list of handlers."
  (run (scheduled (with_handlers handlers program))))


(defn #^ None check-three [#^ tuple answers]
  "To check the shape of the answers of the 3 scenarios (the same for the real one and the no-I/O one)."
  (setv #(value failed escaped) answers)
  (assert (= value (Computed :value 42)) value)
  (assert (and (isinstance failed ComputeFailed) (isinstance failed.error ValueError) (= (str failed.error) "broken computation")) failed)
  (assert (isinstance escaped ComputeFailed) escaped))


(defn #^ None test-inline-answers-values-and-failures-as-values []
  (check-three (on [outside-answers inline-compute-handler] (three-answers))))


(defn #^ None test-thread-pool-answers-the-same-as-inline []
  (with [pool (ThreadPoolExecutor :max-workers 2)]
    (check-three (on [outside-answers (thread-pool-compute-handler pool)] (three-answers)))))


(defn #^ list gate-race [#^ list handlers]
  "To fire, under handlers, the scenario where a computation waits for a gate opened by another task. If the scheduler stops during the computation, the gate does not open until the wait limit, and the computation
   returns False."
  (setv gate (threading.Event))
  (defk waits-for-gate []
    {:pre [] :post [(: % bool)]}
    "A computation that waits (with a wait limit) until another task opens the gate."
    (.wait gate 1.0))
  (defk opens-gate []
    {:pre [] :post [(: % str)]}
    "Another task that opens the gate."
    (.set gate)
    "opened")
  (defk both []
    {:pre [] :post [(: % list)]}
    "To run the computation task and the gate task at the same time."
    (<- computing (Spawn (Compute (waits-for-gate))))
    (<- opening (Spawn (opens-gate)))
    (<- answers (Gather computing opening))
    answers)
  (list (on handlers (both))))


(defn #^ None test-thread-pool-keeps-other-tasks-running-while-computing []
  (with [pool (ThreadPoolExecutor :max-workers 1)]
    (setv answers (gate-race [(thread-pool-compute-handler pool)])))
  (assert (= answers [(Computed :value True) "opened"]) answers))


(defn #^ None test-inline-blocks-other-tasks-while-computing []
  ;; Counterexample: with the responder that runs in place, the gate task does not advance during the computation -- confirming that the test above tells the difference.
  (assert (= (gate-race [inline-compute-handler]) [(Computed :value False) "opened"])))


(defn [pytest.mark.realtime] #^ None test-thread-pool-runs-as-many-at-once-as-the-pool-allows []
  (setv lock (threading.Lock)
        running [0]
        peak [0])
  (defk counted []
    {:pre [] :post [(: % int)]}
    "A computation that counts the maximum number of computations running at the same time."
    (with [lock]
      (setv (get running 0) (+ (get running 0) 1)
            (get peak 0) (max (get peak 0) (get running 0))))
    (time.sleep 0.1)
    (with [lock] (setv (get running 0) (- (get running 0) 1)))
    1)
  (defk four []
    {:pre [] :post [(: % list)]}
    "To fire 4 computations at once."
    (<- a (Spawn (Compute (counted))))
    (<- b (Spawn (Compute (counted))))
    (<- c (Spawn (Compute (counted))))
    (<- d (Spawn (Compute (counted))))
    (<- answers (Gather a b c d))
    answers)
  (with [pool (ThreadPoolExecutor :max-workers 2)]
    (setv answers (on [(thread-pool-compute-handler pool)] (four))))
  (assert (= (list answers) (* [(Computed :value 1)] 4)) answers)
  (assert (= (get peak 0) 2) peak))


(defn #^ tuple held-pair [#^ threading.Event started #^ threading.Event gate]
  "To make two scenarios of computations that block the pool's threads with a gate: when they start running they set started, and block the thread until the gate opens (up to 5 seconds)."
  (defk held []
    {:pre [] :post [(: % bool)]}
    "A computation that signals it has started running and blocks a pool thread until the gate opens."
    (.set started)
    (.wait gate 5.0))
  #((held) (held)))


(defn #^ None test-thread-pool-load-counts-the-queue-and-the-wait []
  ;; Block 1 pool thread with the 1st computation and queue the 2nd -- the load after the event that the 1st started is queued 1, running 1, started
  ;; 1. After the gate opens and both finish, queued and running are both 0, started is 2, and the wait total is greater than 0 as the seconds the 2nd waited for the 1st to finish.
  (setv started (threading.Event)
        gate (threading.Event))
  (setv #(first-held second-held) (held-pair started gate))
  (defk peek []
    {:pre [] :post [(: % ComputeLoad)]}
    "A task that waits for the event that the 1st started, then reads the load and opens the gate."
    (.wait started 5.0)
    (<- busy ComputeLoad (ReadComputeLoad))
    (.set gate)
    busy)
  (defk both []
    {:pre [] :post [(: % tuple)]}
    "To run two computations and a peeking task at the same time, and return the load when blocked and after finishing."
    (<- first (Spawn (Compute first-held)))
    (<- second (Spawn (Compute second-held)))
    (<- peeking (Spawn (peek)))
    (<- answers (Gather first second peeking))
    (<- after ComputeLoad (ReadComputeLoad))
    #((tuple answers) after))
  (with [pool (ThreadPoolExecutor :max-workers 1)]
    (setv #(answers after) (on [(thread-pool-compute-handler pool)] (both))))
  (setv busy (get answers 2))
  (assert (= (cut answers 0 2) #((Computed :value True) (Computed :value True))) answers)
  (assert (= #(busy.queued busy.running busy.started) #(1 1 1)) busy)
  (assert (= #(after.queued after.running after.started) #(0 0 2)) after)
  (assert (> after.waited-seconds 0.0) after)
  ;; The cumulative run seconds is the sum of the seconds the 2 computations blocked the pool thread (the 1st kept running until the peeking task opened the gate).
  (assert (> after.ran-seconds 0.0) after))


(defn #^ None test-thread-pool-load-drops-a-queued-job-that-is-cancelled []
  ;; A computation cancelled while still queued never starts, so it is removed from the queue and is not counted in the started number or the wait total.
  (setv started (threading.Event)
        gate (threading.Event))
  (setv #(first-held second-held) (held-pair started gate))
  (defk cancelled []
    {:pre [] :post [(: % tuple)]}
    "To block the pool with the 1st, cancel the queued 2nd, then open the gate, and return the load before and after the cancellation and after finishing."
    (<- first (Spawn (Compute first-held)))
    (<- second (Spawn (Compute second-held)))
    ;; After the 2 spawned tasks have sent their computations to the pool (hand the turn to the scheduler -- they run in spawn order), wait for the event that the 1st started.
    (<- handed (Spawn (doubled 0)))
    (<- (Gather handed))
    (.wait started 5.0)
    (<- busy ComputeLoad (ReadComputeLoad))
    (<- (Cancel second))
    (<- dropped ComputeLoad (ReadComputeLoad))
    (.set gate)
    (<- (Gather first))
    (<- after ComputeLoad (ReadComputeLoad))
    #(busy dropped after))
  (with [pool (ThreadPoolExecutor :max-workers 1)]
    (setv #(busy dropped after) (on [(thread-pool-compute-handler pool)] (cancelled))))
  (assert (= #(busy.queued busy.running) #(1 1)) busy)
  (assert (= #(dropped.queued dropped.running dropped.started) #(0 1 1)) dropped)
  (assert (= #(after.queued after.running after.started) #(0 0 1)) after))


(defn #^ None test-inline-load-is-always-idle []
  ;; The responder that runs in place holds no queue -- the load is all 0 even after running a computation.
  (defk computed-then-load []
    {:pre [] :post [(: % ComputeLoad)]}
    "To run one computation and then read the load."
    (<- (Compute (doubled 1)))
    (<- load ComputeLoad (ReadComputeLoad))
    load)
  (assert (= (on [inline-compute-handler] (computed-then-load)) (ComputeLoad :queued 0 :running 0 :started 0 :waited-seconds 0.0 :ran-seconds 0.0))))
