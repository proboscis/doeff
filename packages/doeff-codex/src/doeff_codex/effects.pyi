# doeff_hy.static_stub が作った型の宣言 — 手で直さない(元 = effects.hy・作り直し = python -m doeff_hy.static_stub --write <この .pyi の隣の .hy>)

from typing import TypeAlias
from doeff import EffectBase as _doeff_effect_base
from dataclasses import dataclass as _doeff_dataclass
from dataclasses import dataclass as dataclass
from doeff_hy.json_value import OpaqueJson as OpaqueJson
from doeff_codex.values import CodexSessionSpec as CodexSessionSpec
from doeff_codex.values import CodexTurn as CodexTurn
from doeff_codex.values import CodexEvent as CodexEvent
from doeff_codex.values import CodexInput as CodexInput
from doeff_codex.values import FreshThread as FreshThread
from doeff_codex.values import ResumeThread as ResumeThread
from doeff_codex.lines import TurnEnded as TurnEnded

@dataclass(frozen=True, kw_only=True)
class BackendLost:
    detail: str
    exit_code: int | None = None
    stderr_tail: str | None = None
CodexTurnEnd: TypeAlias = TurnEnded | BackendLost

@dataclass(frozen=True, kw_only=True)
class TurnStarted:
    turn: CodexTurn

@dataclass(frozen=True, kw_only=True)
class Steered:
    ...

@dataclass(frozen=True, kw_only=True)
class InterruptRequested:
    ...

@dataclass(frozen=True, kw_only=True)
class TurnEventPage:
    events: tuple[CodexEvent, ...]
    next_seq: int
    end: TurnEnded | BackendLost | None

@dataclass(frozen=True, kw_only=True)
class Answered:
    ...

@dataclass(frozen=True, kw_only=True)
class SessionClosed:
    was_running: bool

@dataclass(frozen=True, kw_only=True)
class ThreadUnknown:
    thread_id: str
    detail: str

@dataclass(frozen=True, kw_only=True)
class TurnInFlight:
    turn: CodexTurn

@dataclass(frozen=True, kw_only=True)
class LaunchFailed:
    detail: str
    exit_code: int | None = None
    stderr_tail: str = ''

@dataclass(frozen=True, kw_only=True)
class RequestRefused:
    method: str
    code: int
    message: str

@dataclass(frozen=True, kw_only=True)
class NoTurnInFlight:
    turn: CodexTurn

@dataclass(frozen=True, kw_only=True)
class UnknownTurn:
    turn: CodexTurn

@dataclass(frozen=True, kw_only=True)
class NoSuchRequest:
    request_id: int | str

@dataclass(frozen=True, kw_only=True)
class ProcessStillAlive:
    detail: str
StartTurnOutcome: TypeAlias = TurnStarted | ThreadUnknown | TurnInFlight | LaunchFailed | RequestRefused

@_doeff_dataclass(frozen=True)
class CodexStartTurn(_doeff_effect_base[StartTurnOutcome]):
    origin: FreshThread | ResumeThread
    spec: CodexSessionSpec
    input: CodexInput

    def __post_init__(self) -> None:
        ...

@_doeff_dataclass(frozen=True)
class CodexSteerTurn(_doeff_effect_base[Steered | NoTurnInFlight | RequestRefused | LaunchFailed]):
    turn: CodexTurn
    input: CodexInput

    def __post_init__(self) -> None:
        ...

@_doeff_dataclass(frozen=True)
class CodexInterruptTurn(_doeff_effect_base[InterruptRequested | NoTurnInFlight]):
    turn: CodexTurn

    def __post_init__(self) -> None:
        ...

@_doeff_dataclass(frozen=True)
class CodexReadTurnEvents(_doeff_effect_base[TurnEventPage | UnknownTurn]):
    turn: CodexTurn
    after_seq: int
    wait_up_to: float

    def __post_init__(self) -> None:
        ...

@_doeff_dataclass(frozen=True)
class CodexAnswerRequest(_doeff_effect_base[Answered | NoSuchRequest]):
    turn: CodexTurn
    request_id: int | str
    result: OpaqueJson

    def __post_init__(self) -> None:
        ...

@_doeff_dataclass(frozen=True)
class CodexCloseSession(_doeff_effect_base[SessionClosed | ProcessStillAlive]):
    thread_id: str
    reason: str

    def __post_init__(self) -> None:
        ...

@_doeff_dataclass(frozen=True)
class CodexLaunchCount(_doeff_effect_base[int]):
    thread_id: str

    def __post_init__(self) -> None:
        ...
