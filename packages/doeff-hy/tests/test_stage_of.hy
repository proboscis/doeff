;;; Test stage-of: partial pipeline reuse
(require doeff-hy.macros [defk defp <-])
(import doeff [do :as _doeff-do])
(import doeff [EffectBase run WithHandler Resume Pass])
(import doeff_core_effects.scheduler [scheduled])
(import dataclasses [dataclass])

;; Effects
(defclass [(dataclass :frozen True)] FetchOhlc [EffectBase]
  #^ str ticker)

(defclass [(dataclass :frozen True)] FetchNews [EffectBase]
  #^ str day)

;; Kleisli functions
(defk merge-data [ohlc news]
  {:pre [(: ohlc object) (: news object)] :post [(: % dict)]}
  {"ohlc" ohlc "news" news})

(defk compute-signal [data]
  {:pre [(: data dict)] :post [(: % dict)]}
  {"signal" "bullish" "data" data})

(defk build-report [signal]
  {:pre [(: signal object)] :post [(: % str)]}
  (+ "Report: " (str signal)))

;; A "big" defk pipeline — as AI would write it
(defk full-pipeline []
  {:pre [] :post [(: % str)]}
  (<- ohlc (FetchOhlc :ticker "7203.T"))
  (<- news (FetchNews :day "2025-11-10"))
  (<- data (merge-data ohlc news))
  (<- signal (compute-signal data))
  (<- report (build-report signal))
  report)

;; defp that calls it
(defp p-full {:post []}
  (<- result (full-pipeline))
  result)


;; === Mock handlers ===
(defk mock-ohlc [effect k]
  {:pre [(: effect object) (: k object)] :post [(: % object)]}
  (if (isinstance effect FetchOhlc)
    (yield (Resume k [100 101 102]))
    (yield (Pass effect k))))

(defk mock-news [effect k]
  {:pre [(: effect object) (: k object)] :post [(: % object)]}
  (if (isinstance effect FetchNews)
    (yield (Resume k ["article-1" "article-2"]))
    (yield (Pass effect k))))

(defn run-with-mocks [program]
  (run (scheduled
    (WithHandler mock-ohlc
      (WithHandler mock-news program)))))


;; === Test list-stages ===
(import doeff_hy.sexpr [list-stages stage-of])

(print "=== list-stages (full-pipeline) ===")
(setv stages (list-stages full-pipeline))
(print stages)
(setv stage-names (lfor s stages (get s "name")))
(assert (= stage-names ["ohlc" "news" "data" "signal" "report"]))
(print "PASS")


;; === Test stage-of ===
(print)
(print "=== stage-of full-pipeline 'ohlc' ===")
(setv p-ohlc-only (stage-of full-pipeline "ohlc"))
(setv r (run-with-mocks p-ohlc-only))
(print "Result:" r)
(assert (= r [100 101 102]))
(print "PASS")

(print)
(print "=== stage-of full-pipeline 'data' ===")
(setv p-data (stage-of full-pipeline "data"))
(setv r (run-with-mocks p-data))
(print "Result:" r)
(assert (= (get r "ohlc") [100 101 102]))
(assert (= (get r "news") ["article-1" "article-2"]))
(print "PASS")

(print)
(print "=== stage-of full-pipeline 'signal' ===")
(setv p-sig (stage-of full-pipeline "signal"))
(setv r (run-with-mocks p-sig))
(print "Result:" r)
(assert (= (get r "signal") "bullish"))
(print "PASS")

(print)
(print "=== Reuse: new program using stage-of ===")
;; Build a new program that taps into 'data' stage of full-pipeline
(defk alternative-analysis [data]
  {:pre [(: data dict)] :post [(: % str)]}
  (+ "Alt analysis on " (str (len (get data "ohlc"))) " prices"))

(defp p-alt {:post []}
  (<- data (stage-of full-pipeline "data"))
  (<- result (alternative-analysis data))
  result)

(setv r (run-with-mocks p-alt))
(print "p-alt result:" r)
(assert (in "3 prices" r))
(print "PASS")

(print)
(print "All stage-of tests passed!")
