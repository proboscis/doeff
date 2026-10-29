# doeff_hy._static_stub が作った型の宣言 — 手で直さない(元 = _argv.hy・作り直し = python -m doeff_hy._static_stub --write <この .pyi の隣の .hy>)

from _typeshed import Incomplete
from doeff import Program as _Program
from collections.abc import Mapping as Mapping
from doeff_hy._frozen import FrozenMap as FrozenMap
from doeff_hy._frozen import thaw_json as thaw_json
from doeff_claude_code._values import ClaudeSessionSpec as ClaudeSessionSpec
from doeff_claude_code._values import ClaudeHome as ClaudeHome
from doeff_claude_code._values import LentCredential as LentCredential
from doeff_claude_code._values import BypassAll as BypassAll
from doeff_claude_code._values import AskHost as AskHost
from doeff_claude_code._values import DenyUnlisted as DenyUnlisted
from doeff_claude_code._values import HomeSettings as HomeSettings
from doeff_claude_code._values import McpSse as McpSse
from doeff_claude_code._values import McpStdio as McpStdio
from doeff_claude_code._values import PrintContent as PrintContent
from doeff_claude_code._values import AutocompactAuto as AutocompactAuto
from doeff_claude_code._values import AutocompactTokens as AutocompactTokens
from doeff_claude_code._values import FreshSession as FreshSession
from doeff_claude_code._values import ResumeSession as ResumeSession
from doeff_claude_code._values import ForkSession as ForkSession
from doeff_claude_code._effects import ClaudePrintOnce as ClaudePrintOnce
STREAM_FLAGS: list[str]
PER_TURN_SETTINGS_ENV: FrozenMap
OUTSIDE_TURN_TOOLS: tuple[str, ...]
DISABLE_ALL_HOOKS: str

def mangle_cwd(canonical_cwd: str) -> str:
    ...

def transcript_dir(config_dir: str, canonical_cwd: str) -> str:
    ...

def transcript_path(config_dir: str, canonical_cwd: str, session_id: str) -> str:
    ...

def subagent_transcript_path(config_dir: str, canonical_cwd: str, session_id: str, agent_id: str) -> _Program[str, object]:
    ...
STARTUP_TIMING_ENV: FrozenMap

def process_env(home: ClaudeHome, config_dir: str) -> dict[str, str]:
    ...

def credential_key_of(credential: LentCredential | None) -> _Program[dict | None, object]:
    ...

def permission_flags(policy: Incomplete) -> list:
    ...

def merged_settings(declared: FrozenMap, env: FrozenMap) -> FrozenMap:
    ...

def settings_flags(settings: FrozenMap) -> list:
    ...

def autocompact_flags(window: Incomplete) -> list:
    ...

def mcp_entry(server: Incomplete) -> dict:
    ...

def mcp_flags(servers: FrozenMap) -> list:
    ...

def base_flags(spec: ClaudeSessionSpec, settings: FrozenMap) -> list:
    ...

def origin_flags(origin: Incomplete) -> list:
    ...

def launch_flags(command: tuple[str, ...], spec: ClaudeSessionSpec) -> list[str]:
    ...

def launch_argv(command: tuple, spec: ClaudeSessionSpec, origin: Incomplete) -> list:
    ...

def launch_key(command: tuple[str, ...], spec: ClaudeSessionSpec) -> _Program[str, object]:
    ...
PRINT_ONCE_FLAGS: list[str]
PRINT_ONCE_ISOLATION_FLAGS: list[str]
PRINT_ONCE_CONTENT_FLAGS: list[str]

def print_once_argv(command: tuple[str, ...], request: ClaudePrintOnce) -> list[str]:
    ...

def print_once_env(request: ClaudePrintOnce, config_dir: str) -> dict[str, str]:
    ...

def cold_resume_argv(command: tuple, spec: ClaudeSessionSpec, session_id: str) -> list:
    ...
