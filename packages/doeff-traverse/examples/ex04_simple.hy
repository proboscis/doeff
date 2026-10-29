;;; Simplified traverse macro test

(require doeff-hy.macros [defk <- traverse])
(import doeff [do :as _doeff-do])
(import doeff [run])

(import doeff_core_effects [try-handler :as try_handler])
(import doeff_core_effects [scheduled])

(import doeff_traverse [Traverse :as _doeff_traverse_Traverse])
(import doeff_traverse [Collection])
(import doeff_traverse [sequential fail-handler :as fail_handler])


(defk double [x]
  {:pre [(: x int)]
   :post [(: % int)]}
  (* x 2))

(defk pipeline [items]
  {:pre [(: items list)]
   :post [(: % Collection)]}
  (<- results
    (traverse
      (<- x (Iterate items))
      (<- y (double x))
      y))
  results)

(setv program (pipeline [1 2 3]))
(setv body (try_handler program))
(setv body (fail_handler body))
(setv body (sequential body))
(setv body (scheduled body))

(setv result (run body))
(print "Result:" result)
(print "Valid:" (. result valid_values))
(print "Failed:" (. result failed_items))
(for [item (. result all_items)]
  (print "  " item.index item.value item.failed item.history))
