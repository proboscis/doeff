(require doeff-hy.macros [defk <-])
(import doeff [do :as _doeff-do])
(import doeff [EffectBase])
(import dataclasses [dataclass field])

;; Define test effects
(defclass [(dataclass :frozen True)] FetchNews [EffectBase]
  #^ str day)

(defclass [(dataclass :frozen True)] LLMQuery [EffectBase]
  #^ str prompt)

;; Kleisli functions
(defk fetch-and-filter [day]
  {:pre [(: day str)] :post [(: % object)]}
  (<- news (FetchNews :day day))
  news)

(defk analyze [day]
  {:pre [(: day str)] :post [(: % object)]}
  (<- data (fetch-and-filter day))
  (<- result (LLMQuery :prompt "analyze"))
  result)

;; Run tests
(defn main []
  (import doeff_hy.sexpr [body-of args-of name-of collect-effects
                          effect-tree print-effect-tree assert-handlers
                          _get-module-globals _walk-calls _call-head classify-call])

  ;; Debug: check globals resolution
  (setv g (_get-module-globals analyze))
  (print "=== debug: globals keys with Fetch ===" )
  (print (lfor k (.keys g) :if (in "etch" (str k)) k))
  (print "FetchNews type:" (type FetchNews))
  (print "FetchNews in caller globals:" (in "FetchNews" (globals)))
  (print)

  (print "=== body-of ===")
  (print (body-of analyze))
  (print)

  (print "=== args-of ===")
  (print (args-of analyze))
  (print)

  (print "=== name-of ===")
  (print (name-of analyze))
  (print)

  (print "=== collect-effects (analyze) ===")
  (print (collect-effects analyze))
  (print)

  (print "=== collect-effects (fetch-and-filter) ===")
  (print (collect-effects fetch-and-filter))
  (print)

  (print "=== effect-tree ===")
  (print-effect-tree (effect-tree analyze))
  (print)

  (print "=== assert-handlers (should pass) ===")
  (assert-handlers analyze #{"FetchNews" "LLMQuery"})
  (print "PASS")
  (print)

  (print "=== assert-handlers (should fail) ===")
  (try
    (assert-handlers analyze #{"FetchNews"})  ;; missing LLMQuery
    (print "FAIL — should have raised")
    (except [e AssertionError]
      (print (+ "PASS — caught: " (str e))))))

(main)
