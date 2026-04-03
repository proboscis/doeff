;;; Example 9: for/do comprehension with From, When, SortBy, Take.
;;;
;;; Demonstrates the new collection comprehension syntax:
;;;   - From: generator bind (replaces Iterate)
;;;   - When: guard (filters items, marks as skipped)
;;;   - When + !: effectful predicate via bang inline bind
;;;   - SortBy / Take: collection effects
;;;
;;; Same pipeline, SQL-like reading:
;;;   SELECT process(item) FROM items WHERE predicate(item)

(require doeff-hy.macros [defk <- for/do fold])
(import doeff [do :as _doeff-do])
(import doeff [run])
(import doeff.program [WithHandler])

(import doeff_core_effects [try-handler :as try_handler])
(import doeff_core_effects.scheduler [scheduled])

(import doeff_traverse [Traverse :as _doeff_traverse_Traverse])
(import doeff_traverse [Skip :as _doeff_traverse_Skip])
(import doeff_traverse.effects [Inspect SortBy Take])
(import doeff_traverse.handlers [sequential fail-handler :as fail_handler])
(import doeff_traverse.helpers [try-call :as try_call])


;; ===========================================================================
;; Data
;; ===========================================================================

(setv items [{"name" "alice"   "score" 85  "active" True}
             {"name" "bob"     "score" 42  "active" False}   ; filtered by When
             {"name" "charlie" "score" 91  "active" True}
             {"name" "diana"   "score" 67  "active" True}
             {"name" "eve"     "score" 38  "active" False}   ; filtered by When
             {"name" "frank"   "score" 73  "active" True}])


;; ===========================================================================
;; Pipeline using for/do + When
;; ===========================================================================

(defk pipeline [items]
  ;; Stage 1: for/do with From and When — filter + transform in one comprehension
  (<- results
    (for/do
      (<- item (From items :label "process"))
      (When (get item "active"))                 ;; guard: skip inactive
      (<- score (try_call (fn [i] (* (get i "score") 1.1)) item))
      {"name" (get item "name") "adjusted" (round score 1)}))

  ;; Stage 2: sort by adjusted score descending
  (<- sorted (SortBy (fn [x] (get x "adjusted")) results :reverse True))

  ;; Stage 3: take top 3
  (<- top3 (Take 3 sorted))

  ;; Inspect for history
  (<- report (Inspect results))

  {"top3" top3 "report" report})


;; ===========================================================================
;; Pipeline 2: effectful predicate via bang (!)
;; ===========================================================================

;; Effectful predicate — could perform effects (e.g., check DB, call API)
(defk check-eligible [item]
  ;; In real code this might be an effect like (CheckEligibility item)
  (and (get item "active") (>= (get item "score") 50)))

(defk pipeline-bang [items]
  ;; When + bang: effectful predicate inlined
  (<- results
    (for/do
      (<- item (From items :label "eligible"))
      (When (! (check-eligible item)))           ;; bang: effectful guard
      (<- score (try_call (fn [i] (* (get i "score") 1.1)) item))
      {"name" (get item "name") "adjusted" (round score 1)}))
  results)


;; ===========================================================================
;; Run
;; ===========================================================================

(defn with-stack [stack program]
  (setv body program)
  (for [h stack]
    (setv body (WithHandler h body)))
  (scheduled body))

(setv stack [try_handler fail_handler (sequential)])

(print "=== for/do + When + SortBy + Take ===")
(setv out (run (with-stack stack (pipeline items))))

(print "Top 3:")
(for [v (. (get out "top3") valid_values)]
  (print (+ "  " (get v "name") ": " (str (get v "adjusted")))))

(print)
(print "Item history:")
(for [item (get out "report")]
  (setv status (cond item.failed "SKIP" True "OK"))
  (print (+ "  [" (str item.index) "] " status ": " (str item.value)))
  (for [h item.history]
    (print (+ "      " (if h.stage (+ h.stage ": ") "") h.event))))

;; --- Pipeline 2: bang syntax ---
(print)
(print "=== for/do + When + bang (effectful predicate) ===")
(setv out2 (run (with-stack stack (pipeline-bang items))))
(print "Eligible (active + score >= 50):")
(for [v (. out2 valid_values)]
  (print (+ "  " (get v "name") ": " (str (get v "adjusted")))))
