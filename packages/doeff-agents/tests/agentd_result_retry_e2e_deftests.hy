(require doeff-hy.macros [deftest])

(import agentd-result-retry-e2e-support [run-agentd-tmux-result-retry-e2e])


(deftest test-agentd-tmux-result-contract-retries-invalid-output [tmp-path]
  (setv result (run-agentd-tmux-result-retry-e2e tmp-path))
  (assert (= (get result "payload") {"summary" "fixed" "ok" True}))
  (assert (= (get result "validation_error") None))
  (assert (= (get result "session_status") "done"))
  (assert (= (get result "retries_used") 1))
  (assert (= (get result "retry_events") 1))
  (assert (= (get result "messages_seen") 2))
  (assert (get result "retry_prompt_seen"))
  (assert (get result "initial_protocol_seen"))
  (assert (= (get result "result_payload_json")
             "{\"ok\":true,\"summary\":\"fixed\"}")))
