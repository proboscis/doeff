;;; The machinery that runs blocking calls (driver I/O, etc.) outside the scheduler's threads, with only the task that fired waiting on a promise completed from outside.
;;; Used by both PostgreSQL answerers - postgres-sql-handler (postgres_sql.hy) and pooled-postgres-sql-handler
;;; (pooled_postgres_sql.hy - the machinery written in #880 U2 was moved here).
;;;   - offloaded = runs call on an Executor thread and waits with CreateExternalPromise + Wait (the same practice as thread_pool_compute.hy). Only the task
;;;     that fired waits, and the scheduler's other tasks keep running - used under scheduled. When the wait is cancelled, Handoff does the cleanup: work that has not started
;;;     is removed, and an answer produced before or after the cancellation is handed exactly once to abandon (cleanup run on the Executor - returning a leased connection, etc.). For work that is already running and has
;;;     no answer yet, interrupt (a way of stopping run on the Executor - stopping a running child process, etc.) is run exactly once. Work that has no way of
;;;     stopping (the default keep-going) runs to completion, and its answer goes to abandon.
;;;   - The answer is put in a one-slot box Delivered and the promise is completed, and the box is emptied when handed to the waiter. The scheduler keeps the answer of a completed promise in its table until the sweep (every HANDLE_SWEEP_INTERVAL ids - doeff #502)
;;;     sees that all handles are gone. If completed with the answer itself, an answer the waiter has let go of
;;;     (rows of a ClickHouse read, file contents) stays until the sweep, and the Rust version's table is not even visible to gc (a read-path
;;;     job went from RssAnon 440 to 2464 MB in 3.5 hours, and the remaining type was SqlRows). The same practice as _AwaitedValue of await_handler.
;;;   - ThreadPerCall = an Executor that starts one thread per call (the default, needing neither an owner nor cleanup). No upper limit is put on the number of threads so that, even if a call
;;;     waits inside a thread for another resource (a connection permit), the next call of the side that holds that resource does not wait for a free thread and jam. The upper limit on concurrency is
;;;     held by the resource side that calls wait for (the permits of PostgresConnections). Threads are daemon - a call that does not stop (a long statement) does not hold up the end of the process.
;;;   - ReusedThreads = an Executor that hands a call to an idle thread and starts one thread only when there is none idle. Like ThreadPerCall
;;;     it has no upper limit and is daemon, and the only difference is that after a call the thread waits for _IDLE-SECONDS and is used for the next call. Starting a thread per call
;;;     made the start-up wait (a few ms on the caller's thread) proportional to the number of statements, and it took 20% of the custody's ordinary CPU. The number of concurrently running threads is the same as ThreadPerCall
;;;     (the peak number of calls), and a thread that stays idle past _IDLE-SECONDS ends. After finishing a call, a thread drops its references to the call and the answer
;;;     before waiting for the next (a waiting thread does not keep holding the answer - the same reason as Delivered above).
(require doeff-hy.macros [defk deff <- val])
(val MODULE-TAGS {:context "sql" :role "foundation"})
(import collections.abc [Callable])
(import concurrent.futures [Executor Future])
(import queue [Empty SimpleQueue])
(import threading)
(import doeff [PyVM])
(import doeff_core_effects._scheduler [CreateExternalPromise Wait TaskCancelledError])

;; A mark that the answer has not been handed over yet (to tell it apart from work whose answer is None).
(val NOT-DELIVERED (object))
;; Seconds an idle thread of ReusedThreads waits for the next call (it ends when passed - header comment).
(val _IDLE-SECONDS 60.0)


(defclass Delivered []
  "A one-slot box that hands one answer of a job exactly once from the promise table to the waiter (or the cleanup) (header comment). What stays in the table is the empty box, and the answer lives only
   while the receiver holds it. It is a resource handler, so not a value type."
  (setv __slots__ #("_mut_value"))

  (deff __init__ [self value]  ; defk にできない: initialization of a resource class
    {:pre [(: self Delivered) (: value "answer of the job")] :post [(: % "None")]}
    "To start holding the answer."
    (setv self._mut-value value))

  (deff take [self]  ; defk にできない: the box's entry touched by the scheduler's thread and the cancellation callback (a resource outside the VM)
    {:pre [(: self Delivered)] :post [(: % "answer of the job")]}
    "To hand over the answer once and drop the box's reference (None the second time)."
    (setv value self._mut-value)
    (setv self._mut-value None)
    value))


(deff run-detached [program]  ; defk にできない: entry run on the Executor's thread (starts a new VM from outside the VM)
  {:pre [(: program "Program that calls the driver")] :post [(: % "answer of program")]}
  "To turn a Program that holds blocking calls (the driver's statement procedure) into a value in a new VM on the Executor's thread."
  (.run (PyVM) program))


(deff keep-nothing [_value]  ; defk にできない: entry of cleanup run on the Executor's thread (cleanup for an answer that holds nothing)
  {:pre [(: _value "the answer that did not arrive")] :post [(: % "None")]}
  "Cleanup for when the answer that did not reach a cancelled waiter holds no resource (does nothing)."
  None)


(deff keep-going []  ; defk にできない: entry of a stopping method run on the Executor's thread (the default for work with nothing to stop)
  {:pre [] :post [(: % "None")]}
  "The way of stopping for when there is no means to stop running work once the waiter is cancelled (does nothing - the work runs to completion and its answer goes to abandon)."
  None)


(defclass ThreadPerCall [Executor]
  "An Executor that starts one daemon thread per call (header comment). It is a resource handler, so not a value type."

  (deff __init__ [self [on-start None]]  ; defk にできない: initialization of a resource class
    {:pre [(: self ThreadPerCall) (: on-start (| None (get Callable #([] None))))] :post [(: % "None")]}
    "To hold a hand (on-start - lowering the thread's IO priority, etc.) called once before the thread runs the call (None = do nothing)."
    (setv self.on-start on-start))

  (deff submit [self call #* args]  ; defk にできない: Executor's entry (a concurrent.futures promise - outside the VM)
    {:pre [(: self ThreadPerCall) (: call "callable (args) -> value") (: args tuple)] :post [(: % Future)]}
    "To run call on a new thread and hand over its end as a Future (a Future cancelled before starting is not run; on-start is called inside the thread before call)."
    (setv future (Future))
    (.start (threading.Thread :target (fn []
                                        (when (.set-running-or-notify-cancel future)
                                          (try
                                            (when (is-not self.on-start None)
                                              (.on-start self))
                                            (.set-result future (call #* args))
                                            (except [error BaseException]
                                              (.set-exception future error)))))
                              :name "doeff-offloaded-call"
                              :daemon True))
    future))


(defclass ReusedThreads [Executor]
  "An Executor that hands a call to an idle daemon thread and starts one thread only when none is idle (header comment). It is a resource handler, so not a value type.
   idle = the number of threads waiting for a call that have not yet been assigned one. started = the number of threads started (read by tests)."

  (deff __init__ [self [idle-seconds _IDLE-SECONDS]]  ; defk にできない: initialization of a resource class
    {:pre [(: self ReusedThreads) (: idle-seconds float)] :post [(: % "None")]}
    "To hold the seconds an idle thread waits for the next call, and start the call queue and the counts empty."
    (setv self.idle-seconds idle-seconds
          self.idle-lock (threading.Lock)
          self.jobs (SimpleQueue)
          self.idle 0
          self.started 0))

  (deff submit [self call #* args]  ; defk にできない: Executor's entry (a concurrent.futures promise - outside the VM)
    {:pre [(: self ReusedThreads) (: call "callable (args) -> value") (: args tuple)] :post [(: % Future)]}
    "To put call in the queue, start one thread if there is no idle one, and hand over its end as a Future (a Future cancelled before starting is not run).
     Threads are started outside the lock - the wait to start one does not stall other calls or the count of idle threads."
    (setv future (Future))
    (with [_ self.idle-lock]
      (.put self.jobs #(future call args))
      (setv fresh (= self.idle 0))
      (if fresh
          (setv self.started (+ self.started 1))
          (setv self.idle (- self.idle 1))))
    (when fresh
      (.start (threading.Thread :target self.serve :name "doeff-offloaded-call" :daemon True)))
    future)

  (deff serve [self]  ; defk にできない: the thread's body (outside the VM - takes calls from the queue and runs them)
    {:pre [(: self ReusedThreads)] :post [(: % "None")]}
    "To run the calls in the queue one at a time, and end if idle-seconds pass while idle. When the wait times out, look at the queue once more under the lock - do not drop a call that was
     assigned to this thread at the same moment it timed out (what submit placed after decrementing idle). It is counted as idle before writing the answer to the Future - so that even if the caller that
     received the answer immediately issues the next call, this thread is counted as idle and a new thread is not started."
    (while True
      (setv job
        (try
          (.get self.jobs :timeout self.idle-seconds)
          (except [Empty]
            (with [_ self.idle-lock]
              (try
                (.get-nowait self.jobs)
                (except [Empty]
                  (setv self.idle (- self.idle 1))
                  None))))))
      (when (is job None)
        (return None))
      (setv #(future call args) job
            job None
            value None
            error None
            ;; A Future cancelled before starting is not run.
            ran (.set-running-or-notify-cancel future))
      (when ran
        (try
          (setv value (call #* args))
          (except [raised BaseException]
            (setv error raised))))
      (setv call None
            args None)
      (with [_ self.idle-lock]
        (setv self.idle (+ self.idle 1)))
      (cond
        (not ran) None
        (is-not error None) (.set-exception future error)
        True (.set-result future value))
      ;; Do not hold the call and the answer while waiting (header comment).
      (setv future None
            value None
            error None))))


(defclass Handoff []
  "The handler that hands the answer of one Executor job to the promise (it touches them taking a lock between the scheduler's thread and the job's thread). When the waiter is cancelled, work that has
   not started is removed, an answer produced before or after the cancellation goes to abandon (cleanup run on the Executor - returning a leased connection, etc.), and work that is running with no answer yet
   goes to interrupt (a way of stopping run on the Executor), exactly once for either one."

  (deff __init__ [self pool abandon interrupt]  ; defk にできない: initialization of a resource class
    {:pre [(: self Handoff) (: pool Executor) (: abandon (get Callable #([object] None))) (: interrupt (get Callable #([] None)))]
     :post [(: % "None")]}
    ;; The lock is an RLock that can be re-entered from the same thread: when give-up removes unstarted work while holding the lock, Future.cancel calls the completion
    ;; callback (deliver) on the same thread. With a non-reentrant lock the scheduler's thread stalled there and the whole run froze (a discovery).
    ;; The job's Future is not held: the Future's completion callback holds this handler and the promise, so if the handler held the Future it would be a reference cycle, and the handles of the promise and
    ;; of what abandon holds (bells, etc.) would not vanish until the cycle collection (gc), and the scheduler's sweep could not remove that row.
    ;; The Future is received as an argument of give-up (held by the cancellation callback and the wait's cleanup - both are let go of once the promise ends).
    (setv self.pool pool
          self.abandon abandon
          self.interrupt interrupt
          self.lock (threading.RLock)
          self.abandoned False
          self.delivered NOT-DELIVERED))

  (deff deliver [self promise future]  ; defk にできない: completion callback called from the job's thread (outside the VM)
    {:pre [(: self Handoff) (: promise "scheduler ExternalPromise") (: future Future)] :post [(: % "None")]}
    "To hand the end of the job to the promise (an answer produced after cancellation goes to cleanup)."
    (with [_ self.lock]
      (cond
        (.cancelled future) None
        (is-not (.exception future) None) (when (not self.abandoned) (.fail promise (.exception future)))
        self.abandoned (.submit self.pool self.abandon (.result future))
        True (do (setv self.delivered (Delivered (.result future)))
                 (.complete promise self.delivered)))))

  (deff give-up [self job]  ; defk にできない: the scheduler's cancellation callback (on_cancel - outside the VM), and the cleanup for when the wait exits by cancellation
    {:pre [(: self Handoff) (: job Future)] :post [(: % "None")]}
    "When the waiter is cancelled, remove work that has not started, send the answer already handed over to cleanup, and send work that is running with no answer yet to the stopping method
     (however many times it is called, either cleanup or the stopping method exactly once). The stopping method is run on the Executor so that even if stopping needs a wait (grace period), it does not
     block the scheduler's thread. The stopping method is also sent to work that has just finished running and is about to hand over its answer, so the stopping method must be a form that does nothing if there is nothing to stop."
    (with [_ self.lock]
      (when (not self.abandoned)
        (setv self.abandoned True)
        (setv removed (.cancel job))
        (cond
          removed None
          (is-not self.delivered NOT-DELIVERED) (.submit self.pool self.abandon (.take self.delivered))
          True (.submit self.pool self.interrupt))))))


(defk offloaded [pool call abandon [interrupt keep-going]]
  {:pre [(: pool Executor) (: call (get Callable #([] object))) (: abandon (get Callable #([object] None)))
         (: interrupt (get Callable #([] None)))]
   :post [(: % "answer of call")]
   :tags {:context "sql" :role "foundation"}}
  "To run call on a pool thread and wait with a promise that only this task completes from outside (the scheduler's other tasks keep running). When the wait is cancelled,
   work that has not started is removed and an answer that did not arrive is cleaned up with abandon (both for a cancellation before the completion value arrives and for one after it arrives but before resumption).
   Running work is stopped with interrupt (default keep-going = let it run to completion without stopping - header comment). The answer is taken out of the box Delivered and returned, and
   only the empty box is left in the scheduler's promise table (header comment)."
  (<- promise (CreateExternalPromise))
  (val handoff (Handoff pool abandon interrupt))
  (val job (.submit pool call))
  (.on-cancel promise (fn [] (.give-up handoff job)))
  (.add-done-callback job (fn [done] (.deliver handoff promise done)))
  (try
    (<- delivered (Wait promise.future))
    (except [TaskCancelledError]
      (.give-up handoff job)
      (raise)))
  (.take delivered))
