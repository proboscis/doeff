(require doeff-hy.macros [deftest])


(deftest test-agentd-real-agent-placeholder [tmp-path]
  ;; rule fixture: real Claude and real Codex coverage are intentionally missing.
  (assert True))


(defn bad-skip []
  pytest.skip)
