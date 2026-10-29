# doeff_hy._static_stub が作った型の宣言 — 手で直さない(元 = _fake.hy・作り直し = python -m doeff_hy._static_stub --write <この .pyi の隣の .hy>)

from _typeshed import Incomplete
from doeff import Program as _Program
from doeff_hy._static_types import Handler as _Handler
from collections.abc import Callable as Callable
from dataclasses import dataclass as dataclass
from doeff_time import Delay as Delay
from doeff_codex._values import CodexSessionSpec as CodexSessionSpec
from doeff_codex._values import CodexTurn as CodexTurn
from doeff_codex._values import CodexEvent as CodexEvent
from doeff_codex._values import CodexInput as CodexInput
from doeff_codex._values import FreshThread as FreshThread
from doeff_codex._values import ResumeThread as ResumeThread
from doeff_codex._lines import TurnStarted as TurnStartedLine
from doeff_codex._lines import TextDelta as TextDelta
from doeff_codex._lines import AgentMessageDone as AgentMessageDone
from doeff_codex._lines import ItemStarted as ItemStarted
from doeff_codex._lines import TurnError as TurnError
from doeff_codex._lines import TurnEnded as TurnEnded
from doeff_codex._lines import TurnStatus as TurnStatus
from doeff_codex._lines import UsageLimitHit as UsageLimitHit
from doeff_codex._lines import USAGE_LIMIT_KIND as USAGE_LIMIT_KIND
from doeff_codex._lines import ServerOverloadedHit as ServerOverloadedHit
from doeff_codex._lines import SERVER_OVERLOADED_KIND as SERVER_OVERLOADED_KIND
from doeff_codex._effects import CodexStartTurn as CodexStartTurn
from doeff_codex._effects import CodexSteerTurn as CodexSteerTurn
from doeff_codex._effects import CodexInterruptTurn as CodexInterruptTurn
from doeff_codex._effects import CodexReadTurnEvents as CodexReadTurnEvents
from doeff_codex._effects import CodexAnswerRequest as CodexAnswerRequest
from doeff_codex._effects import CodexCloseSession as CodexCloseSession
from doeff_codex._effects import CodexLaunchCount as CodexLaunchCount
from doeff_codex._effects import BackendLost as BackendLost
from doeff_codex._effects import ToolsUnavailable as ToolsUnavailable
from doeff_codex._effects import TurnStarted as TurnStarted
from doeff_codex._effects import Steered as Steered
from doeff_codex._effects import InterruptRequested as InterruptRequested
from doeff_codex._effects import TurnEventPage as TurnEventPage
from doeff_codex._effects import SessionClosed as SessionClosed
from doeff_codex._effects import ThreadUnknown as ThreadUnknown
from doeff_codex._effects import TurnInFlight as TurnInFlight
from doeff_codex._effects import NoTurnInFlight as NoTurnInFlight
from doeff_codex._effects import UnknownTurn as UnknownTurn
from doeff_codex._effects import NoSuchRequest as NoSuchRequest
STEERED_PREFIX: str

@dataclass(frozen=True, kw_only=True)
class FakeReply:
    pieces: tuple[str, ...]
    hold: bool = False
    usage_limit: UsageLimitHit | None = None
    server_overloaded: ServerOverloadedHit | None = None
    tools_lost: str | None = None

class FakeTurn:
    events: tuple[CodexEvent, ...]
    end: Incomplete

    def __init__(self) -> None:
        ...

class FakeThread:
    spec: CodexSessionSpec
    launches: Incomplete
    alive: Incomplete
    current: Incomplete
    next_seq: Incomplete
    turns: dict[str, FakeTurn]

    def __init__(self, spec: CodexSessionSpec) -> None:
        ...

    def add(self, turn: FakeTurn, record: Incomplete) -> Incomplete:
        ...

    def running(self) -> Incomplete:
        ...

class FakeCodexWorld:
    respond: Callable[[CodexInput], FakeReply]
    made: Incomplete
    threads: dict[str, FakeThread]

    def __init__(self, respond: Callable[[CodexInput], FakeReply]) -> None:
        ...

    def fresh_id(self, kind: str) -> str:
        ...

def fake_start(world: FakeCodexWorld, asked: CodexStartTurn) -> _Program[TurnStarted | ThreadUnknown | TurnInFlight, object]:
    ...

def fake_read(world: FakeCodexWorld, asked: CodexReadTurnEvents) -> _Program[TurnEventPage | UnknownTurn, object]:
    ...

def fake_steer(world: FakeCodexWorld, asked: CodexSteerTurn) -> _Program[Steered | NoTurnInFlight, object]:
    ...

def fake_interrupt(world: FakeCodexWorld, turn: CodexTurn) -> _Program[InterruptRequested | NoTurnInFlight, object]:
    ...

def fake_close(world: FakeCodexWorld, asked: CodexCloseSession) -> _Program[SessionClosed, object]:
    ...

def fake_launch_count(world: FakeCodexWorld, thread_id: str) -> _Program[int, object]:
    ...

def fake_codex_handler(world: Incomplete) -> _Handler:
    ...
