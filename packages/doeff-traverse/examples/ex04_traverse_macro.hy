;;; Example 4: traverse macro — CPS-converted Iterate.
;;;
;;; This is the main example showing the doeff-traverse design.
;;;
;;; Key ideas:
;;;   1. Program is DATA — (pipeline items) builds a computation graph, not runs it.
;;;   2. Strategy is HANDLER — error recovery / parallelism injected externally.
;;;   3. run() is the only place where execution happens.
;;;
;;; The `traverse` macro replaces do-list/do-list-try/do-try-list/do-dict-try.
;;; It CPS-converts `Iterate` into a `Traverse` effect, letting the handler
;;; decide: sequential vs parallel, fail-fast vs run-all.
;;;
;;; The pipeline has NO error handling — no try/except, no Result matching.

;; --- Imports ---
;; defk: define a kleisli arrow (function returning DoExpr)
;; <-:   bind (yield + assign) inside defk
;; traverse: macro that CPS-converts Iterate into Traverse effect
;; fnk:  anonymous kleisli (lambda returning DoExpr, can perform effects)
(require doeff-hy.macros [defk <- traverse fnk fold])
(import doeff [do :as _doeff-do])
(import doeff [run])
(import doeff.program [WithHandler Resume Pass])

(import doeff_core_effects [try-handler :as try_handler])
(import doeff_core_effects.scheduler [scheduled])

;; _doeff_traverse_Traverse: needed by the traverse macro's expansion
(import doeff_traverse [Traverse :as _doeff_traverse_Traverse])
;; Fail: low-level failure effect. Handler decides what to do.
;; Reduce: fold over opaque collection. f is kleisli: (acc, item) -> DoExpr[acc]
;; Inspect: extract per-item value + history for post-hoc analysis
(import doeff_traverse.effects [Fail Reduce :as _doeff_traverse_Reduce Inspect])
;; sequential: Traverse handler — runs items one by one, isolates per-item failure
;; fail_handler: converts unhandled Fail into Python exception
(import doeff_traverse.handlers [sequential fail-handler :as fail_handler])
;; try_call: wraps a plain Python function as a yield site.
;;           If it raises, performs Fail(exception).
;;           Handler can Resume with a substitute value.
(import doeff_traverse.helpers [try-call :as try_call])


;; ===========================================================================
;; Pure functions — may fail, but contain NO error handling
;; ===========================================================================

(defn extract-feature [item]
  "Extract numeric feature from item. Raises on corrupt data."
  (when (.get item "corrupt")
    (raise (ValueError (+ "corrupt data: " (get item "name")))))
  (* (get item "value") 1.5))

(defn normalize-value [v mean]
  "Center a value by subtracting the mean."
  (round (- v mean) 2))


;; ===========================================================================
;; Pipeline — pure data flow, NO error handling
;;
;; traverse macro: (<- result (traverse (<- x (Iterate items)) body))
;;   - Iterate marks x as the loop variable over items
;;   - Everything after Iterate is the body, applied per-item
;;   - :label is optional, used for per-stage history tracking
;;   - The macro CPS-converts this into Traverse(fn, items) effect
;;   - Handler decides how to execute it (seq/par, fail-fast/run-all)
;;
;; try_call: wraps a plain Python function as a Fail yield site
;;   - If extract-feature raises, try_call performs Fail(exception)
;;   - Handler can Resume(k, substitute_value) to continue
;;   - Without handler, Fail becomes exception → item marked as failed
;;
;; Reduce: fold over the opaque collection returned by traverse
;;   - fnk creates an anonymous kleisli arrow (can perform effects inside)
;;   - Only valid (non-failed) items are folded; failed items are skipped
;;
;; Inspect: extracts per-item value + execution history
;;   - Returns list of ItemResult with .value, .failed, .history
;;   - Used for post-hoc analysis ("why did this item fail?")
;; ===========================================================================

(defk pipeline [items]
  ;; Stage 1: extract features per item
  ;; traverse + Iterate: each item processed independently
  ;; try_call: plain function → Fail effect on exception
  (<- features
    (traverse
      (<- item (Iterate items :label "extract"))
      (<- result (try_call extract-feature item))
      result))

  ;; Stage 2: compute mean via fold
  ;; fold: implicit `acc` (accumulator) and `it` (current item)
  ;; Accumulates (total, count) tuple, only valid items are folded
  (<- #(total count)
    (fold features :init #(0 0)
      #((+ (get acc 0) it) (+ (get acc 1) 1))))
  (setv mean (if (> count 0) (/ total count) 0))

  ;; Stage 3: normalize each feature using mean
  ;; Failed items from stage 1 are automatically skipped
  (<- normalized
    (traverse
      (<- v (Iterate features :label "normalize"))
      (normalize-value v mean)))

  ;; Inspect: extract per-item history for post-hoc analysis
  (<- report (Inspect normalized))
  {"results" normalized "mean" mean "report" report})


;; ===========================================================================
;; Strategy — handler stacks define error recovery policy
;;
;; Handler stack is a list of handlers, innermost first.
;; with-stack wraps the program with each handler via WithHandler.
;;
;; The SAME program runs with DIFFERENT strategies — no code change.
;; ===========================================================================

(defn with-stack [stack program]
  "Wrap program with handlers (innermost first). Returns a runnable program."
  (setv body program)
  (for [h stack]
    (setv body (WithHandler h body)))
  (scheduled body))

;; base-stack: Fail → exception → per-item catch by sequential → item marked failed
;; try_handler: handles Try effect (used internally by sequential handler)
;; fail_handler: converts Fail effect to exception (fail-fast at yield site)
;; sequential: executes Traverse items one by one, isolates per-item failures
(setv base-stack [try_handler fail_handler (sequential)])

;; replace-with-zero: custom Fail handler — injects 0.0 at the yield site
;; When try_call performs Fail, this handler Resume(k, 0.0) → item continues with 0.0
;; The item does NOT fail — it succeeds with 0.0 as its value
(defk replace-with-zero [effect k]
  (if (isinstance effect Fail)
      (return (yield (Resume k 0.0)))
      (yield (Pass effect k))))

;; replace-zero-stack: same as base but with replace-with-zero inside fail_handler
;; replace-with-zero catches Fail BEFORE fail_handler does
(setv replace-zero-stack [try_handler replace-with-zero fail_handler (sequential)])


;; ===========================================================================
;; Data + program construction
;; ===========================================================================

(setv items [{"name" "alice" "value" 10}
             {"name" "bob" "value" 20}
             {"name" "charlie" "value" 30 "corrupt" True}  ; will fail
             {"name" "diana" "value" 40}
             {"name" "eve" "value" 50}])

;; Program is a CONSTANT — not yet executed.
;; (pipeline items) builds the computation graph.
(setv program (pipeline items))


;; ===========================================================================
;; Run — same program, different strategies
;; ===========================================================================

;; base-stack: charlie fails → marked as failed → skipped in stage 2 & 3
(print "=== base stack (skip failed) ===")
(setv out (run (with-stack base-stack program)))
(print "Mean:" (get out "mean"))           ; 45.0 (from 4 valid items)
(print "Valid:" (. (get out "results") valid_values))  ; 4 normalized values
(print "Failed:" (len (. (get out "results") failed_items)))  ; 1

;; replace-zero-stack: charlie's Fail → 0.0 injected → item "succeeds" with 0.0
(print)
(print "=== replace zero ===")
(setv out (run (with-stack replace-zero-stack program)))
(print "Mean:" (get out "mean"))           ; 36.0 (from 5 items, charlie = 0.0)
(print "All:" (. (get out "results") valid_values))  ; 5 normalized values

;; Inspect: per-item execution history — shows what happened at each stage
(print)
(print "=== item history ===")
(setv out (run (with-stack base-stack program)))
(for [item (get out "report")]
  (setv status (if item.failed "FAILED" "OK"))
  (print (+ "  [" (str item.index) "] " status ": value=" (str item.value)))
  (for [h item.history]
    (print (+ "      stage=" (str h.stage) " event=" h.event
              (if h.detail (+ " detail=" h.detail) "")))))
