(defk run-it [request budget]
  "要求 1 つを実体化するため。" ;; 註
  (<- outcome (outcome-of request 42))
  (when (is outcome None)
    (return budget)))
