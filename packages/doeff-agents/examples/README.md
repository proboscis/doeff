# doeff-agents Examples

These examples drive coding agents (Claude, Codex) with the public API of
`doeff-agents`: a program yields the effects (`Launch`, `AwaitResult`, `Events`,
`Send` / `FollowUp`, `Monitor`, `Capture`, `Stop`) and one entry point installs
the handlers that answer them. The same program runs on a fake or mock handler in
a test and on a real handler in production; only the installed handlers change.

| File | What it shows | Handlers it installs |
|------|---------------|----------------------|
| `01_review_with_effects.py` | Launch a session, wait for the turn's result, read it and stop the session; answer a failed turn from its events; run reviews in parallel with `Spawn` / `Gather` | `fake_claude_agent_runtime_handlers` (no process, no API) — `claude_agent_runtime_handlers` for the real CLI |
| `02_terminal_session_with_mock.py` | Watch a terminal (TUI) session, answer the agent's question with `Send`, read the screen with `Capture` | `mock_agent_handlers(sessions=…)` with a scripted `MockSessionScript` — `agent_effectful_handlers` for real tmux |

Run them from the repository root:

```bash
uv run python packages/doeff-agents/examples/01_review_with_effects.py
uv run python packages/doeff-agents/examples/02_terminal_session_with_mock.py
```

Each example checks its own answer with `assert` and prints it.
