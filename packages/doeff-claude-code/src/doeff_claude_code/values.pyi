# doeff_hy.static_stub が作った型の宣言 — 手で直さない(元 = values.hy・作り直し = python -m doeff_hy.static_stub --write <この .pyi の隣の .hy>)

from typing import TypeAlias
from dataclasses import dataclass as dataclass
from dataclasses import field as field
from doeff_hy.frozen import FrozenMap as FrozenMap
from doeff_hy.frozen import frozen_json_object as frozen_json_object
from doeff_hy.frozen import frozen_map_of as frozen_map_of

def checked_env(value: object, what: str) -> FrozenMap[str]:
    ...

def checked_session_id(value: str, what: str) -> str:
    ...

@dataclass(frozen=True)
class ClaudeHome:
    config_dir: str
    env: FrozenMap[str] = ...

    def __post_init__(self) -> None:
        ...

@dataclass(frozen=True)
class BypassAll:
    ...
ASK_HOST_MODES: tuple[str, ...]

@dataclass(frozen=True)
class AskHost:
    mode: str = 'default'

    def __post_init__(self) -> None:
        ...

@dataclass(frozen=True)
class DenyUnlisted:
    allowed_tools: tuple[str, ...] = ...

    def __post_init__(self) -> None:
        ...
PermissionPolicy: TypeAlias = BypassAll | AskHost | DenyUnlisted

@dataclass(frozen=True)
class McpSse:
    url: str

@dataclass(frozen=True)
class McpStdio:
    command: str
    args: tuple[str, ...] = ...
    env: FrozenMap[str] = ...

    def __post_init__(self) -> None:
        ...
McpServer: TypeAlias = McpSse | McpStdio
AUTOCOMPACT_MIN_TOKENS: int
AUTOCOMPACT_MAX_TOKENS: int

@dataclass(frozen=True)
class AutocompactAuto:
    ...

@dataclass(frozen=True)
class AutocompactTokens:
    tokens: int

    def __post_init__(self) -> None:
        ...
AutocompactWindow: TypeAlias = AutocompactAuto | AutocompactTokens

@dataclass(frozen=True)
class ClaudeSessionSpec:
    home: ClaudeHome
    cwd: str
    model: str | None = None
    effort: str | None = None
    settings: FrozenMap = ...
    mcp_servers: FrozenMap[McpSse | McpStdio] = ...
    permission: BypassAll | AskHost | DenyUnlisted = ...
    autocompact: AutocompactAuto | AutocompactTokens | None = None
    system_prompt_append: str | None = None
    cold_resume_prompt: str | None = None
    credential_expires_at: float | None = None

    def __post_init__(self) -> None:
        ...

@dataclass(frozen=True)
class LinkFromHome:
    source_home: ClaudeHome

@dataclass(frozen=True)
class Rebuilt:
    jsonl_text: str

    def __post_init__(self) -> None:
        ...
TranscriptCarry: TypeAlias = LinkFromHome | Rebuilt

@dataclass(frozen=True)
class FreshSession:
    session_id: str

    def __post_init__(self) -> None:
        ...

@dataclass(frozen=True)
class ResumeSession:
    session_id: str
    carry: LinkFromHome | Rebuilt | None = None

    def __post_init__(self) -> None:
        ...

@dataclass(frozen=True)
class ForkSession:
    parent_session_id: str
    carry: LinkFromHome | Rebuilt | None = None

    def __post_init__(self) -> None:
        ...
SessionOrigin: TypeAlias = FreshSession | ResumeSession | ForkSession
IMAGE_MIMES: tuple[str, ...]

@dataclass(frozen=True)
class ImageAttachment:
    mime: str
    data_base64: str

@dataclass(frozen=True)
class TurnInput:
    text: str
    ref: str
    attachments: tuple[ImageAttachment, ...] = ...

    def __post_init__(self) -> None:
        ...

@dataclass(frozen=True)
class ClaudeTurn:
    session_id: str
    turn_seq: int

@dataclass(frozen=True)
class Allow:
    updated_input: FrozenMap | None = None

    def __post_init__(self) -> None:
        ...

@dataclass(frozen=True)
class Deny:
    message: str
PermissionAnswer: TypeAlias = Allow | Deny
