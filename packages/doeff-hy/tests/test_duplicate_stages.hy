;;; Test: duplicate bind names + index-based stage-of
(require doeff-hy.macros [defk defp <-])
(import doeff [do :as _doeff-do])
(import doeff [EffectBase run WithHandler Resume Pass Pure])
(import doeff_core_effects.scheduler [scheduled])
(import dataclasses [dataclass])

(defclass [(dataclass :frozen True)] FetchRaw [EffectBase])
(defclass [(dataclass :frozen True)] Enrich [EffectBase]
  #^ object data)

;; Pipeline with duplicate bind name
(defk my-pipeline []
  {:pre [] :post [(: % dict)]}
  (<- data (FetchRaw))
  (<- data (Enrich :data data))
  (<- signal {"signal" data})
  signal)

;; Mock handlers
(defk mock [effect k]
  {:pre [(: effect object) (: k object)] :post [(: % object)]}
  (cond
    (isinstance effect FetchRaw)
      (yield (Resume k "raw-data"))
    (isinstance effect Enrich)
      (yield (Resume k (+ "enriched:" (str effect.data))))
    True (yield (Pass effect k))))

(defn run-mock [p] (run (scheduled (WithHandler mock p))))

;; Tests
(import doeff_hy.sexpr [list-stages stage-of])

(print "=== list-stages ===")
(setv stages (list-stages my-pipeline))
(for [s stages] (print s))
(assert (= (len stages) 3))
(assert (= (get (get stages 0) "name") "data"))
(assert (= (get (get stages 1) "name") "data"))
(assert (= (get (get stages 2) "name") "signal"))
(print "PASS")

(print)
(print "=== stage-of by name 'data' → LAST occurrence (enriched) ===")
(setv p (stage-of my-pipeline "data"))
(print "Result:" (run-mock p))
(assert (= (run-mock p) "enriched:raw-data"))
(print "PASS")

(print)
(print "=== stage-of by index 0 → first 'data' (raw) ===")
(setv p0 (stage-of my-pipeline 0))
(print "Result:" (run-mock p0))
(assert (= (run-mock p0) "raw-data"))
(print "PASS")

(print)
(print "=== stage-of by index 1 → second 'data' (enriched) ===")
(setv p1 (stage-of my-pipeline 1))
(print "Result:" (run-mock p1))
(assert (= (run-mock p1) "enriched:raw-data"))
(print "PASS")

(print)
(print "All duplicate-stage tests passed!")
