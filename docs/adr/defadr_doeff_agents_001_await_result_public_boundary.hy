;;; Executable ADR for doeff-agents' public result boundary.

(require doeff-adr.macros [defadr defsemgrep rule law])
(import doeff-adr.macros [fact interpretation counterexample])


(defadr ADR-DOE-AGENTS-001
  :title "doeff-agents public result は AwaitResult schema channel のみ"
  :status "accepted"
  :scope ["doeff-agents" "doeff-agents.public-api" "agent-result-schema"]
  :problem
    [(fact
       "旧 doeff-agents は agent に .agentd-result.json を書かせる transport を持っていたが、agent-created JSON result file は利用者が読まなくても runtime result contract として混入しやすい。"
       :evidence "packages/doeff-agents/src/doeff_agents/handlers/production.py")
     (fact
       "doeff-agents は AgentSpec.result_schema と AwaitResultEffect / AwaitOutcome.result を公開 API として持つ。"
       :evidence "packages/doeff-agents/src/doeff_agents/effects/agent.py")
     (fact
       "schema validation と retry prompt は doeff-agents handler が所有している。"
       :evidence "packages/doeff-agents/src/doeff_agents/handlers/production.py")]
  :context
    [(interpretation
       "agent が生成する JSON file は input/result/evidence/checkpoint のどの用途でも runtime contract にしない。結果は doeff-agents が管理する structured result channel で回収する。")
     (interpretation
       "利用者が必要とする値は、LaunchSession 後に AwaitResult を perform し、schema 検証済みの AwaitOutcome.result として受け取る。diagnostic / evidence が必要なら result schema の field に含める。")]
  :decision
    [(rule R1 "doeff-agents 利用者は agent が作成した file を読んで結果を取得してはならない。")
     (rule R2 "doeff-agents 利用者が受け取れる agent result は AwaitResultEffect が返す AwaitOutcome.result のみである。")
     (rule R3 "result schema validation、invalid result retry、missing result retry は doeff-agents handler の責務である。")
     (rule R4 "doeff-agents は agent に .agentd-result.json / result.json などの JSON result file を作らせない。")
     (rule R5 "診断 / 証跡 / checkpoint が必要な caller は AgentSpec.result_schema に明示し、AwaitOutcome.result から受け取る。side-channel file を使わない。")]
  :laws
    [(law await-result-only-public-boundary
       :statement "doeff_agents_user_result => AwaitResult(handle).result and not agent_created_file"
       :counterexamples
         [(counterexample "caller waits for .agentd-result.json and parses it")
          (counterexample "caller tells agent to write result.json, then reads that file")
          (counterexample "caller validates schema after reading an agent-created JSON file instead of letting doeff-agents retry")])
     (law schema-retry-owned-by-doeff-agents
       :statement "invalid_or_absent_agent_result => doeff_agents_handler validates and retries before returning AwaitOutcome"
       :counterexamples
         [(counterexample "application code polls file existence and asks the agent to fix JSON itself")
          (counterexample "application code accepts a JSON file without AwaitResult schema validation")])
     (law agent-result-file-is-not-transport
       :statement "agent_terminal_result => structured_result_channel and not workspace_json_file"
       :counterexamples
         [(counterexample "doeff-agents prompt tells the agent to write .agentd-result.json")
          (counterexample "doeff-agents retry prompt asks the agent to create a JSON result file")])]
  :enforcement
    [(defsemgrep no-public-agentd-result-file-read
       :languages ["generic"]
       :pattern "agentd-result.json"
       :message "doeff-agents users must not read .agentd-result.json; use AwaitResult.result."
       :bad ["payload = Path(work_dir, \".agentd-result.json\").read_text()"]
       :good ["outcome = AwaitResult(handle, timeout_seconds=60)\npayload = outcome.result"])
     (defsemgrep no-public-agent-result-json-boundary
       :languages ["generic"]
       :pattern "result.json"
       :message "agent-created result.json is not a public result boundary; use AgentSpec.result_schema + AwaitResult."
       :bad ["wait_for(Path(work_dir, \"result.json\"))\npayload = json.loads(Path(work_dir, \"result.json\").read_text())"]
       :good ["spec = AgentSpec(result_schema=schema)\noutcome = AwaitResult(handle, timeout_seconds=60)\npayload = outcome.result"])
     (defsemgrep no-public-result-poller
       :languages ["generic"]
       :pattern "start-result-poller"
       :message "file polling for agent results belongs inside doeff-agents, not in users."
       :bad ["start-result-poller(work_dir / \"result.json\")"]
       :good ["outcome = AwaitResult(handle, timeout_seconds=60)"])
     (defsemgrep no-agentd-result-file-write-prompt
       :languages ["generic"]
       :pattern "write a JSON object to `.agentd-result.json`"
       :message "doeff-agents must not instruct agents to create JSON result files."
       :bad ["For this terminal backend, write a JSON object to `.agentd-result.json`."]
       :good ["Do not create JSON result files in the workspace. Return a structured result block."])]
  :plans ["docs/adr/defadr_doeff_agents_001_await_result_public_boundary.hy"])
