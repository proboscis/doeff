(require doeff-hy.macros [deftest])

(import agentd-real-agent-result-retry-e2e-support
        [run-agentd-real-agent-result-report-e2e])


;; ADR 0035: a real agent delivers its result over the agentd-owned
;; report_result MCP channel (byte-faithful, no screen scrape). A valid
;; report finalises the session as done with zero re-prompt retries.
(deftest test-agentd-real-claude-reports-result-over-mcp-channel [tmp-path]
  (setv result (run-agentd-real-agent-result-report-e2e tmp-path "claude"))
  (assert (= (get result "payload") {"summary" "fixed by claude" "ok" True}))
  (assert (= (get result "validation_error") None))
  (assert (= (get result "session_status") "done"))
  (assert (= (get result "retries_used") 0))
  (assert (= (get result "retry_events") 0))
  (assert (= (get result "result_payload_json")
             "{\"ok\":true,\"summary\":\"fixed by claude\"}")))


(deftest test-agentd-real-codex-reports-result-over-mcp-channel [tmp-path]
  (setv result (run-agentd-real-agent-result-report-e2e tmp-path "codex"))
  (assert (= (get result "payload") {"summary" "fixed by codex" "ok" True}))
  (assert (= (get result "validation_error") None))
  (assert (= (get result "session_status") "done"))
  (assert (= (get result "retries_used") 0))
  (assert (= (get result "retry_events") 0))
  (assert (= (get result "result_payload_json")
             "{\"ok\":true,\"summary\":\"fixed by codex\"}")))
