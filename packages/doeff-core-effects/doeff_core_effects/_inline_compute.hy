;;; I/O-free handler inline-compute-handler for the generic compute effects (compute_effects.hy). It uses no thread: it runs the program
;;; in a fresh VM right where the effect is performed and answers. The answer values are the same as the real thread-pool-compute-handler; the only difference is whether
;;; other tasks progress while it runs. It is a handler for deterministic runs under a virtual-clock simulation. It has no queue (the performing task runs it to completion in place), so the load read
;;; (ReadComputeLoad) always answers with an all-zero snapshot.
(require doeff-hy.macros [defhandler defk <- val])
(val MODULE-TAGS {:context "compute" :role "foundation"})
(import doeff [DoExpr])
(import doeff [PyVM])
(import doeff_core_effects._compute_effects [Compute Computed ComputeFailed ComputeLoad ReadComputeLoad])

;; Load of the run-in-place handler (nothing queued or running, no waiting work).
(val INLINE-LOAD (ComputeLoad :queued 0 :running 0 :started 0 :waited-seconds 0.0 :ran-seconds 0.0))


(defk computed [program]
  {:pre [(: program DoExpr)] :post [(: % (| Computed ComputeFailed))]}
  "Run program in a fresh VM with no handlers and turn the outcome into a value (the real handler also runs this in a worker thread)."
  (try
    (Computed :value (.run (PyVM) program))
    (except [error Exception]  ; boundary: the exception of a failed program is carried to the caller as the answer value (see the header note of compute_effects.hy)
      (ComputeFailed :error error))))


(defhandler inline-compute-handler
  "Answer Compute by running the program in a fresh VM right where it is performed, and answer ReadComputeLoad with an all-zero load (no thread is used)."
  ;; Run synchronously in place (see header note).
  (Compute [program]
    (<- outcome (computed program))
    (resume outcome))
  (ReadComputeLoad []
    (resume INLINE-LOAD)))
