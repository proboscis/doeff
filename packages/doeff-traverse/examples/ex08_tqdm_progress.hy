;;; Example 8: tqdm progress bar — handler-injected visualization.
;;;
;;; The pipeline knows nothing about progress bars.
;;; The Traverse handler reports each item's outcome (ItemOutcome: ok, failed,
;;; skipped, carried), and a handler that counts them draws one bar per Traverse.
;;;
;;; proboscis-ema has throttled_gather_with_progress that manually wires tqdm.
;;; With doeff-traverse, just add (tqdm-progress) to the handler stack.

(require doeff-hy.macros [defk <- traverse defhandler])
(import doeff [do :as _doeff-do])
(import doeff [run EffectBase])

(import doeff_core_effects [try-handler :as try_handler Await])
(import doeff_core_effects [await-handler :as await_handler])
(import doeff_core_effects [scheduled])

(import doeff_traverse [Traverse :as _doeff_traverse_Traverse])
(import doeff_traverse [Reduce])
(import doeff_traverse [parallel progress-bars fail-handler :as fail_handler])
(import doeff_traverse [Collection])

(import tqdm [tqdm])


;; ===========================================================================
;; tqdm-progress handler: one tqdm bar per Traverse
;; ===========================================================================

(defn tqdm-progress []
  "Handler that adds a tqdm progress bar to every Traverse.
   Uses the Traverse label as the bar description.
   The Traverse handler (sequential/parallel) reports every item's outcome —
   failed items, items an earlier stage carried over as failed, and items When
   skipped included — so each bar reaches its total and closes.
   Install it inside the Traverse handler, like a body handler: the outcome is
   reported under the handlers between the Traverse and its handler."
  (progress-bars tqdm))


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

;; A backend that is down for one item: that item fails, the others go on.
(defhandler flaky-backend
  (Compute [x]
    (when (= x 7)
      (raise (ConnectionError "backend down for 7")))
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
;; tqdm-progress sits between parallel and the body handlers,
;; where parallel reports each item's outcome
(print)
(print "=== with tqdm (parallel) ===")
(setv result (run (with-stack
  [try_handler compute-backend fail_handler (await_handler) (tqdm-progress) (parallel 10)]
  program)))
(print "Done:" (. result valid_values))

;; With tqdm and a failing item — both bars still reach 20/20 and close:
;; stage1 counts the failed item, stage2 the item it carries over
(print)
(print "=== with tqdm, item 7 failing (parallel) ===")
(setv result (run (with-stack
  [try_handler flaky-backend fail_handler (await_handler) (tqdm-progress) (parallel 10)]
  program)))
(print "Done:" (len (. result valid_values)) "valid,"
       (lfor item (. result failed_items) (. item index)) "failed")
