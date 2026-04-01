;;; Example 7: Realistic rewrite of proboscis-ema news pipeline.
;;;
;;; Original (Python, news/pipeline.py):
;;;   - throttled_gather with semaphore (wrap_with_semaphore + async_gather)
;;;   - Try() per item → list[Result[T]]
;;;   - _normalize_movement_result + zip + filter
;;;   - PipelineResult with successful_price_movements / failed_price_movements
;;;   - Manual error classification (symbol_not_found, market_closed, etc.)
;;;
;;; Rewrite (Hy, doeff-traverse):
;;;   - traverse replaces throttled_gather + Try wrapping
;;;   - Reduce replaces manual success/failure counting
;;;   - Zip replaces manual zip + normalize
;;;   - Collection tracks per-item failure automatically
;;;   - No error handling in pipeline logic
;;;
;;; This example uses mock effects for demonstration.

(require doeff-hy.macros [defk <- traverse fnk])
(import doeff [do :as _doeff-do])
(import doeff [run EffectBase])
(import doeff.program [WithHandler Resume Pass])

(import doeff_core_effects [try-handler :as try_handler])
(import doeff_core_effects.scheduler [scheduled])

(import doeff_traverse [Traverse :as _doeff_traverse_Traverse])
(import doeff_traverse.effects [Fail Reduce Zip Inspect])
(import doeff_traverse.handlers [sequential parallel fail-handler :as fail_handler])
(import doeff_traverse.helpers [try-call :as try_call])


;; ===========================================================================
;; Effects — domain operations, no implementation
;; ===========================================================================

(defclass IndexNews [EffectBase]
  "Index a news event for signal generation."
  (defn __init__ [self event]
    (.__init__ (super))
    (setv self.event event)))

(defclass GenerateSignal [EffectBase]
  "Generate trading signals from an indexed event."
  (defn __init__ [self indexed-event]
    (.__init__ (super))
    (setv self.indexed-event indexed-event)))

(defclass FetchPrice [EffectBase]
  "Fetch price series for a signal's symbol."
  (defn __init__ [self signal]
    (.__init__ (super))
    (setv self.signal signal)))


;; ===========================================================================
;; Pipeline logic — NO error handling, NO throttle, NO Try, NO Result
;;
;; Original proboscis-ema had:
;;   - wrap_with_semaphore() per task
;;   - Try() wrapping each computation
;;   - _normalize_movement_result() to convert exceptions
;;   - zip(signals, raw_movements) + filter by is_ok/is_err
;;   - PipelineResult with success/failure lists
;;
;; All of that is gone. Just data flow.
;; ===========================================================================

(defk index-event [event]
  "Index one news event."
  (<- result (IndexNews event))
  result)

(defk generate-signal [indexed]
  "Generate signal from one indexed event."
  (<- result (GenerateSignal indexed))
  result)

(defk fetch-price [signal]
  "Fetch price data for one signal."
  (<- result (FetchPrice signal))
  result)

(defk news-pipeline [events]
  "Full news pipeline: index → signals → prices → report.

   Original was ~100 lines with throttled_gather, Try, zip, filter.
   This is ~10 lines."
  ;; Stage 1: index each event
  (<- indexed
    (traverse
      (<- event (Iterate events :label "index"))
      (<- result (index-event event))
      result))

  ;; Stage 2: generate signals per indexed event
  (<- signals
    (traverse
      (<- idx (Iterate indexed :label "signal"))
      (<- sig (generate-signal idx))
      sig))

  ;; Stage 3: fetch prices per signal
  (<- prices
    (traverse
      (<- sig (Iterate signals :label "price"))
      (<- price (fetch-price sig))
      price))

  ;; Stage 4: zip signals with prices (failure union)
  (<- paired (Zip signals prices))

  ;; Stage 5: count successes via fold (Reduce only sees valid items)
  (<- success-count
    (Reduce (fnk [acc _] (+ acc 1)) 0 paired))
  ;; Total - success = failed
  (setv fail-count (- (len events) success-count))

  ;; Inspect for post-hoc analysis
  (<- report (Inspect paired))

  {"paired" paired
   "success" success-count
   "fail" fail-count
   "report" report})


;; ===========================================================================
;; Mock backends
;; ===========================================================================

;; Simulate: some symbols not found, some markets closed
(setv _mock-prices
  {"AAPL" 150.0
   "GOOGL" 2800.0
   ;; BADCO → symbol not found
   ;; CLOSED → market closed
   "MSFT" 300.0})

(defk mock-backend [effect k]
  "Mock handler for all domain effects."
  (cond
    (isinstance effect IndexNews)
      (return (yield (Resume k {"event" effect.event "indexed" True})))

    (isinstance effect GenerateSignal)
      (let [evt (get effect.indexed-event "event")]
        (return (yield (Resume k {"symbol" evt "time" "2024-01-01"}))))

    (isinstance effect FetchPrice)
      (let [symbol (get effect.signal "symbol")]
        (cond
          (= symbol "BADCO")
            (do
              (yield (Fail (ValueError (+ "symbol not found: " symbol))
                           :symbol symbol))
              ;; if Fail handler resumes (e.g., normalize-to-none), this continues
              (return (yield (Resume k None))))
          (= symbol "CLOSED")
            (do
              (yield (Fail (ValueError (+ "market closed for: " symbol))
                           :symbol symbol))
              (return (yield (Resume k None))))
          True
            (return (yield (Resume k (get _mock-prices symbol))))))

    True
      (yield (Pass effect k))))


;; ===========================================================================
;; Run
;; ===========================================================================

(defn with-stack [stack program]
  (setv body program)
  (for [h stack]
    (setv body (WithHandler h body)))
  (scheduled body))

(setv events ["AAPL" "BADCO" "GOOGL" "CLOSED" "MSFT"])
(setv program (news-pipeline events))

;; Sequential + fail-handler (BADCO and CLOSED become failed items)
(print "=== sequential (failed items skipped) ===")
(setv out (run (with-stack
  [try_handler mock-backend fail_handler (sequential)]
  program)))
(print "Success:" (get out "success"))
(print "Failed:" (get out "fail"))
(print)

;; Parallel + same strategy
(print "=== parallel(3) (same result, faster) ===")
(setv out (run (with-stack
  [try_handler mock-backend fail_handler (parallel 3)]
  program)))
(print "Success:" (get out "success"))
(print "Failed:" (get out "fail"))
(print)

;; Inspect: per-item history across all stages
(import traceback :as tb)

(print "=== item history ===")
(for [item (get out "report")]
  (setv status (if item.failed "FAILED" "OK"))
  (print (+ "  [" (str item.index) "] " status ": " (str item.value)))
  (for [h item.history]
    (setv detail (if h.detail (+ " — " h.detail) ""))
    (setv stage (if h.stage (+ h.stage ": ") ""))
    (print (+ "      " stage h.event detail)))
  ;; Show traceback for failed items
  (when (and item.failed (isinstance item.value BaseException))
    (print "      --- traceback ---")
    (for [line (tb.format_exception item.value)]
      (print (+ "      " (.rstrip line))))))
