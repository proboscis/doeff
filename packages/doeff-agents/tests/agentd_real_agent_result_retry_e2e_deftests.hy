(require doeff-hy.macros [deftest])

(import agentd-real-agent-result-retry-e2e-support
        [run-agentd-real-agent-result-retry-e2e])


(deftest test-agentd-real-claude-result-contract-retries-invalid-output [tmp-path]
  (setv result (run-agentd-real-agent-result-retry-e2e tmp-path "claude"))
  (assert (= (get result "payload") {"summary" "fixed by claude" "ok" True}))
  (assert (= (get result "validation_error") None))
  (assert (= (get result "session_status") "done"))
  (assert (<= 1 (get result "retries_used") 2))
  (assert (<= 1 (get result "retry_events") 2))
  (assert (= (get result "result_payload_json")
             "{\"ok\":true,\"summary\":\"fixed by claude\"}")))


(deftest test-agentd-real-codex-result-contract-retries-invalid-output [tmp-path]
  (setv result (run-agentd-real-agent-result-retry-e2e tmp-path "codex"))
  (assert (= (get result "payload") {"summary" "fixed by codex" "ok" True}))
  (assert (= (get result "validation_error") None))
  (assert (= (get result "session_status") "done"))
  (assert (<= 1 (get result "retries_used") 2))
  (assert (<= 1 (get result "retry_events") 2))
  (assert (= (get result "result_payload_json")
             "{\"ok\":true,\"summary\":\"fixed by codex\"}")))
