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

(require doeff-hy.macros [defk defhandler <- traverse])
(import doeff [do :as _doeff-do])
(import doeff [run EffectBase])

(import doeff_core_effects [try-handler :as try_handler Ask slog])
(import doeff_core_effects [lazy-ask :as lazy_ask])
(import doeff_core_effects [scheduled])

(import doeff_traverse [Traverse :as _doeff_traverse_Traverse])
(import doeff_traverse.collection [Collection])
(import doeff_traverse.effects [Fail Inspect])
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
  {:pre [(: transcript str)]
   :post [(: % list)]}
  (<- model (Ask "s1a_model"))
  (<- result (LLMStructuredQuery
               :messages [{"role" "user" "content" (+ "List speakers in: " transcript)}]
               :response-format "SpeakerList"
               :model model
               :temperature 0.2))
  result)

(defk assign-spans-one [transcript all-speakers model speaker-name]
  "Stage 1b worker: assign spans for one speaker."
  {:pre [(: transcript str) (: all-speakers str) (: model str) (: speaker-name str)]
   :post [(: % SpeakerProfile)]}
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
  {:pre [(: transcript str) (: speaker-names list)]
   :post [(: % Collection)]}
  (<- model (Ask "s1b_model"))
  (val all-speakers (.join ", " speaker-names))
  (<- speakers
    (traverse
      (<- name (Iterate speaker-names :label "s1b_assign_spans"))
      (<- speaker (assign-spans-one transcript all-speakers model name))
      speaker))
  speakers)

(defk extract-opinions-one [transcript model speaker]
  "Stage 2 worker: extract opinions for one speaker."
  {:pre [(: transcript str) (: model str) (: speaker SpeakerProfile)]
   :post [(: % Opinion)]}
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
  {:pre [(: transcript str) (: speakers Collection)]
   :post [(: % Collection)]}
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
  {:pre [(: transcript str)]
   :post [(: % dict)]}
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

(defhandler mock-llm-backend
  "Mock LLM: returns fake results. Simulates JSONDecodeError on 3rd call."
  (LLMStructuredQuery [response-format]
    (setv (get _call-count 0) (+ (get _call-count 0) 1))
    (val n (get _call-count 0))
    ;; Simulate JSONDecodeError on call #3. fail_handler (outside) raises the Fail here,
    ;; so the exception reaches whoever performed this query — the retry handler.
    (when (= n 3)
      (<- (Fail (Exception "JSONDecodeError: simulated parse failure")
                :query effect)))
    ;; Return mock result (a speaker list answers names, other formats answer text)
    (resume (if (= response-format "SpeakerList")
                ["Alice" "Bob"]
                (+ "mock_result_" (str n) "_" response-format)))))

;; Retry handler: retry LLM failures once
(defk query-with-retry [query]
  "Ask the outer LLM backend, and once more if the answer fails as a JSON parse error."
  {:pre [(: query LLMStructuredQuery)]
   :post [(: % (| str list))]}
  (try
    (<- answer query)
    answer
    (except [e Exception]
      (when (not-in "JSONDecodeError" (str e))
        (raise))
      (<- retried query)
      retried)))

(defhandler retry-on-json-error
  "Retry an LLM query once when it fails with a JSON parse error."
  (LLMStructuredQuery []
    ;; Performed from this clause, the query goes to the handlers outside (the backend)
    (<- answer (query-with-retry effect))
    (resume answer)))


;; ===========================================================================
;; Run: same program, different strategies
;; ===========================================================================

(defn with-stack [stack program]
  (setv body program)
  (for [h stack]
    (setv body (h body)))
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
(print "LLM calls (call #3 failed and was retried):" (get _call-count 0))
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
(print "LLM calls (call #3 failed and was retried):" (get _call-count 0))
(print)

;; Inspect history
(print "=== item history ===")
(for [item (get out "report")]
  (setv status (if item.failed "FAILED" "OK"))
  (print (+ "  [" (str item.index) "] " status ": " (str item.value)))
  (for [h item.history]
    (print (+ "      " h.event (if h.detail (+ " — " h.detail) "")))))
