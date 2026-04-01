;;; Example 6: Realistic rewrite of mediagen parse_discussion pipeline.
;;;
;;; Original (Python, shared.py):
;;;   - 3 Spawn+Gather patterns (~30 lines boilerplate)
;;;   - _llm_query_with_retry used 6+ times (~47 lines)
;;;   - Try + manual error check + conditional retry everywhere
;;;
;;; Rewrite (Hy, doeff-traverse):
;;;   - traverse macro replaces Spawn+Gather
;;;   - retry handler replaces _llm_query_with_retry
;;;   - No error handling in pipeline logic
;;;
;;; This example uses mock LLM calls for demonstration.

(require doeff-hy.macros [defk <- traverse fnk])
(import doeff [do :as _doeff-do])
(import doeff [run EffectBase])
(import doeff.program [WithHandler Resume Pass])

(import doeff_core_effects [try-handler :as try_handler Ask slog])
(import doeff_core_effects.handlers [lazy-ask :as lazy_ask])
(import doeff_core_effects.scheduler [scheduled])

(import doeff_traverse [Traverse :as _doeff_traverse_Traverse])
(import doeff_traverse.effects [Fail Reduce Inspect])
(import doeff_traverse.handlers [sequential parallel fail-handler :as fail_handler])


;; ===========================================================================
;; Effects — what the pipeline declares, not how it's executed
;; ===========================================================================

(defclass LLMStructuredQuery [EffectBase]
  "Request a structured LLM response."
  (defn __init__ [self * messages response-format model [temperature 0.7] [max-tokens 32768]]
    (.__init__ (super))
    (setv self.messages messages
          self.response-format response-format
          self.model model
          self.temperature temperature
          self.max-tokens max-tokens)))


;; ===========================================================================
;; Domain types (simplified for demo)
;; ===========================================================================

(defclass SpeakerProfile []
  (defn __init__ [self name role]
    (setv self.name name self.role role))
  (defn __repr__ [self] (+ "Speaker(" self.name ")")))

(defclass Opinion []
  (defn __init__ [self speaker text]
    (setv self.speaker speaker self.text text))
  (defn __repr__ [self] (+ "Opinion(" self.speaker ": " self.text ")")))


;; ===========================================================================
;; Pipeline logic — NO error handling, NO Spawn/Gather, NO retry
;; ===========================================================================

(defk list-speakers [transcript]
  "Stage 1a: list all speakers (single LLM call)."
  (<- model (Ask "s1a_model"))
  (<- result (LLMStructuredQuery
               :messages [{"role" "user" "content" (+ "List speakers in: " transcript)}]
               :response-format "SpeakerList"
               :model model
               :temperature 0.2))
  result)

(defk assign-spans-one [transcript all-speakers model speaker-name]
  "Stage 1b worker: assign spans for one speaker."
  (<- result (LLMStructuredQuery
               :messages [{"role" "user"
                           "content" (+ "Assign spans for " speaker-name
                                        " in: " transcript
                                        " (all speakers: " all-speakers ")")}]
               :response-format "Speaker"
               :model model
               :temperature 0.2))
  (SpeakerProfile speaker-name result))

(defk assign-speaker-spans [transcript speaker-names]
  "Stage 1b: assign spans per speaker (traverse — handler decides seq/par)."
  (<- model (Ask "s1b_model"))
  (setv all-speakers (.join ", " speaker-names))
  (<- speakers
    (traverse
      (<- name (Iterate speaker-names :label "s1b_assign_spans"))
      (<- speaker (assign-spans-one transcript all-speakers model name))
      speaker))
  speakers)

(defk extract-opinions-one [transcript model speaker]
  "Stage 2 worker: extract opinions for one speaker."
  (<- result (LLMStructuredQuery
               :messages [{"role" "user"
                           "content" (+ "Extract opinions for " speaker.name
                                        " in: " transcript)}]
               :response-format "OpinionMap"
               :model model
               :temperature 0.2))
  (Opinion speaker.name result))

(defk extract-opinions [transcript speakers]
  "Stage 2: extract opinions per speaker (traverse)."
  (<- model (Ask "s2_model"))
  (<- opinions
    (traverse
      (<- speaker (Iterate speakers :label "s2_opinions"))
      (<- opinion (extract-opinions-one transcript model speaker))
      opinion))
  opinions)

(defk parse-discussion [transcript]
  "Main pipeline: speakers → spans → opinions.
   No Spawn, no Gather, no Try, no retry.
   Strategy is entirely external."
  (<- speaker-names (list-speakers transcript))
  (<- speakers (assign-speaker-spans transcript speaker-names))
  (<- opinions (extract-opinions transcript speakers))
  (<- report (Inspect opinions))
  {"speakers" speakers "opinions" opinions "report" report})


;; ===========================================================================
;; Handlers — error recovery + compute backend, defined ONCE
;; ===========================================================================

;; Mock LLM backend (returns fake data, simulates occasional failure)
(setv _call-count [0])

(defk mock-llm-backend [effect k]
  "Mock LLM: returns fake results. Simulates JSONDecodeError on 3rd call."
  (if (isinstance effect LLMStructuredQuery)
      (do
        (+= (get _call-count 0) 1)
        (setv n (get _call-count 0))
        ;; Simulate JSONDecodeError on call #3
        (when (= n 3)
          (yield (Fail (Exception "JSONDecodeError: simulated parse failure")
                       :query effect)))
        ;; Return mock result
        (setv fmt effect.response-format)
        (return (yield (Resume k (+ "mock_result_" (str n) "_" (str fmt))))))
      (yield (Pass effect k))))

;; Retry handler: retry LLM failures once
(defk retry-on-json-error [effect k]
  "Retry Fail from LLM if it looks like a JSON parse error."
  (if (and (isinstance effect Fail)
           (in "JSONDecodeError" (str effect.cause)))
      (do
        ;; Re-perform the original query (available in context)
        (setv query (get effect.context "query"))
        (<- retried (LLMStructuredQuery
                      :messages query.messages
                      :response-format query.response-format
                      :model query.model
                      :temperature query.temperature
                      :max-tokens query.max-tokens))
        (return (yield (Resume k retried))))
      (yield (Pass effect k))))


;; ===========================================================================
;; Run: same program, different strategies
;; ===========================================================================

(defn with-stack [stack program]
  (setv body program)
  (for [h stack]
    (setv body (WithHandler h body)))
  (scheduled body))

(setv env {"s1a_model" "gpt-4o"
            "s1b_model" "gpt-4o"
            "s2_model" "gpt-4o"})

(setv program (parse-discussion "Alice and Bob discuss AI safety. Alice: I think..."))

;; Sequential + mock backend + retry
(print "=== sequential + retry ===")
(setv (get _call-count 0) 0)
(setv out (run (with-stack
  [try_handler
   retry-on-json-error
   mock-llm-backend
   fail_handler
   (lazy_ask :env env)
   (sequential)]
  program)))
(print "Speakers:" (get out "speakers"))
(print "Opinions valid:" (len (. (get out "opinions") valid_values)))
(print)

;; Parallel + mock backend + retry
(print "=== parallel(5) + retry ===")
(setv (get _call-count 0) 0)
(setv out (run (with-stack
  [try_handler
   retry-on-json-error
   mock-llm-backend
   fail_handler
   (lazy_ask :env env)
   (parallel 5)]
  program)))
(print "Speakers:" (get out "speakers"))
(print "Opinions valid:" (len (. (get out "opinions") valid_values)))
(print)

;; Inspect history
(print "=== item history ===")
(for [item (get out "report")]
  (setv status (if item.failed "FAILED" "OK"))
  (print (+ "  [" (str item.index) "] " status ": " (str item.value)))
  (for [h item.history]
    (print (+ "      " h.event (if h.detail (+ " — " h.detail) "")))))
