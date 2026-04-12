;;; Test defpipeline macro
(require doeff-hy.macros [defk defp <- defpipeline])
(import doeff [do :as _doeff-do])
(import doeff [EffectBase K run WithHandler Resume Pass])
(import doeff_core_effects.scheduler [scheduled])
(import dataclasses [dataclass])

;; Test effects
(defclass [(dataclass :frozen True)] FetchOhlc [EffectBase]
  #^ str ticker)

(defclass [(dataclass :frozen True)] FetchNews [EffectBase]
  #^ str day)

(defclass [(dataclass :frozen True)] LLMRank [EffectBase]
  #^ str prompt)

;; Kleisli functions used by pipeline
(defk merge-data [ohlc news]
  {:pre [(: ohlc list) (: news list)] :post [(: % dict)]}
  {"ohlc" ohlc "news" news})

(defk compute-signal [data model]
  {:pre [(: data dict) (: model str)] :post [(: % dict)]}
  {"signal" "bullish" "data" data "model" model})

(defk build-report [signal]
  {:pre [(: signal dict)] :post [(: % str)]}
  (+ "Report: " (str signal)))

;; === The pipeline ===
;; Each stage expr is auto-wrapped with (<- _stage expr) by the macro.
;; Both effect constructors and kleisli calls work without explicit <-.
(defpipeline daily-cllm
  [ohlc]   (FetchOhlc :ticker "7203.T")
  [news]   (FetchNews :day "2025-11-10")
  [data]   (merge-data ohlc news)
  [signal] (compute-signal data "gpt-5")
  [report] (build-report signal))


;; === Part 1: Check that defp constants were generated ===
(print "=== Part 1: Generated defp constants ===")
(print "p-daily-cllm-ohlc:" (type p-daily-cllm-ohlc))
(print "p-daily-cllm-news:" (type p-daily-cllm-news))
(print "p-daily-cllm-data:" (type p-daily-cllm-data))
(print "p-daily-cllm-signal:" (type p-daily-cllm-signal))
(print "p-daily-cllm-report:" (type p-daily-cllm-report))
(print "p-daily-cllm (alias):" (type p-daily-cllm))
(assert (is p-daily-cllm p-daily-cllm-report) "alias should be same object")
(print "PASS: all 5 stages + alias created")

;; === Part 2: Run individual stages ===
(print)
(print "=== Part 2: Run individual stages ===")

;; Mock handlers — return type varies by effect chain
(setv HandlerResult object)

(defk mock-ohlc [effect k]
  {:pre [(: effect EffectBase) (: k K)] :post [(: % HandlerResult)]}
  (if (isinstance effect FetchOhlc)
    (yield (Resume k [100 101 102]))
    (yield (Pass effect k))))

(defk mock-news [effect k]
  {:pre [(: effect EffectBase) (: k K)] :post [(: % HandlerResult)]}
  (if (isinstance effect FetchNews)
    (yield (Resume k ["article-1" "article-2"]))
    (yield (Pass effect k))))

(defk mock-llm [effect k]
  {:pre [(: effect EffectBase) (: k K)] :post [(: % HandlerResult)]}
  (if (isinstance effect LLMRank)
    (yield (Resume k {"rank" "bullish"}))
    (yield (Pass effect k))))

(defn run-with-mocks [program]
  (run (scheduled
    (WithHandler mock-ohlc
      (WithHandler mock-news
        (WithHandler mock-llm program))))))

;; Run just ohlc stage
(setv ohlc-result (run-with-mocks p-daily-cllm-ohlc))
(print "ohlc-only:" ohlc-result)
(assert (= ohlc-result [100 101 102]))
(print "PASS")

;; Run just news stage
(setv news-result (run-with-mocks p-daily-cllm-news))
(print "news-only:" news-result)
(assert (= news-result ["article-1" "article-2"]))
(print "PASS")

;; Run up to data stage (should fetch both ohlc + news)
(setv data-result (run-with-mocks p-daily-cllm-data))
(print "data-stage:" data-result)
(assert (= (get data-result "ohlc") [100 101 102]))
(assert (= (get data-result "news") ["article-1" "article-2"]))
(print "PASS")

;; Run full pipeline
(setv full-result (run-with-mocks p-daily-cllm))
(print "full-pipeline:" full-result)
(assert (in "Report:" full-result))
(print "PASS")

;; === Part 3: S-expr analysis ===
(print)
(print "=== Part 3: S-expr analysis on pipeline stages ===")
(import doeff_hy.sexpr [collect-effects print-effect-tree effect-tree])
(setv KNOWN #{"FetchOhlc" "FetchNews" "LLMRank"})

(print "effects of p-daily-cllm-ohlc:" (collect-effects p-daily-cllm-ohlc :extra-effects KNOWN))
(print "effects of p-daily-cllm-data:" (collect-effects p-daily-cllm-data :extra-effects KNOWN))

(print)
(print "All tests passed!")
