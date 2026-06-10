(require doeff-hy.conductor [defworkflow])
(import doeff_conductor.dsl [artifact])


(defn build-prompt [facts params]
  (+ (get params "task") " with supplied facts"))


(defworkflow clean-workflow
  :params {}
  :roles {}
  (artifact "ok"))


(setv WORKFLOW clean-workflow)
