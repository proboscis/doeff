;;; Example 5: Parallel execution — same program, swappable backend + strategy.
;;;
;;; The pipeline performs a SlowCompute effect per item.
;;; Both the execution strategy AND the compute backend are handlers:
;;;
;;;   - sequential / parallel: how items are dispatched
;;;   - async-backend / sync-backend: how each SlowCompute is executed
;;;
;;; Same program runs with any combination.

(require doeff-hy.macros [defk <- traverse fnk])
(import doeff [do :as _doeff-do])
(import doeff [run EffectBase])
(import doeff.program [WithHandler Resume Pass])

(import doeff_core_effects [try-handler :as try_handler Await])
(import doeff_core_effects.handlers [await-handler :as await_handler])
(import doeff_core_effects.scheduler [scheduled])

(import doeff_traverse [Traverse :as _doeff_traverse_Traverse])
(import doeff_traverse.effects [Reduce])
(import doeff_traverse.handlers [sequential parallel fail-handler :as fail_handler])

(import time [time sleep])


;; ===========================================================================
;; SlowCompute effect — "compute x, takes ~100ms"
;; The logic doesn't know HOW this is executed.
;; ===========================================================================

(defclass SlowCompute [EffectBase]
  (defn __init__ [self x]
    (.__init__ (super))
    (setv self.x x)))


;; ===========================================================================
;; Pipeline — uses SlowCompute effect, no backend knowledge
;; ===========================================================================

(defk pipeline [items]
  (<- results
    (traverse
      (<- x (Iterate items))
      (<- y (SlowCompute x))    ;; effect — handler decides how
      y))
  (<- total (Reduce (fnk [acc x] (+ acc x)) 0 results))
  total)


;; ===========================================================================
;; Backend 1: async (non-blocking, benefits from parallelism)
;; ===========================================================================

(setv _ns {})
(exec "
import asyncio
async def _async_sleep_compute(x):
    await asyncio.sleep(0.1)
    return x * 10
" _ns)
(setv _async-impl (get _ns "_async_sleep_compute"))

(defk async-backend [effect k]
  "Handle SlowCompute via async sleep (non-blocking)."
  (if (isinstance effect SlowCompute)
      (do
        (<- result (Await (_async-impl effect.x)))
        (return (yield (Resume k result))))
      (yield (Pass effect k))))


;; ===========================================================================
;; Backend 2: sync (blocking, no parallelism benefit)
;; ===========================================================================

(defk sync-backend [effect k]
  "Handle SlowCompute via sync sleep (blocking)."
  (if (isinstance effect SlowCompute)
      (do
        (sleep 0.1)
        (return (yield (Resume k (* effect.x 10)))))
      (yield (Pass effect k))))


;; ===========================================================================
;; Run: same program, different combinations
;; ===========================================================================

(setv items (list (range 1 11)))
(setv program (pipeline items))

(defn with-stack [stack program]
  (setv body program)
  (for [h stack]
    (setv body (WithHandler h body)))
  (scheduled body))

(defn timed-run [label stack]
  (setv t0 (time))
  (setv result (run (with-stack stack program)))
  (setv dt (round (- (time) t0) 2))
  (print (+ label (str result) " in " (str dt) "s")))

;; async backend + sequential: 10 × 100ms = ~1.0s
(timed-run "async + sequential:   "
  [try_handler async-backend fail_handler (await_handler) (sequential)])

;; async backend + parallel(10): all at once = ~0.1s
(timed-run "async + parallel(10): "
  [try_handler async-backend fail_handler (await_handler) (parallel 10)])

;; async backend + parallel(3): 4 batches = ~0.4s
(timed-run "async + parallel(3):  "
  [try_handler async-backend fail_handler (await_handler) (parallel 3)])

;; sync backend + sequential: 10 × 100ms = ~1.0s (same as async seq)
(timed-run "sync + sequential:    "
  [try_handler sync-backend fail_handler (sequential)])
