;;; Example 3: Zip — combining results from independent computations.
;;;
;;; Two independent Traversals + Zip.
;;; Different items may fail in different stages.
;;; Zip produces failure union.

(require doeff-hy.macros [defk <- do!])
(import doeff [do :as _doeff-do])
(import doeff [run])
(import doeff.program [WithHandler])

(import doeff_core_effects [try-handler :as try_handler])
(import doeff_core_effects.scheduler [scheduled])

(import doeff_traverse.effects [Traverse Reduce Zip Inspect])
(import doeff_traverse.handlers [sequential fail-handler :as fail_handler])
(import doeff_traverse.helpers [try-call :as try_call])


;; ---------------------------------------------------------------------------
;; Simulated computations (may fail)
;; ---------------------------------------------------------------------------

(defn compute-embedding [text]
  (when (in "error_embed" text)
    (raise (ValueError (+ "embedding failed for: " text))))
  [(* (len text) 0.1) (* (len text) 0.2)])

(defn generate-summary [text]
  (when (in "error_summary" text)
    (raise (ValueError (+ "summary failed for: " text))))
  (+ "summary of '" text "'"))

(defn score-pair [pair]
  (setv #(embedding summary) pair)
  (round (* (len summary) (get embedding 0)) 2))


;; ---------------------------------------------------------------------------
;; Pipeline: all kleisli arrows
;; ---------------------------------------------------------------------------

(defk embed [item]
  (<- result (try_call compute-embedding item))
  result)

(defk summarize [item]
  (<- result (try_call generate-summary item))
  result)

(defk score [pair]
  (score-pair pair))

(defk compute-avg [values]
  (if values
      (round (/ (sum values) (len values)) 2)
      0))

(defk multi-source-pipeline [items]
  ;; Two independent traversals
  (<- embeddings (Traverse embed items))
  (<- summaries (Traverse summarize items))

  ;; Zip: item-indexed join. Failed in either → failed in result.
  (<- combined (Zip embeddings summaries))

  ;; Score valid pairs
  (<- scores (Traverse score combined))

  ;; Aggregate
  (<- avg-score (Reduce compute-avg scores))

  (<- report (Inspect scores))
  {"avg_score" avg-score "report" report})


;; ---------------------------------------------------------------------------
;; Run
;; ---------------------------------------------------------------------------

(defn run-it [program]
  (run (scheduled
    (WithHandler (sequential)
      (WithHandler fail_handler
        (WithHandler try_handler program))))))

(setv items ["hello world"
             "error_embed here"
             "good data"
             "error_summary here"
             "more good data"])

(setv out (run-it (multi-source-pipeline items)))

(print "Average score:" (get out "avg_score"))
(print)
(print "Per-item report:")
(for [item (get out "report")]
  (setv status (if item.failed "FAILED" "OK"))
  (print " " (+ "[" (str item.index) "] " status ": value=" (str item.value))))
