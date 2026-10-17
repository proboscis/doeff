# doeff_hy.static_stub が作った型の宣言 — 手で直さない(元 = handler.hy・作り直し = python -m doeff_hy.static_stub --write <この .pyi の隣の .hy>)

from _typeshed import Incomplete
from doeff import Program as _Program
from doeff_hy.static_types import Handler as _Handler
from collections.abc import Callable as Callable
from doeff import run as run
from doeff_core_effects.scheduler import CreateExternalPromise as CreateExternalPromise
from doeff_core_effects.scheduler import ExternalPromise as ExternalPromise
from doeff_time import GetMonotonic as GetMonotonic
from doeff_time import WaitWithin as WaitWithin
from doeff_codex.values import CodexSessionSpec as CodexSessionSpec
from doeff_codex.values import CodexTurn as CodexTurn
from doeff_codex.values import CodexEvent as CodexEvent
from doeff_codex.values import CodexInput as CodexInput
from doeff_codex.values import FreshThread as FreshThread
from doeff_codex.values import ResumeThread as ResumeThread
import doeff_codex.lines as lines
from doeff_codex.limits import merged_limits as merged_limits
from doeff_codex.limits import usage_limit_hit_of as usage_limit_hit_of
from dataclasses import replace as replace
from doeff_codex.rpc import app_server_argv as app_server_argv
from doeff_codex.rpc import initialize_request as initialize_request
from doeff_codex.rpc import initialized_notification as initialized_notification
from doeff_codex.rpc import thread_start_request as thread_start_request
from doeff_codex.rpc import thread_resume_request as thread_resume_request
from doeff_codex.rpc import turn_start_request as turn_start_request
from doeff_codex.rpc import turn_steer_request as turn_steer_request
from doeff_codex.rpc import turn_interrupt_request as turn_interrupt_request
from doeff_codex.rpc import server_response_line as server_response_line
from doeff_codex.process import CodexProcess as CodexProcess
from doeff_codex.process import EOF_GRACE_SECONDS as EOF_GRACE_SECONDS
from doeff_codex.process import TERM_GRACE_SECONDS as TERM_GRACE_SECONDS
from doeff_codex.credential_home import open_credential_home as open_credential_home
from doeff_codex.credential_home import close_credential_home as close_credential_home
from doeff_codex.effects import CodexStartTurn as CodexStartTurn
from doeff_codex.effects import CodexSteerTurn as CodexSteerTurn
from doeff_codex.effects import CodexInterruptTurn as CodexInterruptTurn
from doeff_codex.effects import CodexReadTurnEvents as CodexReadTurnEvents
from doeff_codex.effects import CodexAnswerRequest as CodexAnswerRequest
from doeff_codex.effects import CodexCloseSession as CodexCloseSession
from doeff_codex.effects import CodexLaunchCount as CodexLaunchCount
from doeff_codex.effects import BackendLost as BackendLost
from doeff_codex.effects import ToolsUnavailable as ToolsUnavailable
from doeff_codex.effects import TurnStarted as TurnStarted
from doeff_codex.effects import Steered as Steered
from doeff_codex.effects import InterruptRequested as InterruptRequested
from doeff_codex.effects import TurnEventPage as TurnEventPage
from doeff_codex.effects import Answered as Answered
from doeff_codex.effects import SessionClosed as SessionClosed
from doeff_codex.effects import ThreadUnknown as ThreadUnknown
from doeff_codex.effects import TurnInFlight as TurnInFlight
from doeff_codex.effects import LaunchFailed as LaunchFailed
from doeff_codex.effects import RequestRefused as RequestRefused
from doeff_codex.effects import NoTurnInFlight as NoTurnInFlight
from doeff_codex.effects import UnknownTurn as UnknownTurn
from doeff_codex.effects import NoSuchRequest as NoSuchRequest
from doeff_codex.effects import ProcessStillAlive as ProcessStillAlive
RETIRE_WAIT_SECONDS: Incomplete
KEPT_TURNS: int
CLIENT_NAME: str
CLIENT_VERSION: str

class Doorbell:
    lock: Incomplete
    bells: tuple[ExternalPromise[bool], ...]

    def __init__(self) -> None:
        ...

    def hang(self, bell: Incomplete) -> Incomplete:
        ...

    def unhang(self, bell: Incomplete) -> Incomplete:
        ...

    def ring(self) -> Incomplete:
        ...

class TurnLog:
    events: tuple[CodexEvent, ...]
    end: Incomplete
    pending: tuple
    limits: tuple

    def __init__(self) -> None:
        ...

class SessionRuntime:
    spec: CodexSessionSpec
    thread_id: Incomplete
    process: Incomplete
    generation: Incomplete
    launches: Incomplete
    lock: Incomplete
    doorbell: Incomplete
    next_request_id: Incomplete
    next_seq: Incomplete
    current_turn: Incomplete
    closed: Incomplete
    exit_code: Incomplete
    stderr_tail: Incomplete
    credential_home: Incomplete
    tools_lost: Incomplete
    answers: dict[int | str, object]
    turns: dict[str, TurnLog]
    outside: tuple
    late: tuple

    def __init__(self, spec: CodexSessionSpec) -> None:
        ...

    def take_request_id(self) -> int:
        ...

    def log_of(self, turn_id: str) -> TurnLog:
        ...

    def mark_running(self, turn_id: str) -> Incomplete:
        ...

    def running_log(self) -> Incomplete:
        ...

    def add_event(self, log: TurnLog, record: Incomplete) -> Incomplete:
        ...

    def end_toolless_turn(self) -> Incomplete:
        ...

class CodexHost:
    command: tuple
    launch_timeout: Incomplete
    lock: Incomplete
    runtimes: dict[str, SessionRuntime]

    def __init__(self, command: tuple, launch_timeout: Incomplete=120.0) -> None:
        ...

    def runtime(self, thread_id: str) -> Incomplete:
        ...

    def register(self, runtime: SessionRuntime) -> Incomplete:
        ...

def another_thread(runtime: SessionRuntime, record: lines.CodexLine) -> _Program[bool, object]:
    ...

def absorb(runtime: SessionRuntime, record: lines.CodexLine) -> _Program[None, object]:
    ...

def on_line(runtime: SessionRuntime, generation: int, raw: str) -> None:
    ...

def on_exit(runtime: SessionRuntime, generation: int, home: str | None, exit_code: int | None, stderr_tail: str) -> None:
    ...

def wait_until(doorbell: Doorbell, ready: Callable, seconds: float) -> _Program[bool, object]:
    ...
EXIT_RECORD_SECONDS: float

def exit_recorded(runtime: SessionRuntime) -> _Program[bool, object]:
    ...

def launch_failure(runtime: SessionRuntime, stage: str) -> _Program[LaunchFailed, object]:
    ...

def answered(runtime: SessionRuntime, request_id: int | str, process: CodexProcess) -> _Program[bool, object]:
    ...

def process_down(process: CodexProcess) -> _Program[bool, object]:
    ...

def request(runtime: SessionRuntime, method: str, build: Callable, seconds: float) -> _Program[lines.Response | lines.ErrorResponse | LaunchFailed, object]:
    ...

def refused(method: str, answer: lines.ErrorResponse) -> _Program[RequestRefused, object]:
    ...

def retire(runtime: SessionRuntime) -> _Program[bool, object]:
    ...

def launch(host: CodexHost, runtime: SessionRuntime, spec: CodexSessionSpec, origin: FreshThread | ResumeThread) -> _Program[str | ThreadUnknown | LaunchFailed | RequestRefused, object]:
    ...

def image_urls_of(input: CodexInput) -> _Program[tuple, object]:
    ...

def start_turn(host: CodexHost, asked: CodexStartTurn) -> _Program[TurnStarted | ThreadUnknown | TurnInFlight | LaunchFailed | RequestRefused, object]:
    ...

def interrupt_turn(host: CodexHost, turn: CodexTurn) -> _Program[InterruptRequested | NoTurnInFlight, object]:
    ...

def steer_turn(host: CodexHost, asked: CodexSteerTurn) -> _Program[Steered | NoTurnInFlight | RequestRefused | LaunchFailed, object]:
    ...

def answer_request(host: CodexHost, asked: CodexAnswerRequest) -> _Program[Answered | NoSuchRequest, object]:
    ...

def page_of(runtime: SessionRuntime, turn: CodexTurn, after_seq: int) -> _Program[TurnEventPage | None, object]:
    ...

def page_ready(runtime: SessionRuntime, turn: CodexTurn, after_seq: int) -> _Program[bool, object]:
    ...

def read_events(host: CodexHost, asked: CodexReadTurnEvents) -> _Program[TurnEventPage | UnknownTurn, object]:
    ...

def close_session(host: CodexHost, asked: CodexCloseSession) -> _Program[SessionClosed | ProcessStillAlive, object]:
    ...

def launch_count(host: CodexHost, thread_id: str) -> _Program[int, object]:
    ...

def codex_handler(host: Incomplete) -> _Handler:
    ...
