(require doeff-hy.macros [deftest])

(import agentd-result-retry-e2e-support
        [run-agentd-deterministic-failure-no-retry-e2e])


;; ADR 0035 R4 / hard rule 7: a deterministic validation failure (a
;; schema-invalid result reported over the report_result channel) is NEVER
;; re-prompted. The session fails on first occurrence with zero retries.
(deftest test-agentd-deterministic-result-failure-is-not-retried [tmp-path]
  (setv result (run-agentd-deterministic-failure-no-retry-e2e tmp-path))
  ;; The agent's invalid report was rejected over the data channel.
  (assert (get result "reported_invalid"))
  (assert (in "does not satisfy" (get result "rejection_error")))
  ;; No valid result was ever recorded.
  (assert (= (get result "result_payload_json") None))
  (assert (= (get result "await_result") None))
  ;; Zero re-prompt retries — the whole point of the ADR.
  (assert (= (get result "retries_used") 0))
  (assert (= (get result "retry_events") 0))
  ;; The rejection is surfaced (failure visibility), not silently dropped.
  (assert (>= (get result "rejected_events") 1))
  ;; The session reached a terminal state without a result.
  (assert (in (get result "session_status") #("failed" "exited"))))
