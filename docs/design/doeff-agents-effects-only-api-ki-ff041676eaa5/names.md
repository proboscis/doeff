# doeff_agents の公開の名ごとの決めと呼び手(機械で生成 — tools/gen_names.py)

目録: doeff `4368f740fbda` の作業木から `tools/agents_public_inventory.py` が数えた `evidence/inventory.json`。外の repo: agora-controllers `b73b9c61ec14` を `tools/agents_external_callers.py` が数えた `evidence/agora_callers.json`。
呼び手の数 = その名を語として含み doeff_agents を名指す file の数(定義元の file を除く)。src = doeff_agents の中・tests = doeff-agents の tests と conformance と repo の tests・他 = packages/ の他の package・docs = docs と例。語の一致で数えるので、`agent`・`Stop`・`Send` のような短い語は多めに出る(残す名なので表の結論に響かない)。

## 1. 区分ごとの数

| 区分 | 名の数 |
|---|---|
| effect(残す) | 46 |
| handler(残す) | 15 |
| 境界: effect の値の型・例外(残す — 区切り 1 の答えに従う) | 77 |
| 境界: handler の引数の値の型(残す — 区切り 1 の答えに従う) | 8 |
| 外す | 69 |
| 計(top-level の `_LAZY_EXPORTS`) | 215 |

## 2. 残す effect

`AgentEffect` `AttachAgentSession` `AttachAgentSessionEffect` `AwaitResult` `AwaitResultEffect` `CancelAgentSession` `CancelAgentSessionEffect` `Capture` `CaptureEffect` `CleanupAgentSession` `CleanupAgentSessionEffect` `Events` `EventsEffect` `ExportContextEffect` `FollowUp` `FollowUpEffect` `GetAgentSession` `GetAgentSessionEffect` `Interrupt` `InterruptEffect` `Launch` `LaunchEffect` `LaunchSession` `LaunchSessionEffect` `ListAgentSessions` `ListAgentSessionsEffect` `Monitor` `MonitorEffect` `ObserveAgentSession` `ObserveAgentSessionEffect` `PutAgentSession` `PutAgentSessionEffect` `RedeemTurnCredentialEffect` `ReleaseSession` `ReleaseSessionEffect` `ReplaceSessionCredential` `ReplaceSessionCredentialEffect` `Send` `SendEffect` `Stop` `StopEffect` `StopSession` `StopSessionEffect` `WarmSession` `WarmSessionEffect` `agent`

## 3. 残す handler

`agent_effectful_handler` `agent_effectful_handlers` `claude_agent_adapter_handler` `claude_agent_runtime_handlers` `claude_process_layer_handler` `codex_agent_adapter_handler` `codex_agent_handler` `codex_process_layer_handler` `daemon_agent_handler` `daemon_agent_handlers` `fake_claude_agent_runtime_handlers` `fake_claude_process_layer_handler` `fake_codex_process_layer_handler` `mock_agent_handler` `mock_agent_handlers`

## 4. 境界(区切り 1 の問いの答えまで残す)

| 名 | 区分 | 註 |
|---|---|---|
| `AgentAccountLimit` | 境界: effect の値の型・例外(残す — 区切り 1 の答えに従う) |  |
| `AgentAccountRefusal` | 境界: effect の値の型・例外(残す — 区切り 1 の答えに従う) |  |
| `AgentAttemptExhaustedError` | 境界: effect の値の型・例外(残す — 区切り 1 の答えに従う) |  |
| `AgentCallUsageEvent` | 境界: effect の値の型・例外(残す — 区切り 1 の答えに従う) |  |
| `AgentCapabilityUnsupportedError` | 境界: effect の値の型・例外(残す — 区切り 1 の答えに従う) |  |
| `AgentCompactionEndedEvent` | 境界: effect の値の型・例外(残す — 区切り 1 の答えに従う) |  |
| `AgentCompactionEvent` | 境界: effect の値の型・例外(残す — 区切り 1 の答えに従う) |  |
| `AgentCompactionStartedEvent` | 境界: effect の値の型・例外(残す — 区切り 1 の答えに従う) |  |
| `AgentDeadlineExceededError` | 境界: effect の値の型・例外(残す — 区切り 1 の答えに従う) |  |
| `AgentError` | 境界: effect の値の型・例外(残す — 区切り 1 の答えに従う) |  |
| `AgentEvent` | 境界: effect の値の型・例外(残す — 区切り 1 の答えに従う) |  |
| `AgentEventPage` | 境界: effect の値の型・例外(残す — 区切り 1 の答えに従う) |  |
| `AgentHookEvent` | 境界: effect の値の型・例外(残す — 区切り 1 の答えに従う) |  |
| `AgentInputFateEvent` | 境界: effect の値の型・例外(残す — 区切り 1 の答えに従う) |  |
| `AgentLaunchError` | 境界: effect の値の型・例外(残す — 区切り 1 の答えに従う) | top-level の出元を session.py の別の class から effects.agent の class へ替える(handler が投げるのは effects.agent の方) |
| `AgentModelRefusal` | 境界: effect の値の型・例外(残す — 区切り 1 の答えに従う) |  |
| `AgentNotAvailableError` | 境界: effect の値の型・例外(残す — 区切り 1 の答えに従う) |  |
| `AgentProviderCapacity` | 境界: effect の値の型・例外(残す — 区切り 1 の答えに従う) |  |
| `AgentReadyTimeoutError` | 境界: effect の値の型・例外(残す — 区切り 1 の答えに従う) | AgentLaunchError と同じく出元を effects.agent へ替える |
| `AgentSessionLifecycle` | 境界: effect の値の型・例外(残す — 区切り 1 の答えに従う) |  |
| `AgentSessionQuery` | 境界: effect の値の型・例外(残す — 区切り 1 の答えに従う) |  |
| `AgentSessionSnapshot` | 境界: effect の値の型・例外(残す — 区切り 1 の答えに従う) |  |
| `AgentSpec` | 境界: effect の値の型・例外(残す — 区切り 1 の答えに従う) |  |
| `AgentStopHookFeedbackEvent` | 境界: effect の値の型・例外(残す — 区切り 1 の答えに従う) |  |
| `AgentSubagentCallUsageEvent` | 境界: effect の値の型・例外(残す — 区切り 1 の答えに従う) |  |
| `AgentTask` | 境界: effect の値の型・例外(残す — 区切り 1 の答えに従う) |  |
| `AgentTextDeltaEvent` | 境界: effect の値の型・例外(残す — 区切り 1 の答えに従う) |  |
| `AgentTextEvent` | 境界: effect の値の型・例外(残す — 区切り 1 の答えに従う) |  |
| `AgentThinkingDeltaEvent` | 境界: effect の値の型・例外(残す — 区切り 1 の答えに従う) |  |
| `AgentThinkingStartedEvent` | 境界: effect の値の型・例外(残す — 区切り 1 の答えに従う) |  |
| `AgentToolCallStartedEvent` | 境界: effect の値の型・例外(残す — 区切り 1 の答えに従う) |  |
| `AgentToolInputDeltaEvent` | 境界: effect の値の型・例外(残す — 区切り 1 の答えに従う) |  |
| `AgentToolResultEvent` | 境界: effect の値の型・例外(残す — 区切り 1 の答えに従う) |  |
| `AgentToolUseEvent` | 境界: effect の値の型・例外(残す — 区切り 1 の答えに従う) |  |
| `AgentTurnCompleted` | 境界: effect の値の型・例外(残す — 区切り 1 の答えに従う) |  |
| `AgentTurnEnd` | 境界: effect の値の型・例外(残す — 区切り 1 の答えに従う) |  |
| `AgentTurnEndEvent` | 境界: effect の値の型・例外(残す — 区切り 1 の答えに従う) |  |
| `AgentTurnFailed` | 境界: effect の値の型・例外(残す — 区切り 1 の答えに従う) |  |
| `AgentTurnInterrupted` | 境界: effect の値の型・例外(残す — 区切り 1 の答えに従う) |  |
| `AgentTurnLost` | 境界: effect の値の型・例外(残す — 区切り 1 の答えに従う) |  |
| `AgentTurnUsage` | 境界: effect の値の型・例外(残す — 区切り 1 の答えに従う) |  |
| `AgentType` | 境界: effect の値の型・例外(残す — 区切り 1 の答えに従う) |  |
| `AgentValidationErrorKind` | 境界: effect の値の型・例外(残す — 区切り 1 の答えに従う) |  |
| `AgentValidationFailure` | 境界: effect の値の型・例外(残す — 区切り 1 の答えに従う) |  |
| `AwaitOutcome` | 境界: effect の値の型・例外(残す — 区切り 1 の答えに従う) |  |
| `AwaitStatus` | 境界: effect の値の型・例外(残す — 区切り 1 の答えに従う) |  |
| `CompactionOutcome` | 境界: effect の値の型・例外(残す — 区切り 1 の答えに従う) |  |
| `CompactionTrigger` | 境界: effect の値の型・例外(残す — 区切り 1 の答えに従う) |  |
| `HandlerMadeContextId` | 境界: effect の値の型・例外(残す — 区切り 1 の答えに従う) |  |
| `HomeTurnCredential` | 境界: effect の値の型・例外(残す — 区切り 1 の答えに従う) |  |
| `InputFateState` | 境界: effect の値の型・例外(残す — 区切り 1 の答えに従う) |  |
| `JSONSchema` | 境界: effect の値の型・例外(残す — 区切り 1 の答えに従う) |  |
| `ModelUsage` | 境界: effect の値の型・例外(残す — 区切り 1 の答えに従う) |  |
| `ModelUsageGap` | 境界: effect の値の型・例外(残す — 区切り 1 の答えに従う) |  |
| `ModelWindow` | 境界: effect の値の型・例外(残す — 区切り 1 の答えに従う) |  |
| `NamedContextId` | 境界: effect の値の型・例外(残す — 区切り 1 の答えに従う) |  |
| `NoTurnInFlightError` | 境界: effect の値の型・例外(残す — 区切り 1 の答えに従う) |  |
| `Observation` | 境界: effect の値の型・例外(残す — 区切り 1 の答えに従う) |  |
| `ReplaceSessionCredentialOutcome` | 境界: effect の値の型・例外(残す — 区切り 1 の答えに従う) |  |
| `ReplacementCredentialUnavailable` | 境界: effect の値の型・例外(残す — 区切り 1 の答えに従う) |  |
| `ResumeTargetNotFoundError` | 境界: effect の値の型・例外(残す — 区切り 1 の答えに従う) |  |
| `SessionAlreadyExistsError` | 境界: effect の値の型・例外(残す — 区切り 1 の答えに従う) |  |
| `SessionCredentialReplaced` | 境界: effect の値の型・例外(残す — 区切り 1 の答えに従う) |  |
| `SessionHandle` | 境界: effect の値の型・例外(残す — 区切り 1 の答えに従う) |  |
| `SessionNotFoundError` | 境界: effect の値の型・例外(残す — 区切り 1 の答えに従う) |  |
| `SessionOnHomeCredential` | 境界: effect の値の型・例外(残す — 区切り 1 の答えに従う) |  |
| `SessionProcessNotLive` | 境界: effect の値の型・例外(残す — 区切り 1 の答えに従う) |  |
| `SessionStatus` | 境界: effect の値の型・例外(残す — 区切り 1 の答えに従う) | 定義を monitor.py から effects/agent.py へ移す(Observation.status の型) |
| `ToolAnswer` | 境界: effect の値の型・例外(残す — 区切り 1 の答えに従う) |  |
| `ToolCall` | 境界: effect の値の型・例外(残す — 区切り 1 の答えに従う) |  |
| `TranscriptRef` | 境界: effect の値の型・例外(残す — 区切り 1 の答えに従う) |  |
| `TurnCredential` | 境界: effect の値の型・例外(残す — 区切り 1 の答えに従う) |  |
| `TurnCredentialUnavailable` | 境界: effect の値の型・例外(残す — 区切り 1 の答えに従う) |  |
| `TurnCredentialUnavailableError` | 境界: effect の値の型・例外(残す — 区切り 1 の答えに従う) |  |
| `TurnInFlightError` | 境界: effect の値の型・例外(残す — 区切り 1 の答えに従う) |  |
| `TurnInputMode` | 境界: effect の値の型・例外(残す — 区切り 1 の答えに従う) |  |
| `TurnRef` | 境界: effect の値の型・例外(残す — 区切り 1 の答えに従う) |  |
| `AgentSessionRepository` | 境界: handler の引数の値の型(残す — 区切り 1 の答えに従う) | 段 B で台帳の handler(handlers/session_store.hy)を据える形へ |
| `AgentdSessionClient` | 境界: handler の引数の値の型(残す — 区切り 1 の答えに従う) |  |
| `ClaudeRuntimePolicy` | 境界: handler の引数の値の型(残す — 区切り 1 の答えに従う) |  |
| `InMemoryAgentSessionRepository` | 境界: handler の引数の値の型(残す — 区切り 1 の答えに従う) | 段 B で台帳の handler の factory へ |
| `JsonlAgentSessionRepository` | 境界: handler の引数の値の型(残す — 区切り 1 の答えに従う) | 段 B で台帳の handler の factory へ(file の I/O を handler の中へ) |
| `MockSessionScript` | 境界: handler の引数の値の型(残す — 区切り 1 の答えに従う) | mock_agent_handlers(sessions=…) の引数の値にする(session の名と最初の出力の欄を足す) |
| `ScenarioStep` | 境界: handler の引数の値の型(残す — 区切り 1 の答えに従う) |  |
| `SessionBackend` | 境界: handler の引数の値の型(残す — 区切り 1 の答えに従う) | 段 B で handler の中へ(agent_effectful_handler が backend を自分で選ぶ) |

## 5. top-level から外す名 — 移し先・段・呼び手

`_LAZY_EXPORTS` と `if TYPE_CHECKING:` の節(tests/test_package_root_typed_exports.py が照らす)の両方から消す。互換の別名は残さない。
段 A = card ki-4b53dcee28f3 で外す。

| 名 | 今の定義元 | 移し先 | 段 | 呼び手 src / tests / 他 / docs | agora-controllers | 註 |
|---|---|---|---|---|---|---|
| `AGENT_SESSIONS_KEY` | `doeff_agents.handlers` | 消す(呼び手 0) | A | 0 / 0 / 0 / 0 | 0 |  |
| `AgentAdapter` | `doeff_agents.adapters.base` | 内側の module(_adapters) | A | 3 / 0 / 0 / 1 | 0 |  |
| `AgentHandler` | `doeff_agents.handlers` | 内側の module(handlers/_production) | A | 3 / 1 / 3 / 0 | 0 |  |
| `AgentResult` | `doeff_agents.programs` | effect の答え AwaitOutcome | A | 0 / 0 / 0 / 0 | 0 |  |
| `AgentSession` | `doeff_agents.session` | 内側の module(_session)— 呼び手は SessionHandle だけを持つ | A | 1 / 0 / 1 / 0 | 0 |  |
| `AgentSessionEvent` | `doeff_agents.session_store` | 内側の module(_session_store) | A | 0 / 0 / 0 / 0 | 0 |  |
| `AgentdClient` | `doeff_agents.agentd_client` | 内側の module(_agentd_client) | A | 2 / 10 / 0 / 3 | 0 |  |
| `AgentdClientError` | `doeff_agents.agentd_client` | 内側の module(_agentd_client) | A | 2 / 8 / 0 / 0 | 0 |  |
| `AgentdPaths` | `doeff_agents.agentd_client` | 内側の module(_agentd_client) | A | 0 / 0 / 0 / 0 | 0 |  |
| `AgentdProtocolError` | `doeff_agents.agentd_client` | 内側の module(_agentd_client) | A | 0 / 1 / 1 / 0 | 0 |  |
| `AgentdSessionList` | `doeff_agents.agentd_client` | 内側の module(_agentd_client) | A | 0 / 2 / 0 / 0 | 0 |  |
| `AgentdSessionParseWarning` | `doeff_agents.agentd_client` | 内側の module(_agentd_client) | A | 1 / 2 / 0 / 0 | 0 |  |
| `AgentdSupervisorConfigError` | `doeff_agents.agentd_client` | 内側の module(_agentd_client) | A | 1 / 1 / 0 / 1 | 0 |  |
| `AgentdSupervisorDeclaration` | `doeff_agents.agentd_client` | 内側の module(_agentd_client) | A | 0 / 0 / 0 / 0 | 0 |  |
| `AgentdUnavailableError` | `doeff_agents.agentd_client` | 内側の module(_agentd_client) | A | 1 / 2 / 0 / 0 | 0 |  |
| `ClaudeLaunchEffect` | `doeff_agents.effects` | 内側の module(effects/_handler_effects)— handler の間だけの effect | A | 7 / 5 / 0 / 0 | 0 |  |
| `DaemonAgentHandler` | `doeff_agents.handlers` | 内側の module(handlers/_daemon)— 呼び手は daemon_agent_handler(s) | A | 2 / 1 / 1 / 4 | 0 |  |
| `InjectionMethod` | `doeff_agents.adapters.base` | 内側の module(_adapters) | A | 6 / 7 / 1 / 1 | 0 |  |
| `L2SessionHandle` | `doeff_agents.effects` | 残す名 SessionHandle | A | 4 / 2 / 0 / 0 | 0 | 同じ型の 2 つ目の名 |
| `LaunchConfig` | `doeff_agents.adapters.base` | effect Launch の平らな欄 | A | 5 / 7 / 6 / 11 | 0 |  |
| `LaunchParams` | `doeff_agents.adapters.base` | 内側の module(_adapters) | A | 11 / 8 / 1 / 6 | 0 |  |
| `LazyAgentdClient` | `doeff_agents.agentd_client` | 内側の module(_agentd_client)— 呼び手は daemon_agent_handler(s) の socket_path 等の引数 | A | 1 / 1 / 3 / 2 | 0 |  |
| `MOCK_AGENT_STATE_KEY` | `doeff_agents.handlers` | 消す(呼び手 0) | A | 0 / 0 / 0 / 0 | 0 |  |
| `MockAgentHandler` | `doeff_agents.handlers` | 内側の module(handlers/_testing)— 呼び手は mock_agent_handler(s) | A | 0 / 2 / 0 / 1 | 0 |  |
| `MockAgentState` | `doeff_agents.handlers` | 内側の module(handlers/_testing) | A | 0 / 0 / 0 / 0 | 0 |  |
| `MonitorState` | `doeff_agents.monitor` | 内側の module(_monitor) | A | 4 / 2 / 0 / 1 | 0 |  |
| `OnStatusChange` | `doeff_agents.monitor` | effect Events を読む loop(callback を持たない) | A | 1 / 0 / 0 / 0 | 0 |  |
| `ScenarioAgentHandler` | `doeff_agents.handlers` | handler の factory scenario_agent_handler(steps)(新しく足す) | A | 0 / 4 / 2 / 3 | 0 |  |
| `SessionConfig` | `doeff_agents.session_backend` | 内側の module(_session_backend) | A | 5 / 3 / 2 / 0 | 0 |  |
| `SessionInfo` | `doeff_agents.session_backend` | 内側の module(_session_backend) | A | 1 / 9 / 2 / 0 | 0 |  |
| `StableTmuxSessionBackend` | `doeff_agents.tmux` | 内側の module(_tmux) | A | 1 / 0 / 0 / 0 | 0 |  |
| `TmuxAgentHandler` | `doeff_agents.handlers` | 内側の module(handlers/_production)— 呼び手は agent_effectful_handler(s) | A | 3 / 6 / 2 / 8 | 0 |  |
| `TmuxError` | `doeff_agents.tmux` | 内側の module(_tmux) | A | 0 / 0 / 0 / 0 | 0 |  |
| `TmuxNotAvailableError` | `doeff_agents.tmux` | 内側の module(_tmux)— handler は AgentNotAvailableError で断る | A | 0 / 0 / 0 / 0 | 0 |  |
| `TmuxSessionBackend` | `doeff_agents.tmux` | 内側の module(_tmux) | A | 1 / 3 / 0 / 2 | 0 |  |
| `agentd_socket_is_supervised` | `doeff_agents.agentd_client` | 内側の module(_agentd_client) | A | 1 / 0 / 0 / 0 | 0 |  |
| `async_monitor_session` | `doeff_agents.session` | effect AwaitResult・Events | A | 0 / 0 / 1 / 1 | 0 |  |
| `async_session_scope` | `doeff_agents.session` | effect Launch と try/finally の Stop | A | 0 / 0 / 2 / 1 | 0 |  |
| `attach_session` | `doeff_agents.session` | effect AttachAgentSession | A | 4 / 3 / 3 / 0 | 0 |  |
| `capture_output` | `doeff_agents.session` | effect AwaitResult の答え AwaitOutcome.result(結果)・Capture(端末の画面 — tmux の handler だけ) | A | 1 / 9 / 7 / 3 | 3 |  |
| `configure_mock_session` | `doeff_agents.handlers` | handler の中へ(mock_agent_handlers(sessions=(MockSessionScript(…), …))) | A | 0 / 2 / 0 / 8 | 0 | module の大域の状態を持たない |
| `default_agent_handler` | `doeff_agents.handlers` | 内側の module(handlers/_production)— 返すのは doeff の handler でなく AgentHandler の object。呼び手は agent_effectful_handler を据える | A | 0 / 1 / 0 / 2 | 1 | AGENTS.md の節 Agent Authentication Boundary の 1 文を直す |
| `default_agentd_paths` | `doeff_agents.agentd_client` | 内側の module(_agentd_client) | A | 1 / 1 / 0 / 0 | 0 |  |
| `default_session_backend` | `doeff_agents.session_backend` | handler の中へ(agent_effectful_handler(s) が既定の backend を選ぶ) | A | 0 / 1 / 0 / 5 | 0 |  |
| `deterministic_session_id` | `doeff_agents.effects` | 内側の module(effects/_ids)— 呼び手は AgentSpec.session_id を読む | A | 1 / 1 / 1 / 1 | 0 |  |
| `dispatch_effect` | `doeff_agents.handlers` | 内側の module(handlers/_dispatch) | A | 0 / 0 / 0 / 0 | 0 |  |
| `ensure_agentd` | `doeff_agents.agentd_client` | 内側の module(_agentd_client)— 呼び手は daemon_agent_handlers を据える(CLI の agentd ensure は残る) | A | 1 / 3 / 1 / 3 | 0 |  |
| `fake_headless_claude_agent_handlers` | `doeff_agents.handlers` | 残す名 fake_claude_agent_runtime_handlers | A | 0 / 1 / 0 / 1 | 10 | 同じ物の 2 つ目の名・agora-controllers が 10 file で使う |
| `get_adapter` | `doeff_agents.session` | 内側の module(_session) | A | 3 / 7 / 1 / 0 | 0 |  |
| `get_mock_agent_state` | `doeff_agents.handlers` | effect GetAgentSession・ListAgentSessions(mock の handler が答える) | A | 0 / 0 / 0 / 0 | 0 |  |
| `headless_claude_agent_handlers` | `doeff_agents.handlers` | 残す名 claude_agent_runtime_handlers | A | 0 / 1 / 0 / 1 | 1 | 同じ物の 2 つ目の名 |
| `interactive_session` | `doeff_agents.programs` | effect Launch・FollowUp・AwaitResult の組 | A | 0 / 1 / 0 / 0 | 0 |  |
| `launch_session` | `doeff_agents.session` | effect Launch | A | 6 / 11 / 3 / 3 | 7 |  |
| `load_supervisor_declaration` | `doeff_agents.agentd_client` | 内側の module(_agentd_client) | A | 0 / 0 / 0 / 0 | 0 |  |
| `mock_handlers` | `doeff_agents.handlers` | 残す名 mock_agent_handlers | A | 0 / 0 / 7 / 0 | 1 | 同じ物の 2 つ目の名 |
| `monitor_agent_to_completion` | `doeff_agents.programs` | effect AwaitResult | A | 0 / 1 / 0 / 0 | 0 |  |
| `monitor_once` | `doeff_agents.programs` | effect Monitor | A | 1 / 1 / 0 / 1 | 0 |  |
| `monitor_session` | `doeff_agents.session` | effect AwaitResult(終わりまで)・Events(出来事)・Monitor(1 度の観測) | A | 1 / 2 / 4 / 3 | 0 |  |
| `monitor_until_terminal` | `doeff_agents.programs` | effect AwaitResult | A | 0 / 0 / 0 / 0 | 0 |  |
| `production_handlers` | `doeff_agents.handlers` | 残す名 agent_effectful_handlers | A | 0 / 0 / 5 / 1 | 1 | 同じ物の 2 つ目の名 |
| `quick_agent` | `doeff_agents.programs` | effect agent(AgentEffect — schema つきの 1 度の頼み) | A | 0 / 0 / 0 / 0 | 0 |  |
| `register_adapter` | `doeff_agents.session` | 内側の module(_session)— 新しい agent の種類は handler の module で足す(ADR-DOE-AGENTS-004 R2) | A | 2 / 0 / 0 / 0 | 0 |  |
| `run_agent_to_completion` | `doeff_agents.programs` | effect Launch・AwaitResult・Stop の組か effect agent | A | 0 / 2 / 0 / 2 | 0 |  |
| `send_message` | `doeff_agents.session` | effect Send・FollowUp | A | 0 / 1 / 3 / 2 | 0 |  |
| `session_scope` | `doeff_agents.session` | effect Launch と try/finally の Stop | A | 0 / 0 / 3 / 3 | 0 |  |
| `stop_session` | `doeff_agents.session` | effect Stop・StopSession | A | 0 / 1 / 2 / 1 | 0 |  |
| `wait_agent_session` | `doeff_agents.programs` | effect ObserveAgentSession・GetAgentSession | A | 0 / 1 / 0 / 0 | 0 |  |
| `wait_and_monitor` | `doeff_agents.programs` | effect AwaitResult | A | 0 / 0 / 0 / 0 | 0 |  |
| `with_session` | `doeff_agents.programs` | effect Launch と try/finally の Stop | A | 0 / 0 / 0 / 3 | 0 |  |

## 6. 外す名の呼び手の file(doeff の repo の中)

### `AGENT_SESSIONS_KEY`

- 呼び手なし

### `AgentAdapter`

- src: `packages/doeff-agents/src/doeff_agents/adapters/__init__.py`, `packages/doeff-agents/src/doeff_agents/handlers/production.py`, `packages/doeff-agents/src/doeff_agents/session.py`
- docs_examples: `packages/doeff-agents/examples/04_custom_adapter.py`

### `AgentHandler`

- src: `packages/doeff-agents/src/doeff_agents/handlers/daemon.py`, `packages/doeff-agents/src/doeff_agents/handlers/effectful.hy`, `packages/doeff-agents/src/doeff_agents/handlers/testing.hy`
- tests: `packages/doeff-agents/tests/test_headless_adapter.hy`
- other_packages: `packages/doeff-conductor/src/doeff_conductor/handlers/agent_handler.py`, `packages/doeff-conductor/tests/test_agent_effect_c1.py`, `packages/doeff-conductor/tests/test_k4_deadline_owning_axis.py`

### `AgentResult`

- 呼び手なし

### `AgentSession`

- src: `packages/doeff-agents/src/doeff_agents/cli.py`
- other_packages: `.semgrep.yaml`

### `AgentSessionEvent`

- 呼び手なし

### `AgentdClient`

- src: `packages/doeff-agents/src/doeff_agents/cli.py`, `packages/doeff-agents/src/doeff_agents/handlers/daemon.py`
- tests: `packages/doeff-agents/conformance/README.md`, `packages/doeff-agents/conformance/harness.py`, `packages/doeff-agents/conformance/test_s10_payload_durability.py`, `packages/doeff-agents/conformance/test_s28d_preexisting_orphan_reaper.py`, `packages/doeff-agents/conformance/test_s29_external_seat_adopt.py`, `packages/doeff-agents/tests/agentd_real_agent_result_retry_e2e_support.py`, `packages/doeff-agents/tests/agentd_result_retry_e2e_support.py`, `packages/doeff-agents/tests/test_agentd_byte_faithful_transport_e2e.py`, `packages/doeff-agents/tests/test_agentd_client.py`, `packages/doeff-agents/tests/test_driver_waits_are_time_effects.py`
- docs_examples: `docs/crystallization/constraint-graph-conductor.md`, `docs/design/acp-sessionhost-boundary/target.md`, `docs/proposals/003-doeff-agentd-rust-daemon.md`

### `AgentdClientError`

- src: `packages/doeff-agents/src/doeff_agents/cli.py`, `packages/doeff-agents/src/doeff_agents/handlers/daemon.py`
- tests: `packages/doeff-agents/conformance/test_s10_payload_durability.py`, `packages/doeff-agents/conformance/test_s11_auth_profile_gate.py`, `packages/doeff-agents/conformance/test_s18_dialog_fastpaths.py`, `packages/doeff-agents/conformance/test_s20_schema_vocabulary.py`, `packages/doeff-agents/conformance/test_s21_resume_fork.py`, `packages/doeff-agents/conformance/test_s22_registration_before_ready.py`, `packages/doeff-agents/conformance/test_s23_adopt_ordering.py`, `packages/doeff-agents/tests/test_agentd_client.py`

### `AgentdPaths`

- 呼び手なし

### `AgentdProtocolError`

- tests: `packages/doeff-agents/tests/test_agentd_client.py`
- other_packages: `.semgrep.yaml`

### `AgentdSessionList`

- tests: `packages/doeff-agents/tests/test_agentd_client.py`, `packages/doeff-agents/tests/test_cli_agentd.py`

### `AgentdSessionParseWarning`

- src: `packages/doeff-agents/src/doeff_agents/cli.py`
- tests: `packages/doeff-agents/tests/test_agentd_client.py`, `packages/doeff-agents/tests/test_cli_agentd.py`

### `AgentdSupervisorConfigError`

- src: `packages/doeff-agents/src/doeff_agents/cli.py`
- tests: `packages/doeff-agents/tests/test_agentd_client.py`
- docs_examples: `docs/adr/defadr_doeff_agents_004_effects_session_host.hy`

### `AgentdSupervisorDeclaration`

- 呼び手なし

### `AgentdUnavailableError`

- src: `packages/doeff-agents/src/doeff_agents/cli.py`
- tests: `packages/doeff-agents/tests/test_agentd_client.py`, `packages/doeff-agents/tests/test_cli_agentd.py`

### `ClaudeLaunchEffect`

- src: `packages/doeff-agents/src/doeff_agents/handlers/__init__.py`, `packages/doeff-agents/src/doeff_agents/handlers/claude.hy`, `packages/doeff-agents/src/doeff_agents/handlers/daemon.py`, `packages/doeff-agents/src/doeff_agents/handlers/effectful.hy`, `packages/doeff-agents/src/doeff_agents/handlers/production.py`, `packages/doeff-agents/src/doeff_agents/handlers/testing.hy`, `packages/doeff-agents/src/doeff_agents/runtime.py`
- tests: `packages/doeff-agents/tests/test_claude_integration.py`, `packages/doeff-agents/tests/test_claude_launch_effect.py`, `packages/doeff-agents/tests/test_ready_gate.py`, `packages/doeff-agents/tests/test_refactored_effects.py`, `packages/doeff-agents/tests/test_session_registration_before_ready_gate.py`

### `DaemonAgentHandler`

- src: `packages/doeff-agents/src/doeff_agents/claude_home.py`, `packages/doeff-agents/src/doeff_agents/handlers/session_store.hy`
- tests: `packages/doeff-agents/tests/test_agentd_client.py`
- other_packages: `packages/doeff-conductor/src/doeff_conductor/handlers/agent_handler.py`
- docs_examples: `docs/adr/defadr_doeff_agents_004_effects_session_host.hy`, `docs/crystallization/constraint-graph-conductor.md`, `docs/proposals/003-doeff-agentd-rust-daemon.md`, `packages/doeff-conductor/docs/adr-0001-workflow-dsl-and-attention-tiers.md`

### `InjectionMethod`

- src: `packages/doeff-agents/src/doeff_agents/adapters/__init__.py`, `packages/doeff-agents/src/doeff_agents/adapters/claude.py`, `packages/doeff-agents/src/doeff_agents/adapters/codex.py`, `packages/doeff-agents/src/doeff_agents/adapters/gemini.py`, `packages/doeff-agents/src/doeff_agents/handlers/production.py`, `packages/doeff-agents/src/doeff_agents/session.py`
- tests: `packages/doeff-agents/tests/agent_launch_invariant_deftests.hy`, `packages/doeff-agents/tests/test_agent_mcp_same_vm.py`, `packages/doeff-agents/tests/test_claude_adapter.py`, `packages/doeff-agents/tests/test_claude_launch_effect.py`, `packages/doeff-agents/tests/test_codex_adapter.py`, `packages/doeff-agents/tests/test_inprocess_report_result.py`, `packages/doeff-agents/tests/test_session_backend.py`
- other_packages: `.semgrep.yaml`
- docs_examples: `packages/doeff-agents/examples/04_custom_adapter.py`

### `L2SessionHandle`

- src: `packages/doeff-agents/src/doeff_agents/handlers/daemon.py`, `packages/doeff-agents/src/doeff_agents/handlers/production.py`, `packages/doeff-agents/src/doeff_agents/handlers/testing.hy`, `packages/doeff-agents/src/doeff_agents/v2/__init__.py`
- tests: `packages/doeff-agents/tests/test_l2_session_algebra.py`, `packages/doeff-agents/tests/test_mcp_e2e.py`

### `LaunchConfig`

- src: `packages/doeff-agents/src/doeff_agents/adapters/__init__.py`, `packages/doeff-agents/src/doeff_agents/cli.py`, `packages/doeff-agents/src/doeff_agents/effects/agent.py`, `packages/doeff-agents/src/doeff_agents/programs.py`, `packages/doeff-agents/src/doeff_agents/session.py`
- tests: `packages/doeff-agents/tests/test_agent_session_state.py`, `packages/doeff-agents/tests/test_handler_protocol.py`, `packages/doeff-agents/tests/test_hy_loader_declared_by_importers.py`, `packages/doeff-agents/tests/test_ready_gate.py`, `packages/doeff-agents/tests/test_refactored_effects.py`, `packages/doeff-agents/tests/test_session_backend.py`, `packages/doeff-agents/tests/test_withhandler_agent_delegation.py`
- other_packages: `packages/doeff-agentic/src/doeff_agentic/handlers/production.py`, `tools/docs-video/src/pages/agent-tutorial/measure/batch.py`, `tools/docs-video/src/pages/agent-tutorial/measure/callbacks.py`, `tools/docs-video/src/pages/agent-tutorial/measure/cleanup.py`, `tools/docs-video/src/pages/agent-tutorial/measure/driver_effects.py`, `tools/docs-video/src/pages/agent-tutorial/measure/lifecycle.py`
- docs_examples: `docs/12-mcp-tools.md`, `docs/19-agent-tutorial.md`, `packages/doeff-agents/README.md`, `packages/doeff-agents/examples/01_basic_session.py`, `packages/doeff-agents/examples/02_context_manager.py`, `packages/doeff-agents/examples/03_async_monitoring.py`, `packages/doeff-agents/examples/04_custom_adapter.py`, `packages/doeff-agents/examples/05_status_callbacks.py`, `packages/doeff-agents/examples/06_effects_api.py`, `packages/doeff-agents/examples/README.md`, `packages/doeff-conductor/docs/adr-0001-workflow-dsl-and-attention-tiers.md`

### `LaunchParams`

- src: `packages/doeff-agents/src/doeff_agents/adapters/__init__.py`, `packages/doeff-agents/src/doeff_agents/adapters/claude.py`, `packages/doeff-agents/src/doeff_agents/adapters/codex.py`, `packages/doeff-agents/src/doeff_agents/adapters/gemini.py`, `packages/doeff-agents/src/doeff_agents/handlers/claude.hy`, `packages/doeff-agents/src/doeff_agents/handlers/codex.hy`, `packages/doeff-agents/src/doeff_agents/handlers/daemon.py`, `packages/doeff-agents/src/doeff_agents/handlers/production.py`, `packages/doeff-agents/src/doeff_agents/session.py`, `packages/doeff-agents/src/doeff_agents/sessionhost/host.hy`, `packages/doeff-agents/src/doeff_agents/sessionhost/launch.hy`
- tests: `packages/doeff-agents/tests/agent_launch_invariant_deftests.hy`, `packages/doeff-agents/tests/test_agent_mcp_same_vm.py`, `packages/doeff-agents/tests/test_claude_adapter.py`, `packages/doeff-agents/tests/test_claude_launch_effect.py`, `packages/doeff-agents/tests/test_codex_adapter.py`, `packages/doeff-agents/tests/test_inprocess_report_result.py`, `packages/doeff-agents/tests/test_refactored_effects.py`, `packages/doeff-agents/tests/test_session_backend.py`
- other_packages: `.semgrep.yaml`
- docs_examples: `docs/adr/defadr_doeff_agents_004_effects_session_host.hy`, `docs/design/auto-compact-window/artifacts/target-2026-09-18b/blind/reply-A.md`, `docs/design/auto-compact-window/artifacts/target-2026-09-18c/blind/reply-A.md`, `docs/semgrep-rules-inventory-2026-07-14.md`, `packages/doeff-agents/README.md`, `packages/doeff-agents/examples/04_custom_adapter.py`

### `LazyAgentdClient`

- src: `packages/doeff-agents/src/doeff_agents/handlers/__init__.py`
- tests: `packages/doeff-agents/tests/test_agentd_client.py`
- other_packages: `packages/doeff-conductor/src/doeff_conductor/api.py`, `packages/doeff-conductor/src/doeff_conductor/handlers/agent_handler.py`, `packages/doeff-conductor/src/doeff_conductor/monitor.py`
- docs_examples: `packages/doeff-agents/README.md`, `packages/doeff-conductor/docs/adr-0001-workflow-dsl-and-attention-tiers.md`

### `MOCK_AGENT_STATE_KEY`

- 呼び手なし

### `MockAgentHandler`

- tests: `packages/doeff-agents/tests/test_headless_adapter.hy`, `packages/doeff-agents/tests/test_mcp_e2e.py`
- docs_examples: `packages/doeff-agents/examples/06_effects_api.py`

### `MockAgentState`

- 呼び手なし

### `MonitorState`

- src: `packages/doeff-agents/src/doeff_agents/handlers/claude.hy`, `packages/doeff-agents/src/doeff_agents/handlers/codex.hy`, `packages/doeff-agents/src/doeff_agents/handlers/production.py`, `packages/doeff-agents/src/doeff_agents/session.py`
- tests: `packages/doeff-agents/tests/test_monitor_claude.py`, `packages/doeff-agents/tests/test_monitor_codex.py`
- docs_examples: `packages/doeff-conductor/docs/adr-0001-workflow-dsl-and-attention-tiers.md`

### `OnStatusChange`

- src: `packages/doeff-agents/src/doeff_agents/session.py`

### `ScenarioAgentHandler`

- tests: `packages/doeff-agents/tests/agent_await_result_deftests.hy`, `packages/doeff-agents/tests/test_handler_protocol.py`, `packages/doeff-agents/tests/test_headless_adapter.hy`, `packages/doeff-agents/tests/test_l2_session_algebra.py`
- other_packages: `packages/doeff-conductor/tests/test_agent_journal_c3.py`, `packages/doeff-conductor/tests/test_k4_deadline_owning_axis.py`
- docs_examples: `publications/zenn-use-cases-v0/doeff-agents.md`, `publications/zenn-use-cases-v0/examples/agents_workflow.py`, `publications/zenn-use-cases-v0/reviews/agents.md`

### `SessionConfig`

- src: `packages/doeff-agents/src/doeff_agents/handlers/claude.hy`, `packages/doeff-agents/src/doeff_agents/handlers/codex.hy`, `packages/doeff-agents/src/doeff_agents/handlers/production.py`, `packages/doeff-agents/src/doeff_agents/session.py`, `packages/doeff-agents/src/doeff_agents/tmux.py`
- tests: `packages/doeff-agents/tests/test_claude_launch_effect.py`, `packages/doeff-agents/tests/test_ready_gate.py`, `packages/doeff-agents/tests/test_session_backend.py`
- other_packages: `packages/doeff-agentic/src/doeff_agentic/handlers/tmux.py`, `tools/docs-video/src/pages/agent-tutorial/measure/stand_ins.py`

### `SessionInfo`

- src: `packages/doeff-agents/src/doeff_agents/tmux.py`
- tests: `packages/doeff-agents/tests/test_agent_mcp_same_vm.py`, `packages/doeff-agents/tests/test_claude_handler.py`, `packages/doeff-agents/tests/test_claude_integration.py`, `packages/doeff-agents/tests/test_claude_launch_effect.py`, `packages/doeff-agents/tests/test_codex_handler.py`, `packages/doeff-agents/tests/test_inprocess_report_result.py`, `packages/doeff-agents/tests/test_mixed_agent_handlers.py`, `packages/doeff-agents/tests/test_ready_gate.py`, `packages/doeff-agents/tests/test_session_backend.py`
- other_packages: `tools/docs-video/src/pages/agent-tutorial/measure/stand_ins.py`, `tools/docs-video/src/pages/mcp-tools/measure/agent_flow.py`

### `StableTmuxSessionBackend`

- src: `packages/doeff-agents/src/doeff_agents/session_backend.py`

### `TmuxAgentHandler`

- src: `packages/doeff-agents/src/doeff_agents/claude_home.py`, `packages/doeff-agents/src/doeff_agents/handlers/effectful.hy`, `packages/doeff-agents/src/doeff_agents/handlers/session_store.hy`
- tests: `packages/doeff-agents/tests/test_claude_launch_effect.py`, `packages/doeff-agents/tests/test_inprocess_report_result.py`, `packages/doeff-agents/tests/test_mcp_live_e2e.py`, `packages/doeff-agents/tests/test_ready_gate.py`, `packages/doeff-agents/tests/test_session_backend.py`, `packages/doeff-agents/tests/test_session_registration_before_ready_gate.py`
- other_packages: `.semgrep.yaml`, `packages/doeff-agentic/src/doeff_agentic/handlers/production.py`
- docs_examples: `AGENTS.md`, `CLAUDE.md`, `docs/12-mcp-tools.md`, `docs/adr/defadr_doeff_agents_004_effects_session_host.hy`, `docs/adr/defadr_doeff_agents_005_inprocess_result_transport.hy`, `docs/doeff-2026-07-13-slog-semantics-architecture-plan.md`, `docs/proposals/003-doeff-agentd-rust-daemon.md`, `docs/semgrep-rules-inventory-2026-07-14.md`

### `TmuxError`

- 呼び手なし

### `TmuxNotAvailableError`

- 呼び手なし

### `TmuxSessionBackend`

- src: `packages/doeff-agents/src/doeff_agents/session_backend.py`
- tests: `packages/doeff-agents/tests/test_mcp_live_e2e.py`, `packages/doeff-agents/tests/test_ready_gate.py`, `packages/doeff-agents/tests/test_session_backend.py`
- docs_examples: `AGENTS.md`, `CLAUDE.md`

### `agentd_socket_is_supervised`

- src: `packages/doeff-agents/src/doeff_agents/cli.py`

### `async_monitor_session`

- other_packages: `tools/docs-video/src/pages/agent-tutorial/measure/batch.py`
- docs_examples: `docs/19-agent-tutorial.md`

### `async_session_scope`

- other_packages: `tools/docs-video/src/pages/agent-tutorial/measure/batch.py`, `tools/docs-video/src/pages/agent-tutorial/measure/stand_ins.py`
- docs_examples: `docs/19-agent-tutorial.md`

### `attach_session`

- src: `packages/doeff-agents/src/doeff_agents/cli.py`, `packages/doeff-agents/src/doeff_agents/handlers/production.py`, `packages/doeff-agents/src/doeff_agents/session_backend.py`, `packages/doeff-agents/src/doeff_agents/tmux.py`
- tests: `packages/doeff-agents/tests/test_claude_launch_effect.py`, `packages/doeff-agents/tests/test_ready_gate.py`, `packages/doeff-agents/tests/test_session_backend.py`
- other_packages: `packages/doeff-agentic/src/doeff_agentic/api.py`, `packages/doeff-agentic/src/doeff_agentic/handlers/tmux.py`, `tools/docs-video/src/pages/agent-tutorial/measure/stand_ins.py`

### `capture_output`

- src: `packages/doeff-agents/src/doeff_agents/sessionhost/substrate.hy`
- tests: `packages/doeff-agents/conformance/harness.py`, `packages/doeff-agents/conformance/test_s28d_preexisting_orphan_reaper.py`, `packages/doeff-agents/tests/agentd_real_agent_result_retry_e2e_support.py`, `packages/doeff-agents/tests/test_hy_loader_declared_by_importers.py`, `packages/doeff-agents/tests/test_mcp_live_e2e.py`, `packages/doeff-agents/tests/test_session_backend.py`, `tests/semgrep/test_vm_failfast_semgrep_rules.py`, `tests/test_daily_test_population.py`, `tests/test_zenn_record_sweep.py`
- other_packages: `.semgrep.yaml`, `conftest.py`, `packages/doeff-agentic/src/doeff_agentic/handlers/tmux.py`, `packages/doeff-conductor/src/doeff_conductor/api.py`, `packages/doeff-conductor/tests/test_agent_effect_c1.py`, `tools/docs-video/src/pages/agent-tutorial/measure/cleanup.py`, `tools/docs-video/src/pages/agent-tutorial/measure/lifecycle.py`
- docs_examples: `docs/19-agent-tutorial.md`, `docs/design-checks/lt-R12TTZXKQPKRPSE5342853KM0V/blind/blind-b-reply.md`, `docs/design/test-population-ki-08ec/evidence/proto/pins.py`
- agora-controllers: `controllers/agent_task/tests/test_worktree_diffs.hy`, `controllers/agora_sim/tests/test_screen_test_reads_no_aiohttp.hy`, `scripts/tests/test_cloud_run_tests_tools.hy`

### `configure_mock_session`

- tests: `packages/doeff-agents/tests/test_agent_session_state.py`, `packages/doeff-agents/tests/test_handler_protocol.py`
- docs_examples: `docs/doeff-2026-07-13-slog-semantics-architecture-plan.md`, `packages/doeff-agents/examples/01_basic_session.py`, `packages/doeff-agents/examples/02_context_manager.py`, `packages/doeff-agents/examples/03_async_monitoring.py`, `packages/doeff-agents/examples/04_custom_adapter.py`, `packages/doeff-agents/examples/05_status_callbacks.py`, `packages/doeff-agents/examples/06_effects_api.py`, `packages/doeff-agents/examples/README.md`

### `default_agent_handler`

- tests: `packages/doeff-agents/tests/test_session_backend.py`
- docs_examples: `AGENTS.md`, `CLAUDE.md`
- agora-controllers: `controllers/foundation/tests/test_managed_mark.hy`

### `default_agentd_paths`

- src: `packages/doeff-agents/src/doeff_agents/cli.py`
- tests: `packages/doeff-agents/tests/test_agentd_client.py`

### `default_session_backend`

- tests: `packages/doeff-agents/tests/test_session_backend.py`
- docs_examples: `AGENTS.md`, `CLAUDE.md`, `publications/zenn-use-cases-v0/doeff-agents.md`, `publications/zenn-use-cases-v0/examples/agents_workflow.py`, `publications/zenn-use-cases-v0/reviews/agents.md`

### `deterministic_session_id`

- src: `packages/doeff-agents/src/doeff_agents/v2/__init__.py`
- tests: `packages/doeff-agents/tests/agent_await_result_deftests.hy`
- other_packages: `packages/doeff-conductor/src/doeff_conductor/effects/agent.py`
- docs_examples: `packages/doeff-conductor/docs/adr-0002-read-only-run-monitor.md`

### `dispatch_effect`

- 呼び手なし

### `ensure_agentd`

- src: `packages/doeff-agents/src/doeff_agents/cli.py`
- tests: `packages/doeff-agents/conformance/harness.py`, `packages/doeff-agents/tests/test_agentd_client.py`, `packages/doeff-agents/tests/test_cli_agentd.py`
- other_packages: `.semgrep.yaml`
- docs_examples: `docs/adr/defadr_doeff_agents_004_effects_session_host.hy`, `docs/proposals/003-doeff-agentd-rust-daemon.md`, `packages/doeff-agents/README.md`

### `fake_headless_claude_agent_handlers`

- tests: `packages/doeff-agents/tests/test_headless_adapter.hy`
- docs_examples: `packages/doeff-agents/README.md`
- agora-controllers: `controllers/agora_sim/tests/test_continued_turn_closed_watch.hy`, `controllers/agora_sim/tests/test_emulated_agent_task_translation.hy`, `controllers/agora_sim/tests/test_exec_ticket_ends_turn.hy`, `controllers/agora_sim/tests/test_long_tool_turn_is_not_refused.hy`, `controllers/agora_sim/tests/test_revoked_continuation_ends.hy`, `controllers/agora_sim/tests/test_task_attempt_events.hy`, `controllers/agora_sim/tests/test_tool_contents_on_task.hy`, `controllers/agora_sim/tests/test_turn_body_before_head.hy`, `controllers/agora_sim/tests/test_turn_cost_across_segments.hy`, `controllers/foundation/tests/test_managed_mark.hy`

### `get_adapter`

- src: `packages/doeff-agents/src/doeff_agents/handlers/__init__.py`, `packages/doeff-agents/src/doeff_agents/handlers/daemon.py`, `packages/doeff-agents/src/doeff_agents/handlers/production.py`
- tests: `packages/doeff-agents/tests/test_agent_mcp_same_vm.py`, `packages/doeff-agents/tests/test_agentd_client.py`, `packages/doeff-agents/tests/test_claude_launch_effect.py`, `packages/doeff-agents/tests/test_inprocess_report_result.py`, `packages/doeff-agents/tests/test_ready_gate.py`, `packages/doeff-agents/tests/test_session_backend.py`, `packages/doeff-agents/tests/test_session_registration_before_ready_gate.py`
- other_packages: `packages/doeff-conductor/tests/test_agent_effect_c1.py`

### `get_mock_agent_state`

- 呼び手なし

### `headless_claude_agent_handlers`

- tests: `packages/doeff-agents/tests/test_headless_adapter.hy`
- docs_examples: `packages/doeff-agents/README.md`
- agora-controllers: `controllers/foundation/tests/test_managed_mark.hy`

### `interactive_session`

- tests: `packages/doeff-agents/tests/test_withhandler_agent_delegation.py`

### `launch_session`

- src: `packages/doeff-agents/src/doeff_agents/agentd_client.py`, `packages/doeff-agents/src/doeff_agents/cli.py`, `packages/doeff-agents/src/doeff_agents/handlers/daemon.py`, `packages/doeff-agents/src/doeff_agents/sessionhost/host.hy`, `packages/doeff-agents/src/doeff_agents/sessionhost/launch.hy`, `packages/doeff-agents/src/doeff_agents/sessionhost/policy.hy`
- tests: `packages/doeff-agents/conformance/harness.py`, `packages/doeff-agents/conformance/test_s28d_preexisting_orphan_reaper.py`, `packages/doeff-agents/tests/agentd_real_agent_result_retry_e2e_support.py`, `packages/doeff-agents/tests/agentd_result_retry_e2e_support.py`, `packages/doeff-agents/tests/sessionhost_launch_deftests.hy`, `packages/doeff-agents/tests/sessionhost_resume_cross_binding_deftests.hy`, `packages/doeff-agents/tests/sessionhost_resume_deftests.hy`, `packages/doeff-agents/tests/test_agentd_byte_faithful_transport_e2e.py`, `packages/doeff-agents/tests/test_agentd_client.py`, `packages/doeff-agents/tests/test_ready_gate.py`, `packages/doeff-agents/tests/test_session_backend.py`
- other_packages: `packages/doeff-conductor/tests/test_agent_effect_c1.py`, `tools/docs-video/src/pages/agent-tutorial/measure/driver_effects.py`, `tools/docs-video/src/pages/agent-tutorial/measure/lifecycle.py`
- docs_examples: `docs/19-agent-tutorial.md`, `docs/adr/defadr_doeff_enforce_002_semgrep_p0_fixtures.hy`, `packages/doeff-conductor/docs/adr-0001-workflow-dsl-and-attention-tiers.md`
- agora-controllers: `controllers/agent_task/protocol/agent_turn.hy`, `controllers/agora_sim/tests/test_cli_process_layer_by_node.hy`, `controllers/agora_sim/tests/test_cli_turn_starts.hy`, `controllers/agora_sim/tests/test_emulated_agent_task.hy`, `controllers/agora_sim/tests/test_emulated_agent_task_translation.hy`, `controllers/agora_sim/tests/test_turn_compaction_threshold.hy`, `controllers/foundation/agent_runtime.hy`

### `load_supervisor_declaration`

- 呼び手なし

### `mock_handlers`

- other_packages: `.semgrep.yaml`, `packages/doeff-agentic/src/doeff_agentic/__init__.py`, `packages/doeff-agentic/tests/test_handlers_module.py`, `packages/doeff-conductor/src/doeff_conductor/handlers/testing.py`, `packages/doeff-conductor/tests/test_agent_effect_c1.py`, `packages/doeff-conductor/tests/test_agent_journal_c3.py`, `packages/doeff-conductor/tests/test_k4_deadline_owning_axis.py`
- agora-controllers: `controllers/foundation/tests/test_managed_mark.hy`

### `monitor_agent_to_completion`

- tests: `packages/doeff-agents/tests/test_withhandler_agent_delegation.py`

### `monitor_once`

- src: `packages/doeff-agents/src/doeff_agents/sessionhost/policy.hy`
- tests: `packages/doeff-agents/tests/sessionhost_policy_deftests.hy`
- docs_examples: `docs/adr/defadr_doeff_agents_002_agentd_tui_boundary_contracts.hy`

### `monitor_session`

- src: `packages/doeff-agents/src/doeff_agents/cli.py`
- tests: `packages/doeff-agents/tests/test_cli_agentd.py`, `packages/doeff-agents/tests/test_session_backend.py`
- other_packages: `.semgrep.yaml`, `tools/docs-video/src/pages/agent-tutorial/measure/callbacks.py`, `tools/docs-video/src/pages/agent-tutorial/measure/cleanup.py`, `tools/docs-video/src/pages/agent-tutorial/measure/lifecycle.py`
- docs_examples: `docs/19-agent-tutorial.md`, `packages/doeff-agents/README.md`, `packages/doeff-conductor/docs/adr-0001-workflow-dsl-and-attention-tiers.md`

### `monitor_until_terminal`

- 呼び手なし

### `production_handlers`

- other_packages: `.semgrep.yaml`, `packages/doeff-agentic/src/doeff_agentic/__init__.py`, `packages/doeff-agentic/tests/test_handlers_module.py`, `packages/doeff-conductor/src/doeff_conductor/api.py`, `packages/doeff-conductor/tests/test_k4_deadline_owning_axis.py`
- docs_examples: `CHANGELOG.md`
- agora-controllers: `controllers/foundation/tests/test_managed_mark.hy`

### `quick_agent`

- 呼び手なし

### `register_adapter`

- src: `packages/doeff-agents/src/doeff_agents/handlers/__init__.py`, `packages/doeff-agents/src/doeff_agents/handlers/production.py`

### `run_agent_to_completion`

- tests: `packages/doeff-agents/tests/test_handler_protocol.py`, `packages/doeff-agents/tests/test_withhandler_agent_delegation.py`
- docs_examples: `packages/doeff-agents/examples/06_effects_api.py`, `packages/doeff-agents/examples/README.md`

### `send_message`

- tests: `packages/doeff-agents/tests/test_session_backend.py`
- other_packages: `.semgrep.yaml`, `packages/doeff-agentic/src/doeff_agentic/api.py`, `tools/docs-video/src/pages/agent-tutorial/measure/callbacks.py`
- docs_examples: `packages/doeff-agentic/README.md`, `packages/doeff-agents/README.md`

### `session_scope`

- other_packages: `tools/docs-video/src/pages/agent-tutorial/measure/callbacks.py`, `tools/docs-video/src/pages/agent-tutorial/measure/cleanup.py`, `tools/docs-video/src/pages/agent-tutorial/measure/stand_ins.py`
- docs_examples: `docs/19-agent-tutorial.md`, `packages/doeff-agents/README.md`, `packages/doeff-conductor/docs/adr-0001-workflow-dsl-and-attention-tiers.md`

### `stop_session`

- tests: `packages/doeff-agents/tests/test_session_backend.py`
- other_packages: `tools/docs-video/src/pages/agent-tutorial/measure/cleanup.py`, `tools/docs-video/src/pages/agent-tutorial/measure/lifecycle.py`
- docs_examples: `docs/19-agent-tutorial.md`

### `wait_agent_session`

- tests: `packages/doeff-agents/tests/test_agent_session_state.py`

### `wait_and_monitor`

- 呼び手なし

### `with_session`

- docs_examples: `packages/doeff-agents/examples/02_context_manager.py`, `packages/doeff-agents/examples/06_effects_api.py`, `packages/doeff-agents/examples/README.md`

## 7. submodule の決め

公開に残す module は 4 つ。ほかは名を `_` で始まる内側の module へ移す(段 A = card ki-4b53dcee28f3・段 B = 後の card)。

| module | 決め | 公開の名の数 | import する file(doeff の外の package / doeff-agents の中) | agora-controllers |
|---|---|---|---|---|
| `doeff_agents` | 公開(残す) — top-level — effect・handler・境界の値だけを出す | 0 | 223 / 52 | 87 |
| `doeff_agents.effects` | 公開(残す) — effect と境界の値 | 128 | 35 / 18 | 36 |
| `doeff_agents.effects.agent` | 公開(残す) — effect と値の定義元。関数 deterministic_session_id・refuse_turn_capabilities と ClaudeLaunchEffect・LaunchTaskEffect(互換の別名)を外す | 123 | 13 / 8 | 19 |
| `doeff_agents.handlers` | 公開(残す) — handler の factory と handler の引数の値だけ。class と関数を外す | 40 | 35 / 9 | 37 |
| `doeff_agents.session` | 段 A — doeff_agents._session | 15 | 6 / 7 | 1 |
| `doeff_agents.programs` | 段 A — 消す(呼び手を effect の組へ移す) | 14 | 0 / 1 | 0 |
| `doeff_agents.monitor` | 段 A — doeff_agents._monitor(SessionStatus は effects/agent.py へ) | 15 | 7 / 13 | 0 |
| `doeff_agents.v2` | 段 A — 消す(同じ effect の 2 つ目の語彙・呼び手は自分の test 1 つ) | 20 | 1 / 0 | 0 |
| `doeff_agents.session_backend` | 段 B — doeff_agents._session_backend | 5 | 12 / 5 | 0 |
| `doeff_agents.tmux` | 段 B — doeff_agents._tmux | 48 | 17 / 3 | 0 |
| `doeff_agents.shell` | 段 B — doeff_agents._shell | 4 | 1 / 8 | 0 |
| `doeff_agents.agentd_client` | 段 B — doeff_agents._agentd_client | 16 | 20 / 5 | 0 |
| `doeff_agents.adapters` | 段 B — doeff_agents._adapters(AgentType・AgentSessionLifecycle は effects/agent.py へ) | 29 | 29 / 19 | 4 |
| `doeff_agents.runtime` | 段 B — doeff_agents._runtime(ClaudeRuntimePolicy・CodexRuntimePolicy は handlers から出す) | 3 | 5 / 4 | 0 |
| `doeff_agents.session_store` | 段 B — doeff_agents._session_store | 7 | 3 / 3 | 0 |
| `doeff_agents.session_store_sql` | 段 B — doeff_agents._session_store_sql | 21 | 1 / 1 | 0 |
| `doeff_agents.io_effects` | 段 B — doeff_agents._io_effects | 19 | 6 / 11 | 0 |
| `doeff_agents.io_fake` | 段 B — doeff_agents._io_fake | 12 | 8 / 5 | 0 |
| `doeff_agents.io_handlers` | 段 B — doeff_agents._io_handlers | 2 | 7 / 10 | 0 |
| `doeff_agents.io_root` | 段 B — doeff_agents._io_root | 9 | 4 / 11 | 0 |
| `doeff_agents.agent_env` | 段 B — doeff_agents._agent_env | 9 | 2 / 3 | 0 |
| `doeff_agents.claude_home` | 段 B — doeff_agents._claude_home | 3 | 2 / 2 | 0 |
| `doeff_agents.ready_physics` | 段 B — doeff_agents._ready_physics | 3 | 1 / 3 | 0 |
| `doeff_agents.result_validation` | 段 B — doeff_agents._result_validation | 1 | 5 / 1 | 0 |
| `doeff_agents.mcp_server` | 段 B — doeff_agents._mcp_server | 8 | 5 / 0 | 0 |
| `doeff_agents.cli` | 段 B — doeff_agents._cli(console script doeff-agents の入口を移す) | 20 | 3 / 0 | 0 |
| `doeff_agents.sessionhost` | 段 B — doeff_agents._sessionhost(console script doeff-sessionhost の入口を移す・ADR-004/007 の path と .semgrep.yaml の include を同じ便で直す) | 607 | 56 / 18 | 3 |
| `doeff_agents.handlers.claude` | 段 B — doeff_agents.handlers._claude | 1 | 3 / 1 | 0 |
| `doeff_agents.handlers.codex` | 段 B — doeff_agents.handlers._codex | 1 | 4 / 1 | 0 |
| `doeff_agents.handlers.daemon` | 段 B — doeff_agents.handlers._daemon | 2 | 4 / 1 | 0 |
| `doeff_agents.handlers.effectful` | 段 B — doeff_agents.handlers._effectful | 4 | 0 / 2 | 0 |
| `doeff_agents.handlers.headless` | 段 B — doeff_agents.handlers._headless | 47 | 2 / 3 | 0 |
| `doeff_agents.handlers.headless_codex` | 段 B — doeff_agents.handlers._headless_codex | 40 | 1 / 2 | 1 |
| `doeff_agents.handlers.headless_compose` | 段 B — doeff_agents.handlers._headless_compose(FakeReply・FakeClaudeWorld・ClaudeLiveLimitExceeded は doeff_agents.handlers から出す) | 8 | 4 / 1 | 34 |
| `doeff_agents.handlers.mcp_server_loop` | 段 B — doeff_agents.handlers._mcp_server_loop | 2 | 3 / 0 | 0 |
| `doeff_agents.handlers.production` | 段 B — doeff_agents.handlers._production | 8 | 8 / 4 | 0 |
| `doeff_agents.handlers.session_store` | 段 B — doeff_agents.handlers._session_store | 3 | 1 / 1 | 0 |
| `doeff_agents.handlers.testing` | 段 B — doeff_agents.handlers._testing | 5 | 7 / 1 | 0 |

## 8. 内側へ移す submodule を import する file(doeff-agents の src の外)

### `doeff_agents.session`

- `.semgrep.yaml`
- `packages/doeff-agents/tests/test_agentd_client.py`
- `packages/doeff-agents/tests/test_onboarding_dismiss.py`
- `packages/doeff-agents/tests/test_ready_gate.py`
- `packages/doeff-agents/tests/test_session_backend.py`
- `tools/docs-video/src/pages/agent-tutorial/measure/stand_ins.py`
- agora-controllers: `controllers/runtime_env/core/declaration.hy`

### `doeff_agents.programs`


### `doeff_agents.monitor`

- `packages/doeff-agentic/tests/test_handlers_module.py`
- `packages/doeff-agents/tests/session_store_laws.hy`
- `packages/doeff-agents/tests/test_headless_adapter.hy`
- `packages/doeff-agents/tests/test_headless_codex_adapter.hy`
- `packages/doeff-agents/tests/test_monitor_claude.py`
- `packages/doeff-agents/tests/test_monitor_codex.py`
- `packages/doeff-agents/tests/test_session_registration_before_ready_gate.py`

### `doeff_agents.v2`

- `packages/doeff-agents/tests/test_v2_agent_api.py`

### `doeff_agents.session_backend`

- `AGENTS.md`
- `CLAUDE.md`
- `packages/doeff-agents/tests/test_agent_mcp_same_vm.py`
- `packages/doeff-agents/tests/test_claude_integration.py`
- `packages/doeff-agents/tests/test_claude_launch_effect.py`
- `packages/doeff-agents/tests/test_codex_handler.py`
- `packages/doeff-agents/tests/test_inprocess_report_result.py`
- `packages/doeff-agents/tests/test_ready_gate.py`
- `packages/doeff-agents/tests/test_session_backend.py`
- `publications/zenn-use-cases-v0/doeff-agents.md`
- `publications/zenn-use-cases-v0/examples/agents_workflow.py`
- `tools/docs-video/src/pages/agent-tutorial/measure/stand_ins.py`

### `doeff_agents.tmux`

- `AGENTS.md`
- `CLAUDE.md`
- `docs/adr/defadr_doeff_agents_010_unsubmitted_prompt_terminal_sweep.hy`
- `docs/adr/defadr_doeff_agents_013_driver_io_through_handlers.hy`
- `packages/doeff-agentic/src/doeff_agentic/api.py`
- `packages/doeff-agentic/src/doeff_agentic/handlers/tmux.py`
- `packages/doeff-agents/tests/test_agent_mcp_same_vm.py`
- `packages/doeff-agents/tests/test_claude_handler.py`
- `packages/doeff-agents/tests/test_claude_integration.py`
- `packages/doeff-agents/tests/test_codex_handler.py`
- `packages/doeff-agents/tests/test_driver_waits_are_time_effects.py`
- `packages/doeff-agents/tests/test_inprocess_report_result.py`
- `packages/doeff-agents/tests/test_mcp_live_e2e.py`
- `packages/doeff-agents/tests/test_mixed_agent_handlers.py`
- `packages/doeff-agents/tests/test_ready_gate.py`
- `packages/doeff-agents/tests/test_session_backend.py`
- `tools/docs-video/src/pages/mcp-tools/measure/agent_flow.py`

### `doeff_agents.shell`

- `packages/doeff-agents/tests/test_shell_env_guard.py`

### `doeff_agents.agentd_client`

- `.semgrep.yaml`
- `docs/adr/defadr_doeff_agents_004_effects_session_host.hy`
- `packages/doeff-agents/conformance/harness.py`
- `packages/doeff-agents/conformance/test_s10_payload_durability.py`
- `packages/doeff-agents/conformance/test_s11_auth_profile_gate.py`
- `packages/doeff-agents/conformance/test_s18_dialog_fastpaths.py`
- `packages/doeff-agents/conformance/test_s20_schema_vocabulary.py`
- `packages/doeff-agents/conformance/test_s21_resume_fork.py`
- `packages/doeff-agents/conformance/test_s22_registration_before_ready.py`
- `packages/doeff-agents/conformance/test_s23_adopt_ordering.py`
- `packages/doeff-agents/conformance/test_s28d_preexisting_orphan_reaper.py`
- `packages/doeff-agents/conformance/test_s29_external_seat_adopt.py`
- `packages/doeff-agents/tests/agentd_real_agent_result_retry_e2e_support.py`
- `packages/doeff-agents/tests/agentd_result_retry_e2e_support.py`
- `packages/doeff-agents/tests/test_agentd_byte_faithful_transport_e2e.py`
- `packages/doeff-agents/tests/test_agentd_client.py`
- `packages/doeff-agents/tests/test_cli_agentd.py`
- `packages/doeff-agents/tests/test_driver_waits_are_time_effects.py`
- `packages/doeff-agents/tests/test_session_backend.py`
- `packages/doeff-conductor/tests/test_agent_effect_c1.py`

### `doeff_agents.adapters`

- `docs/12-mcp-tools.md`
- `docs/adr/defadr_doeff_agents_008_ready_physics_single_home.hy`
- `packages/doeff-agentic/tests/test_handlers_module.py`
- `packages/doeff-agents/conformance/harness.py`
- `packages/doeff-agents/conformance/test_s26_interactive_not_reaped.py`
- `packages/doeff-agents/examples/04_custom_adapter.py`
- `packages/doeff-agents/tests/agentd_real_agent_result_retry_e2e_support.py`
- `packages/doeff-agents/tests/session_store_laws.hy`
- `packages/doeff-agents/tests/test_agent_mcp_same_vm.py`
- `packages/doeff-agents/tests/test_claude_adapter.py`
- `packages/doeff-agents/tests/test_claude_handler.py`
- `packages/doeff-agents/tests/test_claude_integration.py`
- `packages/doeff-agents/tests/test_claude_launch_effect.py`
- `packages/doeff-agents/tests/test_codex_adapter.py`
- `packages/doeff-agents/tests/test_codex_handler.py`
- `packages/doeff-agents/tests/test_headless_adapter.hy`
- `packages/doeff-agents/tests/test_headless_codex_adapter.hy`
- `packages/doeff-agents/tests/test_hy_loader_declared_by_importers.py`
- `packages/doeff-agents/tests/test_inprocess_report_result.py`
- `packages/doeff-agents/tests/test_mcp_e2e.py`
- `packages/doeff-agents/tests/test_mcp_live_e2e.py`
- `packages/doeff-agents/tests/test_mixed_agent_handlers.py`
- `packages/doeff-agents/tests/test_ready_gate.py`
- `packages/doeff-agents/tests/test_ready_physics_single_home.py`
- `packages/doeff-agents/tests/test_refactored_effects.py`
- `packages/doeff-agents/tests/test_session_backend.py`
- `packages/doeff-agents/tests/test_session_registration_before_ready_gate.py`
- `tests/semgrep/fixtures/python/packages/doeff-agents/src/doeff_agents/agent_api_key_env.py`
- `tools/docs-video/src/pages/mcp-tools/measure/agent_flow.py`
- agora-controllers: `controllers/agora_sim/tests/test_cli_process_layer_by_node.hy`
- agora-controllers: `controllers/agora_sim/tests/test_cli_turn_starts.hy`
- agora-controllers: `controllers/foundation/tests/test_claude_runtime_contract.hy`
- agora-controllers: `controllers/runtime_env/core/declaration.hy`

### `doeff_agents.runtime`

- `packages/doeff-agents/tests/test_agentd_client.py`
- `packages/doeff-agents/tests/test_launch_lowering.py`
- `packages/doeff-agents/tests/test_ready_gate.py`
- `packages/doeff-agents/tests/test_session_backend.py`
- `packages/doeff-agents/tests/test_session_registration_before_ready_gate.py`

### `doeff_agents.session_store`

- `packages/doeff-agents/tests/test_session_backend.py`
- `packages/doeff-agents/tests/test_session_registration_before_ready_gate.py`
- `packages/doeff-agents/tests/test_session_store_handlers.hy`

### `doeff_agents.session_store_sql`

- `packages/doeff-agents/tests/test_session_store_handlers.hy`

### `doeff_agents.io_effects`

- `CHANGELOG.md`
- `docs/adr/defadr_doeff_agents_013_driver_io_through_handlers.hy`
- `packages/doeff-agents/tests/fake_io_support.py`
- `packages/doeff-agents/tests/test_driver_io_contract.hy`
- `packages/doeff-agents/tests/test_io_effects_reexports.hy`
- `packages/doeff-agents/tests/test_session_backend.py`

### `doeff_agents.io_fake`

- `docs/adr/defadr_doeff_agents_013_driver_io_through_handlers.hy`
- `packages/doeff-agents/tests/driver_io_contract_handlers.hy`
- `packages/doeff-agents/tests/fake_io_support.py`
- `packages/doeff-agents/tests/test_agentd_client.py`
- `packages/doeff-agents/tests/test_driver_waits_are_time_effects.py`
- `packages/doeff-agents/tests/test_session_store_handlers.hy`
- `tools/docs-video/src/pages/agent-tutorial/measure/driver_effects.py`
- `tools/docs-video/src/pages/agent-tutorial/measure/stand_ins.py`

### `doeff_agents.io_handlers`

- `docs/adr/defadr_doeff_agents_013_driver_io_through_handlers.hy`
- `packages/doeff-agents/examples/04_custom_adapter.py`
- `packages/doeff-agents/tests/agentd_real_agent_result_retry_e2e_support.py`
- `packages/doeff-agents/tests/driver_io_contract_handlers.hy`
- `packages/doeff-agents/tests/test_agentd_client.py`
- `packages/doeff-agents/tests/test_driver_waits_are_time_effects.py`
- `packages/doeff-agents/tests/test_session_backend.py`

### `doeff_agents.io_root`

- `packages/doeff-agents/tests/fake_io_support.py`
- `packages/doeff-agents/tests/test_agentd_client.py`
- `packages/doeff-agents/tests/test_driver_waits_are_time_effects.py`
- `packages/doeff-agents/tests/test_session_backend.py`

### `doeff_agents.agent_env`

- `.semgrep.yaml`
- `packages/doeff-agents/tests/test_headless_adapter.hy`

### `doeff_agents.claude_home`

- `packages/doeff-agents/tests/agentd_real_agent_result_retry_e2e_support.py`
- `packages/doeff-agents/tests/test_hy_loader_declared_by_importers.py`

### `doeff_agents.ready_physics`

- `docs/adr/defadr_doeff_agents_008_ready_physics_single_home.hy`

### `doeff_agents.result_validation`

- `packages/doeff-agents/tests/test_session_backend.py`
- `packages/doeff-conductor/src/doeff_conductor/handlers/testing.py`
- `packages/doeff-conductor/src/doeff_conductor/journal.py`
- `packages/doeff-conductor/tests/test_agent_effect_c1.py`
- `packages/doeff-conductor/tests/test_attention_tiers_c5.py`

### `doeff_agents.mcp_server`

- `packages/doeff-agents/tests/test_inprocess_report_result.py`
- `packages/doeff-agents/tests/test_mcp_server.py`
- `packages/doeff-agents/tests/test_mcp_server_loop.py`
- `packages/doeff-agents/tests/test_mcp_server_loop_scheduler_capture_regression.py`
- `tests/test_mcp_json_type_table.py`

### `doeff_agents.cli`

- `docs/adr/defadr_doeff_agents_004_effects_session_host.hy`
- `packages/doeff-agents/pyproject.toml`
- `packages/doeff-agents/tests/test_cli_agentd.py`

### `doeff_agents.sessionhost`

- `CHANGELOG.md`
- `docs/adr/defadr_doeff_agents_002_agentd_tui_boundary_contracts.hy`
- `docs/adr/defadr_doeff_agents_004_effects_session_host.hy`
- `docs/adr/defadr_doeff_agents_007_koine_session_surface.hy`
- `docs/adr/defadr_doeff_agents_008_ready_physics_single_home.hy`
- `docs/adr/defadr_doeff_agents_009_observation_supply_cut.hy`
- `docs/adr/defadr_doeff_agents_010_unsubmitted_prompt_terminal_sweep.hy`
- `docs/adr/defadr_doeff_agents_011_launch_readiness_and_prompt_delivery.hy`
- `docs/design-checks/lt-N23MQ5ZMSM6KCDKCB0G2RTFCAH/evidence/race_b_probe.py`
- `docs/design-checks/lt-R12TTZXKQPKRPSE5342853KM0V/blind/blind-a-reply.md`
- `docs/design/acp-sessionhost-boundary/responsibilities.md`
- `docs/design/auto-compact-window/artifacts/target-2026-09-18b/blind/reply-A.md`
- `docs/design/auto-compact-window/artifacts/target-2026-09-18c/blind/reply-A.md`
- `docs/design/daily-red-639/frozen-table-cache/evidence/blind-a/retryable_flip_probe.py`
- `docs/design/daily-red-639/frozen-table-cache/evidence/blind-b/probe.py`
- `docs/design/daily-red-639/frozen-table-cache/evidence/experiment_s3.py`
- `docs/design/daily-red-639/frozen-table-cache/evidence/repro_b_probe.py`
- `docs/design/daily-red-639/herdr-argv0/blind/blind-b-return.md`
- `docs/design/daily-red-639/herdr-argv0/evidence/blind_a_repro.hy`
- `docs/design/daily-red-639/herdr-argv0/evidence/blind_b_violation.hy`
- `docs/design/daily-red-639/herdr-argv0/evidence/experiments_scenarios-72da0ff1.hy`
- `docs/design/daily-red-639/herdr-argv0/evidence/experiments_scenarios.hy`
- `docs/design/daily-red-639/sessionhost-defk/evidence/experiments_scenarios.hy`
- `docs/design/daily-red-639/sessionhost-defk/evidence/repro_counterexamples.hy`
- `docs/design/seat-home-common-instructions-AJ8C0B/counterexamples/repro_A_real_substrate.py`
- `docs/design/seat-home-common-instructions-AJ8C0B/evidence/symlink_install_race_real_fn.py`
- `docs/design/seat-home-common-instructions-AJ8C0B/evidence/symlink_install_residual_doors.py`
- `docs/design/seat-record-destination-ki-e930/counterexamples/B_witness_continue.py`
- `docs/design/seat-record-destination-ki-e930/evidence/proto_arm_on_node_env_change.py`
- `docs/design/symlink-verbs-fail-vocabulary-ZCN5BD/evidence/link_artifact_doors.py`
- `docs/design/symlink-verbs-fail-vocabulary-ZCN5BD/evidence/replace_reach_darwin.py`
- `packages/doeff-agents/conformance/test_s17_endpoint_parity.py`
- `packages/doeff-agents/pyproject.toml`
- `packages/doeff-agents/tests/memory_store_world.hy`
- `packages/doeff-agents/tests/session_store_contract_handlers.hy`
- `packages/doeff-agents/tests/sessionhost_host_deftests.hy`
- `packages/doeff-agents/tests/sessionhost_impls_deftests.hy`
- `packages/doeff-agents/tests/sessionhost_launch_deftests.hy`
- `packages/doeff-agents/tests/sessionhost_policy_deftests.hy`
- `packages/doeff-agents/tests/sessionhost_resume_cross_binding_deftests.hy`
- `packages/doeff-agents/tests/sessionhost_resume_deftests.hy`
- `packages/doeff-agents/tests/sessionhost_schema_deftests.hy`
- `packages/doeff-agents/tests/sessionhost_store_deftests.hy`
- `packages/doeff-agents/tests/sessionhost_substrate_deftests.hy`
- `packages/doeff-agents/tests/sessionhost_substrate_herdr_deftests.hy`
- `packages/doeff-agents/tests/sessionhost_transcript_rebuild_deftests.hy`
- `packages/doeff-agents/tests/sessionhost_turn_deftests.hy`
- `packages/doeff-agents/tests/sessionhost_unsubmitted_prompt_sweep_deftests.hy`
- `packages/doeff-agents/tests/test_env_vocabulary_single_point.py`
- `packages/doeff-agents/tests/test_headless_adapter.hy`
- `packages/doeff-agents/tests/test_jsonschema_official_suite.py`
- `packages/doeff-agents/tests/test_provider_failure_markers.py`
- `packages/doeff-agents/tests/test_ready_gate.py`
- `packages/doeff-agents/tests/test_ready_physics_single_home.py`
- `packages/doeff-agents/tests/test_session_store_contract.hy`
- `tests/semgrep/fixtures/python/packages/doeff-agents/src/doeff_agents/sessionhost/idle_shell_vocabulary_outside_policy_forbidden.hy`
- agora-controllers: `controllers/agora_sim/adr/defadr_turn_boundary.hy`
- agora-controllers: `controllers/agora_sim/tests/test_headless_route_on_stub_cli.hy`
- agora-controllers: `scripts/turn_boundary_examples.hy`

### `doeff_agents.handlers.claude`

- `packages/doeff-agents/tests/test_claude_handler.py`
- `packages/doeff-agents/tests/test_mixed_agent_handlers.py`
- `packages/doeff-agents/tests/test_ready_gate.py`

### `doeff_agents.handlers.codex`

- `packages/doeff-agents/tests/test_codex_handler.py`
- `packages/doeff-agents/tests/test_headless_adapter.hy`
- `packages/doeff-agents/tests/test_mixed_agent_handlers.py`
- `packages/doeff-agents/tests/test_ready_gate.py`

### `doeff_agents.handlers.daemon`

- `packages/doeff-agents/architecture.hy`
- `packages/doeff-agents/tests/test_agentd_client.py`
- `packages/doeff-conductor/src/doeff_conductor/handlers/agent_handler.py`
- `packages/doeff-conductor/tests/test_agent_effect_c1.py`

### `doeff_agents.handlers.effectful`


### `doeff_agents.handlers.headless`

- `packages/doeff-agents/tests/test_headless_adapter.hy`
- `packages/doeff-agents/tests/test_thinking_started_event.hy`

### `doeff_agents.handlers.headless_codex`

- `packages/doeff-agents/tests/test_headless_codex_account_limit.hy`
- agora-controllers: `docs/design-checks/lt-H32P81572MAR6ANS1EAQJRXAAQ/evidence/blind/A-probe_timeless_codex_limit.hy`

### `doeff_agents.handlers.headless_compose`

- `packages/doeff-agents/README.md`
- `packages/doeff-agents/tests/test_headless_adapter.hy`
- `packages/doeff-agents/tests/test_headless_codex_account_limit.hy`
- `packages/doeff-agents/tests/test_headless_codex_adapter.hy`
- agora-controllers: `controllers/agent_task/tests/test_cli_host.hy`
- agora-controllers: `controllers/agora_sim/tests/host_dispatch_world.hy`
- agora-controllers: `controllers/agora_sim/tests/test_any_attachment_reaches_the_workdir.hy`
- agora-controllers: `controllers/agora_sim/tests/test_cli_credential_swap.hy`
- agora-controllers: `controllers/agora_sim/tests/test_continued_turn_closed_watch.hy`
- agora-controllers: `controllers/agora_sim/tests/test_emulated_agent_task_translation.hy`
- agora-controllers: `controllers/agora_sim/tests/test_emulated_custody_lease.hy`
- agora-controllers: `controllers/agora_sim/tests/test_emulated_local_agora.hy`
- agora-controllers: `controllers/agora_sim/tests/test_emulated_local_agora_screen.hy`
- agora-controllers: `controllers/agora_sim/tests/test_exec_ticket_ends_turn.hy`
- agora-controllers: `controllers/agora_sim/tests/test_local_agora_latency.hy`
- agora-controllers: `controllers/agora_sim/tests/test_local_agora_serve_fake.hy`
- agora-controllers: `controllers/agora_sim/tests/test_model_source_and_set_at.hy`
- agora-controllers: `controllers/agora_sim/tests/test_pasted_image_reaches_the_turn.hy`
- agora-controllers: `controllers/agora_sim/tests/test_revoked_continuation_ends.hy`
- agora-controllers: `controllers/agora_sim/tests/test_runtime_env_declaration.hy`
- agora-controllers: `controllers/agora_sim/tests/test_screen_body_on_record.hy`
- agora-controllers: `controllers/agora_sim/tests/test_screen_card_accept_replies.hy`
- agora-controllers: `controllers/agora_sim/tests/test_screen_convert_opens_the_carrier.hy`
- agora-controllers: `controllers/agora_sim/tests/test_screen_force_right_after_send.hy`
- agora-controllers: `controllers/agora_sim/tests/test_screen_force_unread_input.hy`
- agora-controllers: `controllers/agora_sim/tests/test_screen_interrupt_send_stops_the_tool.hy`
- agora-controllers: `controllers/agora_sim/tests/test_screen_job_comes_up_with_the_custody_url.hy`
- agora-controllers: `controllers/agora_sim/tests/test_screen_model_change_then_send.hy`
- agora-controllers: `controllers/agora_sim/tests/test_screen_tell_then_send_order.hy`
- agora-controllers: `controllers/agora_sim/tests/test_screen_turn_stop.hy`
- agora-controllers: `controllers/agora_sim/tests/test_screen_turn_waits_from_messaging.hy`
- agora-controllers: `controllers/agora_sim/tests/test_sigint_unread_input_returns_to_next_turn.hy`
- agora-controllers: `controllers/agora_sim/tests/test_task_attempt_events.hy`
- agora-controllers: `controllers/agora_sim/tests/test_tool_contents_on_task.hy`
- agora-controllers: `controllers/agora_sim/tests/test_turn_body_before_head.hy`
- agora-controllers: `controllers/agora_sim/tests/test_turn_cost_across_segments.hy`
- agora-controllers: `controllers/agora_sim/tests/test_work_wait_without_party_continues.hy`
- agora-controllers: `controllers/runtime_env/core/declaration.hy`

### `doeff_agents.handlers.mcp_server_loop`

- `packages/doeff-agents/tests/test_mcp_server_loop.py`
- `packages/doeff-agents/tests/test_mcp_server_loop_scheduler_capture_regression.py`
- `tests/test_mcp_json_type_table.py`

### `doeff_agents.handlers.production`

- `packages/doeff-agents/architecture.hy`
- `packages/doeff-agents/tests/test_agent_mcp_same_vm.py`
- `packages/doeff-agents/tests/test_inprocess_report_result.py`
- `packages/doeff-agents/tests/test_l2_session_algebra.py`
- `packages/doeff-agents/tests/test_mcp_live_e2e.py`
- `packages/doeff-agents/tests/test_ready_gate.py`
- `packages/doeff-agents/tests/test_session_backend.py`
- `packages/doeff-agents/tests/test_session_registration_before_ready_gate.py`

### `doeff_agents.handlers.session_store`

- `packages/doeff-agents/tests/test_session_store_handlers.hy`

### `doeff_agents.handlers.testing`

- `packages/doeff-agents/tests/test_headless_adapter.hy`
- `packages/doeff-agents/tests/test_l2_session_algebra.py`
- `packages/doeff-agents/tests/test_mcp_e2e.py`
- `packages/doeff-conductor/tests/test_agent_journal_c3.py`
- `packages/doeff-conductor/tests/test_k4_deadline_owning_axis.py`
- `publications/zenn-use-cases-v0/doeff-agents.md`
- `publications/zenn-use-cases-v0/examples/agents_workflow.py`

