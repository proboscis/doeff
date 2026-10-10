# doeff_hy.static_stub が作った型の宣言 — 手で直さない(元 = headless_compose.hy・作り直し = python -m doeff_hy.static_stub --write <この .pyi の隣の .hy>)

from _typeshed import Incomplete
from doeff import Program as _Program
from collections.abc import Callable as Callable
from collections.abc import Mapping as Mapping
from doeff_hy.frozen import FrozenMap as FrozenMap
from doeff_time import sync_time_handler as sync_time_handler
from doeff_claude_code.values import ClaudeHome as ClaudeHome
from doeff_claude_code.values import BypassAll as BypassAll
from doeff_claude_code.values import PermissionPolicy as PermissionPolicy
from doeff_claude_code.clock import clock_of as clock_of
from doeff_claude_code.handler import ClaudeCodeHost as ClaudeCodeHost
from doeff_claude_code.handler import claude_code_handler as claude_code_handler
from doeff_claude_code.fake import FakeClaudeWorld as FakeClaudeWorld
from doeff_claude_code.fake import FakeReply as FakeReply
from doeff_claude_code.fake import StopHookRejection as StopHookRejection
from doeff_claude_code.fake import fake_claude_code_handler as fake_claude_code_handler
from doeff_claude_code.lines import Usage as Usage
from doeff_claude_code.lines import AccountRefusalHit as AccountRefusalHit
from doeff_claude_code.lines import CompactBoundary as CompactBoundary
from doeff_claude_code.lines import CompactTrigger as CompactTrigger
from doeff_claude_code.lines import HookNotice as HookNotice
from doeff_claude_code.lines import HookPhase as HookPhase
from doeff_claude_code.effects import ClaudeLiveLimitExceeded as ClaudeLiveLimitExceeded
from doeff_agents.handlers.headless import HeadlessClaudeConfig as HeadlessClaudeConfig
from doeff_agents.handlers.headless import HeadlessState as HeadlessState
from doeff_agents.handlers.headless import headless_claude_handler as headless_claude_handler
from doeff_codex.values import CodexHome as CodexHome
from doeff_codex.values import CodexInput as CodexTurnInput
from doeff_codex.rpc import ApprovalPolicy as CodexApprovalPolicy
from doeff_codex.rpc import SandboxMode as CodexSandboxMode
from doeff_codex.handler import CodexHost as CodexHost
from doeff_codex.handler import codex_handler as codex_handler
from doeff_codex.fake import FakeCodexWorld as FakeCodexWorld
from doeff_codex.fake import FakeReply as FakeCodexReply
from doeff_codex.fake import fake_codex_handler as fake_codex_handler
from doeff_agents.handlers.headless_codex import HeadlessCodexConfig as HeadlessCodexConfig
from doeff_agents.handlers.headless_codex import HeadlessCodexState as HeadlessCodexState
from doeff_agents.handlers.headless_codex import headless_codex_handler as headless_codex_handler

def headless_claude_handlers(config_dir: str, env: FrozenMap[str], settings: Incomplete=None, cold_resume_prompt: Incomplete=None, command: Incomplete=..., *, live_limit: int | None, credential_floor_seconds: float) -> list:
    ...

def fake_headless_claude_handlers(responder: Incomplete, config_dir: Incomplete='fake-claude-home', world: Incomplete=None, *, env: Mapping[str, str], settings: Mapping[str, object], permission: PermissionPolicy=...) -> list:
    ...

def claude_process_layer(command: tuple, live_limit: int | None, credential_floor_seconds: float) -> _Program[Callable, object]:
    ...

def fake_claude_process_layer(responder: Callable | None, world: FakeClaudeWorld | None) -> _Program[Callable, object]:
    ...

def claude_adapter(config_dir: str, env: Mapping, settings: Mapping, cold_resume_prompt: str | None, permission: PermissionPolicy) -> _Program[Callable, object]:
    ...

def codex_process_layer(command: tuple, launch_timeout: float) -> _Program[Callable, object]:
    ...

def fake_codex_process_layer(world: FakeCodexWorld) -> _Program[Callable, object]:
    ...

def codex_adapter(env: Mapping, approval_policy: CodexApprovalPolicy, sandbox: CodexSandboxMode) -> _Program[Callable, object]:
    ...
