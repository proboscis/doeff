;;; Example 2: Traverse pipeline — multi-stage batch processing.
;;;
;;; 3-stage pipeline: extract → stats → normalize.
;;; No error handling in logic. Strategy composed externally.
;;; Program is defined once, run with different strategies.

(require doeff-hy.macros [defk defhandler <- fnk])
(import doeff [do :as _doeff-do])
(import doeff [run])

(import doeff_core_effects [try-handler :as try_handler])
(import doeff_core_effects.scheduler [scheduled])

(import doeff_traverse [Collection])
(import doeff_traverse [Fail Traverse Reduce Inspect])
(import doeff_traverse [sequential fail-handler :as fail_handler])
(import doeff_traverse [try-call :as try_call])


;; ---------------------------------------------------------------------------
;; Pure compute functions (may fail)
;; ---------------------------------------------------------------------------

(defn extract-feature [item]
  (when (.get item "corrupt")
    (raise (ValueError (+ "corrupt data: " (get item "name")))))
  (* (get item "value") 1.5))

(defn normalize-value [value mean]
  (round (- value mean) 2))


;; ---------------------------------------------------------------------------
;; Pipeline logic: all kleisli arrows, no error handling
;; ---------------------------------------------------------------------------

(defk extract [item]
  {:pre [(: item dict)]
   :post [(: % float)]}
  (<- result (try_call extract-feature item))
  result)

(defk normalize-item [pair]
  {:pre [(: pair tuple)]
   :post [(: % float)]}
  (normalize-value (get pair 0) (get pair 1)))

(defk pipeline [items]
  {:pre [(: items list)]
   :post [(: % dict)]}
  (<- features (Traverse extract items :label "extract"))
  (<- total (Reduce (fnk [acc x] (+ acc x)) 0.0 features))
  (<- count (Reduce (fnk [acc _] (+ acc 1)) 0 features))
  (val mean (if count (/ total count) 0.0))
  (defk normalize-with-mean [v]
    {:pre [(: v float)]
     :post [(: % float)]}
    (<- result (normalize-item #(v mean)))
    result)
  (<- normalized (Traverse normalize-with-mean features :label "normalize"))
  (<- report (Inspect normalized))
  {"results" normalized "mean" mean "report" report})


;; ---------------------------------------------------------------------------
;; Strategies (handler compositions)
;; ---------------------------------------------------------------------------

(defhandler replace-with-zero
  "Answer every Fail with 0.0; other effects pass to the outer handlers."
  (Fail []
    (resume 0.0)))

;; Base handler stack: sequential + fail_handler + try_handler
(setv base-stack
  [try_handler fail_handler (sequential)])

;; Replace failures with 0.0 instead of marking as failed
(setv replace-zero-stack
  [try_handler replace-with-zero fail_handler (sequential)])

(defn with-stack [stack program]
  "Wrap program with a list of handlers (innermost first). Returns a program."
  (setv body program)
  (for [h stack]
    (setv body (h body)))
  (scheduled body))


;; ---------------------------------------------------------------------------
;; Data
;; ---------------------------------------------------------------------------

(setv items [{"name" "alice" "value" 10}
             {"name" "bob" "value" 20}
             {"name" "charlie" "value" 30 "corrupt" True}
             {"name" "diana" "value" 40}
             {"name" "eve" "value" 50}])

(setv program (pipeline items))


;; ---------------------------------------------------------------------------
;; Run same program with different strategies
;; ---------------------------------------------------------------------------

;; Programs: data + strategy composed, not yet executed
(setv skip-failed-program (with-stack base-stack program))
(setv replace-zero-program (with-stack replace-zero-stack program))

;; run() is the only place where execution happens
(print "=== skip_failed (base stack) ===")
(setv out (run skip-failed-program))
(print "Mean:" (get out "mean"))
(print "Valid results:" (. (get out "results") valid_values))
(print "Failed items:" (len (. (get out "results") failed_items)))

(print)
(print "=== replace_zero ===")
(setv out (run replace-zero-program))
(print "Mean:" (get out "mean"))
(print "All results:" (. (get out "results") valid_values))

(print)
(print "=== item history (base stack) ===")
(setv out (run skip-failed-program))
(for [item (get out "report")]
  (setv status (if item.failed "FAILED" "OK"))
  (print (+ "  [" (str item.index) "] " status ": value=" (str item.value)))
  (for [h item.history]
    (print (+ "      stage=" (str h.stage) " event=" h.event
              (if h.detail (+ " detail=" h.detail) "")))))
