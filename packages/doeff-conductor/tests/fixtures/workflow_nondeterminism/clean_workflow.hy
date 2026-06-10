;;; Clean deterministic workflow glue: loads without diagnostics.

(require doeff-hy.conductor [defworkflow])
(import doeff_conductor.dsl [artifact])


(defn build-prompt [facts params]
  (.format "{} at {} with seed {}"
           (get params "task")
           (get facts "timestamp")
           (get facts "seed")))


(defworkflow clean-workflow
  :params {}
  :roles {}
  (artifact "ok"))

(setv WORKFLOW clean-workflow)
