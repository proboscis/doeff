;;; Example 8: tqdm progress bar — handler-injected visualization.
;;;
;;; The pipeline knows nothing about progress bars.
;;; A handler wraps each Traverse with tqdm, updating on each item completion.
;;;
;;; proboscis-ema has throttled_gather_with_progress that manually wires tqdm.
;;; With doeff-traverse, just add (tqdm-progress) to the handler stack.

(require doeff-hy.macros [defk <- traverse defhandler do!])
(import doeff [do :as _doeff-do])
(import doeff [run EffectBase])

(import doeff_core_effects [try-handler :as try_handler Await])
(import doeff_core_effects.handlers [await-handler :as await_handler])
(import doeff_core_effects.scheduler [scheduled])

(import doeff_traverse [Traverse :as _doeff_traverse_Traverse])
(import doeff_traverse.effects [Traverse Reduce])
(import doeff_traverse.handlers [sequential parallel fail-handler :as fail_handler])
(import doeff_traverse.collection [Collection])

(import tqdm [tqdm])


;; ===========================================================================
;; tqdm-progress handler: wraps Traverse items with a progress bar
;; ===========================================================================

(defhandler tqdm-progress []
  "Handler that adds tqdm progress bar to every Traverse.
   Uses the Traverse label as the bar description.
   Mutates the Traverse effect's f to wrap with tqdm update,
   then reperforms the effect to the next handler (sequential/parallel).
   Other effects are not matched, so defhandler passes them through.

   The bar counts items whose f finished. An item that fails, that an earlier
   stage carried over as failed, or that When skipped is not counted, so the bar
   can stop short of total and stay open: the Traverse handler answers the whole
   Collection at once and does not report each item's outcome. Reperform is
   required (the same k keeps the inner handlers visible to each item), so this
   handler cannot run code when the Traverse finishes either."
  (Traverse [f items label]
    ;; Materialize for len (tqdm needs total)
    (setv all-items (if (isinstance items Collection) items (list items)))
    (setv bar (tqdm :total (len all-items)
                    :desc (or label "traverse")
                    :leave True))
    ;; Wrap f: call original, then update bar (close it when every item has finished)
    (defn wrapped [x]
      (do!
        (<- result (f x))
        (.update bar)
        (when (>= bar.n bar.total) (.close bar))
        result))
    ;; Mutate effect and reperform it with the same continuation k
    ;; (sequential/parallel handles execution; reperform is terminal)
    (setv effect.f wrapped)
    (setv effect.items all-items)
    (reperform effect)))


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

(defhandler compute-backend
  (Compute [x]
    (<- result (Await (async-compute x)))
    (resume result)))

(defk add-value [acc v]
  "Reduce step: add one computed value to the running total."
  {:pre [(: acc int) (: v int)]
   :post [(: % int)]}
  (+ acc v))

(defk pipeline [items]
  {:pre [(: items list)]
   :post [(: % Collection)]}
  (<- stage1
    (traverse
      (<- x (Iterate items :label "stage1: compute"))
      (<- y (Compute x))
      y))
  (<- total (Reduce add-value 0 stage1))
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
    (setv body (h body)))
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
