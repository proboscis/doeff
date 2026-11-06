;;; The real responder thread-pool-compute-handler for the generic compute effect (compute_effects.hy). It runs the program on a
;;; thread pool handed in by the caller, and waits on a promise completed from outside (the scheduler's CreateExternalPromise). Only the task that fired waits; the scheduler's other
;;; tasks keep running meanwhile -- use it under scheduled (same shape as ScheduleAt in doeff-time's sync-time-handler).
;;;
;;; The pool's owner is the caller (the assembly side both creates and closes it -- the number running at once is the pool's max_workers). If the waiting task is Cancelled, work that has not
;;; started yet is removed from the pool (work that has started is not stopped -- its answer is discarded).
;;; WARNING: These are Python threads, so heavy pure-Python computation shares the GIL. What you get is "the processing loop keeps answering other events during the computation",
;;; not a speedup proportional to the CPU count.
;;;
;;; Pool load (the answer to ReadComputeLoad): one snapshot per pool (ComputeLoad -- an immutable value) is kept in a single per-process store LOADS,
;;; and replaced inside LOADS-LOCK when work is submitted (queued + 1), when it starts running on a pool thread (queued - 1, running + 1, waited seconds = from submit time to start time), when it finishes (running - 1,
;;; ran seconds = from start time to finish time), and
;;; when it is cancelled before starting (queued - 1). Times come from time.monotonic (the submit time is on the scheduler's thread, the start
;;; time on a pool thread -- the same clock). One entry per pool, not one per responder built: building two responders on the same pool still gives one pool row. Only
;;; work submitted by this responder is counted (work the caller submits directly to the same pool is not counted). When a pool is discarded its snapshot disappears too (weak-reference key).
(require doeff-hy.macros [defhandler deff <- val])
(val MODULE-TAGS {:context "compute" :role "foundation"})
(import concurrent.futures [Executor Future])
(import threading)
(import time)
(import weakref)
(import doeff [Program])
(import doeff [PyVM])
(import doeff_core_effects._scheduler [CreateExternalPromise ExternalPromise Wait])
(import doeff_core_effects._compute_effects [Compute ComputeLoad ReadComputeLoad])
(import doeff_core_effects._inline_compute [computed])

;; Load of a pool that has not had any work submitted.
(val IDLE-LOAD (ComputeLoad :queued 0 :running 0 :started 0 :waited-seconds 0.0 :ran-seconds 0.0))
;; pool -> snapshot of the load of work submitted to that pool (see the header comment). Writes are a single replacement inside LOADS-LOCK.
(val LOADS (weakref.WeakKeyDictionary))
(val LOADS-LOCK (threading.Lock))


(deff shift-load [pool queued running started waited ran]  ; defk にできない: a write outside the VM, also called from pool threads (work entry and exit) and the completion callback
  {:pre [(: pool Executor) (: queued int) (: running int) (: started int) (: waited float) (: ran float)] :post [(: % None)]}
  "Add the deltas to the pool's load snapshot and replace it (see the header comment -- the queued and running counts move in the same single replacement)."
  (with [LOADS-LOCK]
    (setv load (.get LOADS pool IDLE-LOAD))
    (setv (get LOADS pool) (ComputeLoad :queued (+ load.queued queued) :running (+ load.running running)
                                        :started (+ load.started started) :waited-seconds (+ load.waited-seconds waited)
                                        :ran-seconds (+ load.ran-seconds ran))))
  None)


(deff load-of [pool]  ; defk にできない: a one-line read of the store by the responder clause (a store outside the VM -- read inside the same lock as the writes)
  {:pre [(: pool Executor)] :post [(: % ComputeLoad)]}
  "Read the current snapshot of the pool's load (a pool that has had no work submitted gets IDLE-LOAD)."
  (with [LOADS-LOCK]
    (.get LOADS pool IDLE-LOAD)))


(deff settle [pool promise future]  ; defk にできない: the completion callback called from a pool thread (the body of the function passed to add-done-callback, outside the VM)
  {:pre [(: pool Executor) (: promise ExternalPromise) (: future Future)] :post [(: % None)]}
  "Hand the end of the pool's work to the promise (computed turns exceptions into values, so only BaseException can fail here). Work cancelled before starting is
   removed from the queue (it never started running, so it is not in the running and waiting totals)."
  (cond
    (.cancelled future) (shift-load pool -1 0 0 0.0 0.0)
    (is-not (.exception future) None) (.fail promise (.exception future))
    True (.complete promise (.result future))))


(deff run-computed [program pool submitted]  ; defk にできない: the entry run on a pool thread (passed to pool.submit -- starts a new VM from outside the VM)
  {:pre [(: program Program) (: pool Executor) (: submitted float)] :post [(: % "the answer of program (what computed turned the exception into a value)")]}
  "Turn the program's answer into a value on a pool thread, and write the start and end to the pool's load (waited seconds = from the submit time `submitted` to the start time,
   ran seconds = from the start time to the finish time)."
  (setv began (time.monotonic))
  (shift-load pool -1 1 1 (max 0.0 (- began submitted)) 0.0)
  (try
    (.run (PyVM) (computed program))
    (finally
      (shift-load pool 0 -1 0 0.0 (max 0.0 (- (time.monotonic) began))))))


(defhandler thread-pool-compute-handler [#^ Executor pool]
  "Answer Compute by running the program in a new VM on a pool thread (only the task that fired waits), and answer ReadComputeLoad with the pool's load."
  ;; Why it stays an argument: the pool is a resource the assembly side creates and closes (the number running at once is also decided there).
  (Compute [program]
    (<- promise (CreateExternalPromise))
    ;; Add to the queue before submitting (a pool thread that starts running only subtracts from the queue after the submit). If a closed pool refuses the submit, give back what was added.
    (shift-load pool 1 0 0 0.0 0.0)
    (val job (try (.submit pool run-computed program pool (time.monotonic))
                  (except [refused RuntimeError]
                    (shift-load pool -1 0 0 0.0 0.0)
                    (raise refused))))
    (.on-cancel promise (fn [] (.cancel job)))
    (.add-done-callback job (fn [done] (settle pool promise done)))
    (<- outcome (Wait promise.future))
    (resume outcome))
  (ReadComputeLoad []
    (resume (load-of pool))))
