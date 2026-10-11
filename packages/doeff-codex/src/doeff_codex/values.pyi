# doeff_hy.static_stub が作った型の宣言 — 手で直さない(元 = values.hy・作り直し = python -m doeff_hy.static_stub --write <この .pyi の隣の .hy>)

from dataclasses import dataclass as dataclass
from dataclasses import field as field
from doeff_codex.rpc import ApprovalPolicy as ApprovalPolicy
from doeff_codex.rpc import SandboxMode as SandboxMode
from doeff_codex.lines import CodexLine as CodexLine

@dataclass(frozen=True, kw_only=True)
class CodexHome:
    env: dict[str, str] = ...
    auth_json: str | None = ...

    def __post_init__(self) -> None:
        ...

@dataclass(frozen=True, kw_only=True)
class CodexSessionSpec:
    home: CodexHome
    cwd: str
    model: str | None = None
    approval_policy: ApprovalPolicy | None = None
    sandbox: SandboxMode | None = None
    effort: str | None = None
    auto_compact_token_limit: int | None = None

    def __post_init__(self) -> None:
        ...

@dataclass(frozen=True, kw_only=True)
class CodexImage:
    mime: str
    data_base64: str

    def __post_init__(self) -> None:
        ...

@dataclass(frozen=True, kw_only=True)
class CodexInput:
    text: str
    images: tuple[CodexImage, ...] = ...

@dataclass(frozen=True, kw_only=True)
class FreshThread:
    ...

@dataclass(frozen=True, kw_only=True)
class ResumeThread:
    thread_id: str

@dataclass(frozen=True, kw_only=True)
class CodexTurn:
    thread_id: str
    turn_id: str

@dataclass(frozen=True, kw_only=True)
class CodexEvent:
    seq: int
    record: CodexLine
