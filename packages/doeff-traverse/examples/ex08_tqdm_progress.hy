;;; Example 8: tqdm progress bar — handler-injected visualization.
;;;
;;; The pipeline knows nothing about progress bars.
;;; A handler wraps each Traverse with tqdm, updating on each item completion.
;;;
;;; proboscis-ema has throttled_gather_with_progress that manually wires tqdm.
;;; With doeff-traverse, just add (tqdm-progress) to the handler stack.

(require doeff-hy.macros [defk <- traverse fold])
(import doeff [do :as _doeff-do])
(import doeff [run EffectBase])
(import doeff.program [WithHandler Resume Pass])

(import doeff_core_effects [try-handler :as try_handler Await])
(import doeff_core_effects.handlers [await-handler :as await_handler])
(import doeff_core_effects.scheduler [scheduled])

(import doeff_traverse [Traverse :as _doeff_traverse_Traverse])
(import doeff_traverse.effects [Traverse Reduce :as _doeff_traverse_Reduce])
(import doeff_traverse.handlers [sequential parallel fail-handler :as fail_handler])
(import doeff_traverse.collection [Collection])

(import tqdm [tqdm])


;; ===========================================================================
;; tqdm-progress handler: wraps Traverse items with a progress bar
;; ===========================================================================

(defn tqdm-progress []
  "Handler that adds tqdm progress bar to every Traverse.
   Uses the Traverse label as the bar description.
   Mutates the Traverse effect's f to wrap with tqdm update,
   then passes the effect through to the next handler (sequential/parallel)."

  (defk handler [effect k]
    (if (isinstance effect Traverse)
        (do
          ;; Materialize for len (tqdm needs total)
          (setv items (if (isinstance effect.items Collection)
                          effect.items
                          (list effect.items)))
          (setv total (if (isinstance items Collection) (len items) (len items)))
          (setv bar (tqdm :total total
                          :desc (or effect.label "traverse")
                          :leave True))
          (setv orig-f effect.f)
          ;; Wrap f: call original, then update bar
          (defn wrapped [x]
            (defk _run []
              (<- result (orig-f x))
              (.update bar)
              result)
            (_run))
          ;; Mutate effect and pass through
          (setv effect.f wrapped)
          (setv effect.items items)
          ;; Pass to next handler (sequential/parallel handles execution)
          (yield (Pass effect k))
          (.close bar))
        (yield (Pass effect k))))

  handler)


;; ===========================================================================
;; Async compute (100ms per item)
;; ===========================================================================

(setv _ns {})
(exec "
import asyncio
async def async_compute(x):
    await asyncio.sleep(0.1)
    return x * 10
" _ns)
(setv async-compute (get _ns "async_compute"))


;; ===========================================================================
;; Pipeline — no progress bars, no parallelism in logic
;; ===========================================================================

(defclass Compute [EffectBase]
  (defn __init__ [self x]
    (.__init__ (super))
    (setv self.x x)))

(defk compute-backend [effect k]
  (if (isinstance effect Compute)
      (do
        (<- result (Await (async-compute effect.x)))
        (return (yield (Resume k result))))
      (yield (Pass effect k))))

(defk pipeline [items]
  (<- stage1
    (traverse
      (<- x (Iterate items :label "stage1: compute"))
      (<- y (Compute x))
      y))
  (<- total (fold stage1 :init 0 (+ acc it)))
  (<- stage2
    (traverse
      (<- v (Iterate stage1 :label "stage2: normalize"))
      (round (/ v total) 4)))
  stage2)


;; ===========================================================================
;; Run — same program, with and without progress bar
;; ===========================================================================

(defn with-stack [stack program]
  (setv body program)
  (for [h stack]
    (setv body (WithHandler h body)))
  (scheduled body))

(setv items (list (range 1 21)))
(setv program (pipeline items))

;; Without tqdm
(print "=== without tqdm (parallel) ===")
(setv result (run (with-stack
  [try_handler compute-backend fail_handler (await_handler) (parallel 10)]
  program)))
(print "Done:" (. result valid_values))

;; With tqdm — just add (tqdm-progress) to the stack
;; tqdm-progress sits between parallel and the body handlers
;; so it sees each Traverse before parallel dispatches it
(print)
(print "=== with tqdm (parallel) ===")
(setv result (run (with-stack
  [try_handler compute-backend fail_handler (await_handler) (tqdm-progress) (parallel 10)]
  program)))
(print "Done:" (. result valid_values))
