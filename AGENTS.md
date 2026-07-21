# Repository Guidelines

## Communication
Respond to the user in Japanese by default unless they explicitly request another language.

## Ownership & Write Authority (CRITICAL)

doeff is a personal open-source project owned and maintained by @proboscis. It is
consumed as a dependency by other projects (including corporate research projects),
but it is NOT owned by any employer. To keep authorship, provenance, and IP
attribution clean, write access is governed by **account routing**:

- **Reading is unrestricted.** Any agent may freely read, search, and analyze this
  codebase.
- **Write authority = correct account, not a workflow choke point.** `git commit`,
  `git push`, branch creation, and pull requests may be performed by any agent
  session running under the maintainer's designated personal (non-employer)
  profile. This is enforced system-wide by the profile / git-author boundary
  hooks (company-write-boundary); **orch runs are NOT required for landing
  changes**. (2026-07-21 maintainer directive: the former "all changes land via
  orch" rule was a proxy for account routing, which the surrounding system now
  enforces directly — the choke point is removed.)
- **Agents running under an employer-provided AI subscription must never author
  commits or PRs in this repository**, even when technically able to. For such
  sessions the sole contribution channel is report-only: file a GitHub issue
  with the symptom, expected behavior, and a minimal repro.
- **Downstream-task agents: report, don't drive-by fix.** If your primary task
  is a downstream project that depends on doeff and you find a bug or missing
  feature here, file an issue instead of fixing it in passing — the maintainer
  routes it.
- orch remains available as an execution engine for multi-agent runs; using it
  is a workflow choice, not a write-authority requirement.

## Project Structure & Module Organization
Core runtime code lives in `doeff/`: monadic primitives in `program.py`, the `@do` decorator in `do.py`, execution via `run()` in `run.py`, result types in `result.py`, and handler utilities in `handler_utils.py`. CLI commands are in `cli/` with auto-discovery (`discovery.py`) and execution (`run_services.py`). Effect definitions and handlers live in `packages/doeff-core-effects/` (effects in `effects.py`, handlers in `handlers.py`, scheduler in `scheduler.py`). Tests that exercise each capability reside in `tests/`, while runnable samples land in `examples/`. Workspace extensions such as OpenAI, Gemini, agents, and pinjected bridges are published from `packages/`; keep connector-specific assets inside their respective subpackages. Use `docs/` for design notes or long-form guides that support future contributors.

## Build, Test, and Development Commands
Install everything (including dev tools) with `make sync`. Use `uv run pytest` for the full suite, `uv run pytest tests/test_core_effects.py::test_reader_ask` to target a single scenario, and `uv run pyright` for static typing. Build distributable artifacts via `uv run python -m build` before publishing packages.

**WARNING — Stale Rust VM builds:** `uv sync --group dev` does NOT reliably rebuild the Rust VM extension (`packages/doeff-vm`). If you edit any `.rs` file under `packages/doeff-vm/src/`, you MUST run `make sync` (or `cd packages/doeff-vm && maturin develop --release`) to rebuild. Failing to do so will run tests against a stale binary, producing phantom failures that look like real regressions but disappear after a clean rebuild. Always use `make sync` instead of bare `uv sync`.

## Agent Authentication Boundary
`doeff-agents` owns Claude/Codex agent launch, tmux/zellij-style terminal transport,
schema-result retries, and agent authentication boundaries. Callers may provide
workspace, prompt, model, MCP tools, result schema, and non-secret runtime hints, but
must not smuggle LLM provider API keys into agent processes.

Callers must not read files created by an agent to obtain the agent result. The
only public result boundary for a doeff-agents user is `AwaitResult` returning
`AwaitOutcome.result` validated against `AgentSpec.result_schema`.
`doeff-agents` must not ask agents to create JSON files for input, result,
evidence, or checkpoints; especially do not use `.agentd-result.json`,
`result.json`, or `helper-result.json` as a transport. If an agent result is
absent or invalid, doeff-agents owns the schema validation and retry loop before
returning the final `AwaitOutcome`. If callers need diagnostic or evidence data,
it must be part of `AgentSpec.result_schema` and returned in
`AwaitOutcome.result`, not carried through a side-channel file.

Application packages must not import `doeff_agents.tmux`, instantiate
`TmuxSessionBackend` / `TmuxAgentHandler`, or shell out to `tmux` directly. They
should emit `LaunchEffect` / `LaunchSession` effects and install
`agent_effectful_handler()`. When they must provide a local backend, use
`doeff_agents.session_backend.default_session_backend()`. When an advanced caller
must build a custom effect boundary with an explicit MCP `run_tool` stack, use
`doeff_agents.handlers.default_agent_handler()` rather than importing transport
classes. The terminal multiplexer implementation is an internal doeff-agents
detail so it can move to tmux, zellij, opencode, or another transport without
changing trading or application code.

Never pass Anthropic API keys to agents through `session_env`,
`ClaudeRuntimePolicy.bootstrap_exports`, wrappers, shell exports, or equivalent
transport hooks. In particular, `ANTHROPIC_API_KEY`,
`anthropic_api_key__personal`, and `anthropic-api-key-personal` are forbidden at
the agent boundary. Claude agents must use Claude Code's interactive/OAuth
credential state; Codex agents must use Codex's own credential state. API-key-backed
LLM calls are allowed only through memoized `LLMStructuredQuery` /
`StructuredLLMQuery` handlers for a single structured call, never through agents.

## Linting & Architectural Enforcement
Run `make lint` to execute all linters (ruff, pyright, semgrep, doeff-linter). Individual targets: `make lint-ruff`, `make lint-pyright`, `make lint-semgrep`, `make lint-doeff`. Format code with `make format`. Install semgrep via `uv tool install semgrep`. Build doeff-linter with `cd packages/doeff-linter && cargo install --path .`. The `.semgrep.yaml` rules enforce architectural patterns (layer boundaries, effect system conventions); the Rust-based doeff-linter enforces immutability and code quality patterns. Install pre-commit hooks with `make pre-commit-install`.

## Coding Style & Naming Conventions
Write Python 3.10+ with four-space indentation, rich type hints, and generator-based `@do` functions when composing effects. Follow the 100-character soft limit enforced by Ruff, and rely on Ruff for import ordering. Modules, functions, and variables use `snake_case`; classes and effect types use `PascalCase`. Keep public typing metadata updated in `.pyi` files and ensure `py.typed` remains present for exported packages.

## Testing Guidelines
Pytest with strict asyncio mode powers the suite, so mark coroutines with `@pytest.mark.asyncio` or use async fixtures. Name new tests `test_<feature>.py` and structure coroutine assertions with `await` rather than event loops. Add regression coverage near related tests (for example, extend `tests/test_program_monadic_methods.py` when touching `Program`). If you introduce long-running integrations, guard them with `pytest.mark.e2e` per the configured marker list.

## Task Management
When a request is provided, always use the Task tool (TaskCreate) to break the request into concrete todo items before starting work. Mark each task as `in_progress` when you begin it and `completed` when done. This ensures progress is visible and nothing is missed.

## Orch Run Management (CRITICAL)
When using `orch` to manage agent runs, **NEVER stop a run that is in `wait` or `blocked` state**. These states mean the agent is alive and waiting for user input — use `orch send <RUN_REF> "message"` to communicate with it. Stopping a waiting run kills the session, loses all agent context, and forces a costly restart. Only use `orch stop` on runs that are clearly stale (hours of no progress with `running` state) or confirmed dead (`unknown`/`failed`). When in doubt, use `orch capture` to check the agent's output before deciding.

| Run State | Action |
|-----------|--------|
| `wait` / `blocked` | `orch send` — NEVER `orch stop` |
| `running` | Leave alone or `orch send` if guidance needed |
| `failed` / `unknown` | OK to `orch stop` + `orch continue` |
| `done` / `cancel` | Already terminated |

## Concurrent Sessions — Cooperate via herdr Messaging (CRITICAL)

Multiple agent sessions (including dependency-upgrade agents from downstream
projects) may work in this repository at the same time. The `~/repos/doeff`
checkout and its worktrees are shared infrastructure — do not fight over them;
communicate.

- **Detect**: if the working tree changes under you (HEAD/branch moved, files
  reverted, an unfamiliar stash like `parked by <session>`, reflog entries you
  didn't make), another session is likely active. Check
  `git reflog --date=iso` and `git stash list` before assuming data loss.
- **Cooperate by actually messaging the other session** via herdr: find its
  pane with `herdr pane list` (match `cwd`/`label`/`agent_status`), then
  `herdr pane send-text <pane_id> "<one-line message>"` followed by
  `herdr pane send-keys <pane_id> enter`. Identify yourself (your own pane id),
  state facts (commits/stashes/branches, concretely), make requests explicit
  (including do-NOTs), and name your pane for replies.
- **Park, don't claim**: prefer a dedicated `git worktree` for branch work;
  landing works from a worktree via `git push origin <branch>:main` without
  touching the shared main checkout.
- **If you must move another session's uncommitted changes aside**, label the
  stash with your session name (`git stash push -m "parked by <session> <date>"`)
  AND notify that session's pane.
- Full protocol and etiquette: shared skill `herdr-comms`
  (`~/dotfiles/agent/skills/herdr-comms/SKILL.md`).

## TDD + Semgrep Enforcement Strategy

Every issue that adds, removes, or changes an architectural invariant MUST follow this protocol:

### Phase 1: Write Failing Tests (TDD)
1. Write tests that assert the NEW expected behavior FIRST
2. Run them — they MUST fail (proves the test is meaningful)
3. Commit the failing tests to the branch (they belong to the issue)

### Phase 2: Write Semgrep Guard Rules
If the change introduces a ban (removed API, forbidden pattern, layer boundary):
1. Write a semgrep rule in `.semgrep.yaml` that bans the OLD pattern (severity: `ERROR`)
2. Run it — it SHOULD fire on existing code (confirms the rule works)
3. The rule stays permanently as a regression guard after migration

For NEW patterns worth protecting:
1. Identify the invariant: "what would break if someone did X?"
2. Write a semgrep rule banning X
3. Include the "why" in the `message:` field with spec/issue reference

### Phase 3: Implement
1. Make the code changes that satisfy both the tests and the semgrep rules
2. All tests pass, `make lint` clean (including the new semgrep rules)

### Why This Order Matters
- Tests prove the change works (regression guard at runtime)
- Semgrep rules prevent the old pattern from creeping back (regression guard at lint time)
- Writing them first forces precise thinking about what exactly is changing
- Agents that skip this produce unverifiable work

### Issue Template Pattern
Issues SHOULD include:
```
## Failing Tests (commit first)
- test_foo_new_behavior: asserts X
- test_bar_rejects_old_api: asserts Y is gone

## Semgrep Rules (commit with tests or with fix)
- rule-id: ban-old-pattern — bans Z in paths P

## Implementation
- change A in file B
- remove C from file D

## Acceptance Criteria
1. Failing tests now pass
2. Semgrep rules pass (no violations)
3. All existing tests still pass
4. `make lint` clean
```

## Verification Contract (CRITICAL)

The `## Verification` section of the driving issue is a BINDING contract, not a suggestion. Two same-day incidents (PR #472, #474 on 2026-06-12) shipped silently weakened verification — mock-runtime E2E where the issue demanded real-git kill→resume, display-only checks where the issue demanded an exit-code gate — while the PR claimed completion. Green tests cannot catch this: the weakened criteria are what made them green. Hence:

1. **1:1 mapping is mandatory.** The PR body MUST contain a table mapping every Verification bullet of the issue to a named shipped test (`path/to/test_file.py::test_name`). A bullet you cannot point a test at is NOT done.
2. **Substitution must be declared.** Replacing or weakening any Verification item (mock for real resource, unit for E2E, log line for exit code, smaller scope) is sometimes legitimate — but ONLY with an explicit `## Verification deviations` section in the PR body stating what was substituted and why.
3. **Undeclared deviation = automatic bounce.** Reviewers check the mapping table against the issue line by line; any silent weakening returns the PR without further review.

## PR Review Workflow (CRITICAL)
When reviewing a PR created by an `orch` agent run, **NEVER fix issues directly on the branch**. Always send feedback via `orch send <RUN_REF> "message"` and wait for the agent to apply the fix. This preserves the agent's ownership of its branch, avoids merge conflicts from concurrent edits, and ensures the agent understands the feedback for future work.

| Situation | Action |
|-----------|--------|
| Found smell/hack in agent PR | `orch send` with specific feedback |
| Agent's fix is wrong | `orch send` with correction |
| Trivial typo in agent PR | `orch send` — still don't fix directly |
| Agent run is dead/out of budget | Only then: fix directly or create new run |

## Coding Anti-Patterns (Banned)
The following patterns are banned in production code. Agents must avoid these; reviewers must flag them.

- **`getattr(obj, "attr", default)`**: Silent fallback that hides type errors. Use direct attribute access on properly typed objects. If the type is uncertain, narrow it first (`isinstance` check), don't paper over it with `getattr`.
- **`as any` / `@ts-ignore` / `@ts-expect-error`**: Type suppression.
- **Empty `except:` or `except Exception:` without re-raise**: Silent error swallowing.
- **`_ =>` catch-all match arms in Rust**: Use exhaustive matches. Every variant must be named.

## Commit & Pull Request Guidelines
Recent history favors concise, imperative summaries (for example, `Fix cache invalidation` or `Add Gemini structured support`). Reference related issues in the body, note behavioral risks, and list validation commands you ran. Pull requests should describe the effect on core `doeff/` APIs versus optional `packages/` integrations, attach screenshots or traces when diagnostics change, and mention follow-up work in a checklist so maintainers can track it.
