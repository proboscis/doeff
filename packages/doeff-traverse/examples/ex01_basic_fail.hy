;;; Example 1: Fail effect basics.
;;;
;;; Same logic, different error strategies via handler composition.
;;; No error handling in the logic itself.

(require doeff-hy.macros [defk defhandler <-])
(import doeff [do :as _doeff-do])
(import doeff [run])

(import doeff_core_effects [try-handler :as try_handler])
(import doeff_core_effects.scheduler [scheduled])

(import doeff_traverse [Fail])
(import doeff_traverse [fail-handler :as fail_handler normalize-to-none :as normalize_to_none])
(import doeff_traverse [try-call :as try_call])


;; ---------------------------------------------------------------------------
;; Logic: no error handling here
;; ---------------------------------------------------------------------------

(defn parse-score [raw]
  "Plain Python function that may fail."
  (int (.strip raw)))

(defk process-scores [raw-scores]
  "Parse and sum scores. Errors are Fail effects."
  {:pre [(: raw-scores list)]
   :post [(: % int)]}
  (var total 0)
  (for [raw raw-scores]
    (<- score (try_call parse-score raw))
    (when (is-not score None)
      (:= total (+ total score))))
  total)


;; ---------------------------------------------------------------------------
;; Strategies
;; ---------------------------------------------------------------------------

(defn run-fail-fast [program]
  (run (scheduled
    (fail_handler (try_handler program)))))

(defn run-normalize [program]
  (run (scheduled
    (fail_handler (normalize_to_none (try_handler program))))))

(defhandler replace-with-zero
  "Answer every Fail with 0; other effects pass to the outer handlers."
  (Fail []
    (resume 0)))

(defn run-replace-zero [program]
  (run (scheduled
    (fail_handler (replace-with-zero (try_handler program))))))


;; ---------------------------------------------------------------------------
;; Run
;; ---------------------------------------------------------------------------

(setv data ["10" "20" "bad" "30" "nope"])
(setv program (process-scores data))

(print "=== Strategy 1: fail-fast ===")
(try
  (print "Result:" (run-fail-fast program))
  (except [e ValueError]
    (print "Failed:" (str e))))

(print)
(print "=== Strategy 2: normalize (skip bad) ===")
(print "Result:" (run-normalize program))

(print)
(print "=== Strategy 3: replace with 0 ===")
(print "Result:" (run-replace-zero program))
