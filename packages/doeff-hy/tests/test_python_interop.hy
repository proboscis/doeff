;;; Test: what happens when defk calls a Python @do function?
(require doeff-hy.macros [defk <-])
(import doeff [do :as _doeff-do])
(import tests.multimod.effects [FetchPrice])
(import tests.multimod.python_layer [python-risk-check ComputeRisk])
(import doeff_hy.sexpr [body-of collect-effects effect-tree
                        print-effect-tree classify-call])

;; Hy defk that calls a Python @do function
(defk pipeline-with-python [ticker]
  {:pre [(: ticker str)] :post [(: % dict)]}
  (<- prices (FetchPrice :ticker ticker))
  (<- risk (python-risk-check "my-portfolio"))  ;; ← Python @do, no S-expr
  {"prices" prices "risk" risk})

(print "=== body-of ===")
(for [form (body-of pipeline-with-python)]
  (print (str form)))

(print)
(print "=== classify python-risk-check ===")
(setv g (. pipeline-with-python __globals__))
;; Use module globals instead
(import sys)
(setv g (vars (get sys.modules (. pipeline-with-python __module__))))
(print "has __doeff_body__:" (hasattr python-risk-check "__doeff_body__"))
(print "classify:" (classify-call "python-risk-check" g))

(print)
(print "=== collect-effects ===")
(setv effects (collect-effects pipeline-with-python))
(print "Found:" effects)

(print)
(print "=== effect-tree ===")
(print-effect-tree (effect-tree pipeline-with-python))

(print)
(print "=== What's missing ===")
(print "ComputeRisk is INSIDE python_risk_check but NOT detected")
(print "because Python @do functions have no __doeff_body__.")
(print)
(if (in "ComputeRisk" effects)
  (print "UNEXPECTED: ComputeRisk was found somehow")
  (print "CONFIRMED: ComputeRisk is invisible — Python @do is opaque"))
