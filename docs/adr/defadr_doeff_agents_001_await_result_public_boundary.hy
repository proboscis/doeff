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
       :evidence "packages/doeff-agents/src/doeff_agents/handlers/production.py")
     (fact
       "2026-07-02 の Nakagawa SBI L2 readiness では、agent pane に schema-valid な DOEFF_AGENT_RESULT_BEGIN/END block が出ていたが、通常監視幅と入力待ち判定の競合で AwaitResult.result として回収できなかった。"
       :evidence "packages/doeff-agents/tests/test_session_backend.py::test_l2_await_result_uses_extended_capture_before_awaiting_input")
     (fact
       "同じ失敗モードでは、AwaitOutcome.status が AWAITING_INPUT でも schema-valid result を持つ可能性があるため、effectful loop も status より result 値を優先する必要がある。"
       :evidence "packages/doeff-agents/tests/test_agent_launch_invariants.py::test_await_result_result_wins_over_awaiting_input_status")
     (fact
       "Claude Code の `bypass permissions on` / `shift+tab to cycle` は常時フッターであり、長い MCP tool 実行中に出続けるため、これだけを AwaitResult の入力待ち根拠にすると schema result が出る直前に早期 FollowUp してしまう。"
       :evidence "packages/doeff-agents/tests/test_monitor_claude.py::test_stable_claude_permission_footer_alone_does_not_block")
     (fact
       "一度 BLOCKED と判定された Claude Code session は、現在 pane に入力待ち根拠がない場合 RUNNING に戻さなければ、長い MCP tool 実行中の安定画面を古い BLOCKED 状態のまま AwaitResult.AWAITING_INPUT として返してしまう。"
       :evidence "packages/doeff-agents/tests/test_session_backend.py::test_l2_await_result_clears_stale_blocked_state_when_current_footer_is_not_input")
     (fact
       "Claude Code の tmux pane は launch 直後や長い MCP tool 後に shell prompt 風の行を含むことがある。tmux session がまだ存在する間にこれだけで EXITED + result None を返すと、schema result が出る直前に caller が absent result retry を消費する。"
       :evidence "packages/doeff-agents/tests/test_session_backend.py::test_l2_await_result_does_not_finalize_absent_result_on_live_tmux_shell_prompt")
     (fact
       "2026-07-02 の Nakagawa SBI L2 readiness では、Claude が長い readiness_blocker JSON を出した結果、tmux 可視 pane には DOEFF_AGENT_RESULT_END だけが残り、DOEFF_AGENT_RESULT_BEGIN が流れて AwaitResult が result を回収できなかった。"
       :evidence "packages/doeff-agents/tests/test_session_backend.py::test_l2_await_result_uses_transcript_when_result_begin_scrolled_off_screen")
     (fact
       "2026-07-02 の Nakagawa SBI L2 readiness では、Claude が account-state JSON をまだ描画中なのに AwaitResult が DOEFF_AGENT_RESULT_BEGIN だけで result block と見なし、validation follow-up を送って出力中の JSON を壊した。tmux pipe-pane transcript は ANSI/cursor 制御を含むため、BEGIN/END があっても JSON として抽出できないことがある。"
       :evidence "packages/doeff-agents/tests/test_session_backend.py::test_l2_await_result_waits_until_result_end_marker")]
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
     (rule R5 "診断 / 証跡 / checkpoint が必要な caller は AgentSpec.result_schema に明示し、AwaitOutcome.result から受け取る。side-channel file を使わない。")
     (rule R6 "AwaitResult は pane に schema-valid result block が存在する場合、agent がまだ起動中または入力待ち表示でも、その result を入力待ち判定より優先して返す。")
     (rule R7 "AwaitResult effectful loop は AwaitOutcome.result が存在し validation_error がない場合、AwaitOutcome.status が AWAITING_INPUT でも待受を終了して caller に返す。")
     (rule R8 "Claude Code の常時フッターだけを AwaitResult の AWAITING_INPUT 根拠にしてはならない。明示的な入力要求または permission prompt だけを入力待ちとして扱う。")
     (rule R9 "doeff-agents は現在 pane に入力待ち根拠がない BLOCKED 状態を保持してはならない。古い BLOCKED は RUNNING に戻して AwaitResult を継続する。")
     (rule R10 "tmux session がまだ存在する Claude Code L2 session では、shell prompt 風の表示だけで result absent を確定してはならない。結果がなければ heartbeat timeout として再監視する。")
     (rule R11 "tmux backend の AwaitResult は可視 pane だけに依存せず、session 開始時からの transcript tail からも DOEFF_AGENT_RESULT_BEGIN/END block を回収する。ただし transcript fallback は JSON payload として抽出できる時だけ採用し、raw terminal 制御で壊れた block は結果として扱わない。")
     (rule R12 "AwaitResult は DOEFF_AGENT_RESULT_BEGIN だけで result block と見なさず、対応する DOEFF_AGENT_RESULT_END が出るまで待つ。")]
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
          (counterexample "doeff-agents retry prompt asks the agent to create a JSON result file")])
     (law visible-result-block-wins-over-awaiting-input
       :statement "pane_contains_valid_result_block => AwaitResult.result regardless_of_prompt_status"
       :counterexamples
         [(counterexample "handler sees Claude prompt after the block and returns AWAITING_INPUT")
          (counterexample "handler captures only the latest status area and misses an older result block")])
     (law result-value-wins-over-await-status
       :statement "AwaitOutcome.result and no validation_error => caller receives outcome regardless_of_AwaitStatus"
       :counterexamples
         [(counterexample "effectful loop keeps waiting because status is AWAITING_INPUT even though result is present")
          (counterexample "application code requires AwaitStatus.EXITED after doeff-agents has returned a schema-valid result")])
     (law claude-footer-is-not-input-request
       :statement "claude_status_footer_only => not AwaitStatus.AWAITING_INPUT"
       :counterexamples
         [(counterexample "bypass permissions footer alone triggers FollowUp during a long MCP tool call")
          (counterexample "shift+tab footer alone is treated as a user prompt")])
     (law stale-blocked-state-must-clear
       :statement "previous_status_BLOCKED and not current_input_prompt => current_status_RUNNING"
       :counterexamples
         [(counterexample "old idle prompt keeps AwaitResult returning AWAITING_INPUT during an MCP call")
          (counterexample "stable Claude footer with no input request inherits a previous BLOCKED state")])
     (law live-tmux-prompt-is-not-absent-result
       :statement "tmux_session_exists and no_result_block => AwaitStatus.TIMED_OUT not EXITED_absent"
       :counterexamples
         [(counterexample "Claude launch echo with a shell prompt returns EXITED + result None")
          (counterexample "a post-MCP idle-looking pane consumes absent-result retries before the schema block appears")])
     (law result-block-may-outlive-visible-pane
       :statement "transcript_contains_valid_result_block => AwaitResult.result even_if_visible_pane_lost_BEGIN"
       :counterexamples
         [(counterexample "visible pane contains DOEFF_AGENT_RESULT_END but not BEGIN, so AwaitResult ignores a valid transcript result")
          (counterexample "long readiness blocker JSON scrolls BEGIN out of tmux visible screen and the caller waits until timeout")])
     (law partial-or-unparseable-result-block-is-not-result
       :statement "partial_or_unparseable_result_block => keep_waiting_or_use_other_valid_source"
       :counterexamples
         [(counterexample "AwaitResult sends a FollowUp while the agent is still printing JSON between BEGIN and END")
          (counterexample "AwaitResult validates a raw pipe-pane transcript containing cursor-control fragments instead of waiting for a parseable pane block")])]
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
