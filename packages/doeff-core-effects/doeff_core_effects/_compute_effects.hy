;;; A generic effect "run pure computation and receive the answer as a value" (first consumer = the folds and question answers outside the processing loop of the agora screen
;;; -- formerly FoldLater and AnswerLater). A foundation vocabulary with no business words, at the same level as process_effects.hy. The answerer is swapped per mechanism:
;;;   thread-pool-compute-handler   runs on a thread pool the caller passes (thread_pool_compute.hy). Only the task that fired waits, and the scheduler's
;;;                                 other tasks keep running (used under scheduled -- waits on a promise completed from outside)
;;;   inline-compute-handler        no I/O -- runs synchronously on the spot (inline_compute.hy). It runs deterministically in the virtual-clock simulation
;;;
;;;   Compute   runs a program on a new VM with no handler attached and returns the answer as Computed (value) or ComputeFailed (exception).
;;;             The program must be pure -- if it issues an effect, there is no handler to reach and it becomes ComputeFailed (it does not reach the caller's handlers.
;;;             Same in both answerers). Interpreting success or failure is up to the caller (it does not raise).
;;;   ReadComputeLoad
;;;             Reads a cross-section of the computation load the answerer currently holds (ComputeLoad): the queue (number issued and not yet started running), the running (number running),
;;;             the cumulative number started, the cumulative seconds jobs waited from being issued until they started running, and the cumulative seconds finished jobs ran. To show the pool congestion (a large computation blocks all the
;;;             threads of the pool and later computations wait in line) as numbers to the user's instruments (e.g. GET /metrics). The cumulative values are since the answerer was built, and
;;;             dividing the difference of two reads by the difference in started gives the average wait seconds of the jobs that started in between. The answerer that runs on the spot has no queue, so all are 0.
;;;
;; Things triggered by time (wake up later, run delayed) are outside the scope of this effect -- use ScheduleAt / Delay of doeff-time.
(require doeff-hy.record [defrecord])
(import dataclasses [dataclass])
(import doeff [DoExpr EffectBase])


(defrecord Computed
  "Answer of Compute -- the value the program returned."
  (#^ object value))


(defrecord ComputeFailed
  "Answer of Compute -- the program failed (carries the exception as a value; interpretation is up to the caller)."
  (#^ Exception error))


(defclass [(dataclass :frozen True)] Compute [(get EffectBase (| Computed ComputeFailed))]
  "Run a pure program and receive the answer as a value (header comment). Answer = Computed | ComputeFailed."
  (#^ DoExpr program))


(defrecord ComputeLoad
  "Answer of ReadComputeLoad -- a cross-section of the computation load the answerer currently holds: queued = number of jobs issued and not yet started running, running = number of jobs
   running, started = cumulative number of jobs started, waited-seconds = cumulative seconds jobs that started waited from being issued until they started (sum over started jobs),
   ran-seconds = cumulative seconds finished jobs ran on the pool's threads (sum over started - running jobs -- difference of two reads / seconds between / number of threads = utilization)."
  {:check [(>= queued 0) (>= running 0) (>= started 0) (>= waited-seconds 0.0) (>= ran-seconds 0.0)]}
  (#^ int queued)
  (#^ int running)
  (#^ int started)
  (#^ float waited-seconds)
  (#^ float ran-seconds))


(defclass [(dataclass :frozen True)] ReadComputeLoad [(get EffectBase ComputeLoad)]
  "Reads a cross-section of the computation load the answerer currently holds (header comment). Answer = ComputeLoad.")
