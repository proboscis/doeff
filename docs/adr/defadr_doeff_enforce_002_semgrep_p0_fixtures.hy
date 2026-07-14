;;; Executable ADR: P0 Semgrep rules carry installed-rule hit/clean fixtures.

(require doeff-adr.macros [defadr defsemgrep rule law])
(import doeff-adr.macros [fact counterexample])


(defadr ADR-DOE-ENFORCE-002
  :title "P0 semgrep ルールは hit/clean fixture で生存証明を常設する"
  :status "proposed"
  :scope [".semgrep.yaml"
          "docs/adr/defadr_doeff_enforce_002_semgrep_p0_fixtures.hy"
          "docs/adr/enforcement-ledger.json"]
  :problem
    [(fact
       "2026-07-14 棚卸しで 229 中 8 ルールが無音で死んでいた。"
       :evidence "docs/semgrep-rules-inventory-2026-07-14.md")
     (fact
       "一括ゲート(baseline 0)は「違反ゼロ」と「ルール死亡」を区別できない。"
       :evidence "tests/test_semgrep_gate.py")]
  :decision
    [(rule R1 "P0 の 16 ルールは hit/clean fixture を defsemgrep で常設する。")
     (rule R2 "P1 以降への拡張は P0 の運用経験を見て別途裁定する。")]
  :laws
    [(law p0-rule-liveness
       :statement
         "for_all p0_rule: fires_on(bad_fixture) AND not fires_on(good_fixture)"
       :counterexamples
         [(counterexample
            "一括ゲートが green のまま P0 ルールの matcher または path 条件が死ぬ")])]
  :enforcement
    [(defsemgrep p0-no-anthropic-api-key-agent-env
       "doeff-agents-no-anthropic-api-key-agent-env"
       [{"relative-path" "packages/doeff-agents/src/doeff_agents/session.py"
         "source" "session_env = {'ANTHROPIC_API_KEY': 'secret'}\n"}]
       [{"relative-path" "packages/doeff-agents/src/doeff_agents/session.py"
         "source" "session_env = {'PYTHONUNBUFFERED': '1'}\n"}])
     (defsemgrep p0-no-claude-print-mode
       "doeff-agents-no-claude-print-mode"
       [{"relative-path" "packages/doeff-agents/src/doeff_agents/adapters/claude.py"
         "source" "command = 'claude --print'\n"}]
       [{"relative-path" "packages/doeff-agents/src/doeff_agents/adapters/claude.py"
         "source" "command = 'claude'\n"}])
     (defsemgrep p0-require-real-claude-result-retry-e2e
       "doeff-agents-require-real-claude-result-retry-e2e"
       [{"relative-path"
           "packages/doeff-agents/tests/agentd_real_agent_result_retry_e2e_deftests.hy"
         "source"
           "(deftest test-agentd-real-codex-result-contract-retries-invalid-output []\n  (assert True))\n"}]
       [{"relative-path"
           "packages/doeff-agents/tests/agentd_real_agent_result_retry_e2e_deftests.hy"
         "source"
           "(deftest test-agentd-real-claude-result-contract-retries-invalid-output []\n  (assert True))\n"}])
     (defsemgrep p0-require-real-codex-result-retry-e2e
       "doeff-agents-require-real-codex-result-retry-e2e"
       [{"relative-path"
           "packages/doeff-agents/tests/agentd_real_agent_result_retry_e2e_deftests.hy"
         "source"
           "(deftest test-agentd-real-claude-result-contract-retries-invalid-output []\n  (assert True))\n"}]
       [{"relative-path"
           "packages/doeff-agents/tests/agentd_real_agent_result_retry_e2e_deftests.hy"
         "source"
           "(deftest test-agentd-real-codex-result-contract-retries-invalid-output []\n  (assert True))\n"}])
     (defsemgrep p0-real-agent-e2e-must-not-be-skipped
       "doeff-agents-real-agent-e2e-must-not-be-skipped"
       [{"relative-path"
           "packages/doeff-agents/tests/agentd_real_agent_result_retry_e2e_deftests.hy"
         "source" "(deftest test-real-agent []\n  (pytest.skip))\n"}]
       [{"relative-path"
           "packages/doeff-agents/tests/agentd_real_agent_result_retry_e2e_deftests.hy"
         "source" "(deftest test-real-agent []\n  (assert True))\n"}])
     (defsemgrep p0-real-agent-e2e-must-not-use-command-override
       "doeff-agents-real-agent-e2e-must-not-use-command-override"
       [{"relative-path"
           "packages/doeff-agents/tests/agentd_real_agent_result_retry_e2e_support.py"
         "source"
           "def launch(client):\n    return client.launch_session(agent_type='claude', command='fake-agent')\n"}]
       [{"relative-path"
           "packages/doeff-agents/tests/agentd_real_agent_result_retry_e2e_support.py"
         "source"
           "def launch(client):\n    return client.launch_session(agent_type='claude')\n"}])
     (defsemgrep p0-agentd-request-requires-result-key
       "doeff-agents-agentd-request-requires-result-key"
       [{"relative-path" "packages/doeff-agents/src/doeff_agents/agentd_client.py"
         "source"
           "def request(response):\n    return response.get('result')\n"}]
       [{"relative-path" "packages/doeff-agents/src/doeff_agents/agentd_client.py"
         "source"
           "def request(response):\n    return response['result']\n"}])
     (defsemgrep p0-await-result-requires-protocol-fields
       "doeff-agents-await-result-requires-protocol-fields"
       [{"relative-path" "packages/doeff-agents/src/doeff_agents/agentd_client.py"
         "source"
           "def _await_outcome_from_result(result):\n    return result.get('session')\n"}]
       [{"relative-path" "packages/doeff-agents/src/doeff_agents/agentd_client.py"
         "source"
           "def _await_outcome_from_result(result):\n    return result['session']\n"}])
     (defsemgrep p0-no-return-control-primitives-in-handlers
       "doeff-no-return-control-primitives-in-handlers"
       [{"relative-path" "packages/sample/handler.py"
         "source"
           "@do\ndef handle(effect, k):\n    return Pass()\n"}]
       [{"relative-path" "packages/sample/handler.py"
         "source"
           "@do\ndef handle(effect, k):\n    yield Pass()\n"}])
     (defsemgrep p0-no-effect-substitution-on-forward
       "doeff-no-effect-substitution-on-forward"
       [{"relative-path" "packages/sample/handler.py"
         "source"
           "def handle(effect):\n    yield Delegate(effect)\n"}]
       [{"relative-path" "packages/sample/handler.py"
         "source"
           "def handle(effect):\n    yield effect\n    yield Delegate()\n"}])
     (defsemgrep p0-vm-core-no-wildcard-unreachable-on-match
       "vm-core-no-wildcard-unreachable-on-match"
       [{"relative-path" "packages/doeff-vm-core/src/vm.rs"
         "source"
           "fn dispatch(value: Value) {\n    match value {\n        _ => unreachable!(),\n    }\n}\n"}]
       [{"relative-path" "packages/doeff-vm-core/src/vm.rs"
         "source"
           "fn dispatch(value: Value) {\n    match value {\n        Value::Done => unreachable!(),\n    }\n}\n"}])
     (defsemgrep p0-vm-no-raw-runtime-or-type-throw
       "vm-no-raw-runtime-or-type-throw"
       [{"relative-path" "packages/doeff-vm-core/src/vm.rs"
         "source"
           "fn fail(error: String) {\n    let mode = Mode::Throw(PyException::runtime_error(error));\n}\n"}]
       [{"relative-path" "packages/doeff-vm-core/src/vm.rs"
         "source"
           "fn fail(error: String) {\n    let mode = contextual_throw_mode(PyException::runtime_error(error));\n}\n"}])
     (defsemgrep p0-scheduler-spawn-must-capture-boundaries
       "doeff-scheduler-spawn-must-capture-boundaries"
       [{"relative-path"
           "packages/doeff-core-effects/doeff_core_effects/scheduler.py"
         "source" "boundaries = yield get_inner_handlers(k)\n"}]
       [{"relative-path"
           "packages/doeff-core-effects/doeff_core_effects/scheduler.py"
         "source" "boundaries = yield get_inner_boundaries(k)\n"}])
     (defsemgrep p0-k4-no-blocking-agent-handler
       "k4-no-blocking-agent-handler"
       [{"relative-path"
           "packages/doeff-conductor/src/doeff_conductor/handlers/agent.py"
         "source"
           "handlers = [(AgentEffect, make_blocking_scheduled_handler(handle_agent))]\n"}]
       [{"relative-path"
           "packages/doeff-conductor/src/doeff_conductor/handlers/agent.py"
         "source"
           "handlers = [(AgentEffect, make_offloaded_scheduled_handler(handle_agent))]\n"}])
     (defsemgrep p0-k4-no-sync-await-result-in-scheduled-handler
       "k4-no-sync-await-result-in-scheduled-handler"
       [{"relative-path"
           "packages/doeff-agents/src/doeff_agents/handlers/agent.py"
         "source"
           "handler = make_blocking_scheduled_handler(lambda effect: client.await_result(effect))\n"}]
       [{"relative-path"
           "packages/doeff-agents/src/doeff_agents/handlers/agent.py"
         "source"
           "handler = make_offloaded_scheduled_handler(lambda effect: client.await_result(effect))\n"}])
     (defsemgrep p0-k4-deadline-not-transport-timeout
       "k4-deadline-not-transport-timeout"
       [{"relative-path"
           "packages/doeff-agents/src/doeff_agents/effects/agent.py"
         "source" "task = AgentTask(prompt='run', timeout_seconds=30)\n"}]
       [{"relative-path"
           "packages/doeff-agents/src/doeff_agents/effects/agent.py"
         "source" "task = AgentTask(prompt='run', deadline_seconds=30)\n"}])]
  :plans ["docs/doeff-2026-07-14-agent-first-investment-architecture-plan.md"
          "docs/semgrep-rules-inventory-2026-07-14.md"])
