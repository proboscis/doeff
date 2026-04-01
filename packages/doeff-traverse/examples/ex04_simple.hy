;;; Simplified traverse macro test

(require doeff-hy.macros [defk <- traverse])
(import doeff [do :as _doeff-do])
(import doeff [run])
(import doeff.program [WithHandler])

(import doeff_core_effects [try-handler :as try_handler])
(import doeff_core_effects.scheduler [scheduled])

(import doeff_traverse [Traverse :as _doeff_traverse_Traverse])
(import doeff_traverse.handlers [sequential fail-handler :as fail_handler])


(defk double [x]
  (* x 2))

(defk pipeline [items]
  (<- results
    (traverse
      (<- x (Iterate items))
      (<- y (double x))
      y))
  results)

(setv program (pipeline [1 2 3]))
(setv body (WithHandler try_handler program))
(setv body (WithHandler fail_handler body))
(setv body (WithHandler (sequential) body))
(setv body (scheduled body))

(setv result (run body))
(print "Result:" result)
(print "Valid:" (. result valid_values))
(print "Failed:" (. result failed_items))
(for [item (. result all_items)]
  (print "  " item.index item.value item.failed item.history))
