# doeff_hy.static_stub が作った型の宣言 — 手で直さない(元 = _rpc.hy・作り直し = python -m doeff_hy.static_stub --write <この .pyi の隣の .hy>)

from typing import TypeAlias
from doeff import Program as _Program
from dataclasses import dataclass as dataclass
from enum import StrEnum as StrEnum
from doeff_hy._json_value import OpaqueJson as OpaqueJson
from doeff_hy._wire import dump as dump
from doeff_hy._wire import dump_json as dump_json
JSONRPC_VERSION: str
APP_SERVER_ARGS: tuple[str, ...]

class ApprovalPolicy(StrEnum):
    UNTRUSTED = 'untrusted'
    ON_FAILURE = 'on-failure'
    ON_REQUEST = 'on-request'
    NEVER = 'never'

class SandboxMode(StrEnum):
    READ_ONLY = 'read-only'
    WORKSPACE_WRITE = 'workspace-write'
    DANGER_FULL_ACCESS = 'danger-full-access'

@dataclass(frozen=True, kw_only=True)
class RequestWire:
    jsonrpc: str
    id: int
    method: str
    params: OpaqueJson

@dataclass(frozen=True, kw_only=True)
class ResponseWire:
    jsonrpc: str
    id: int | str
    result: OpaqueJson

@dataclass(frozen=True, kw_only=True)
class NotificationWire:
    jsonrpc: str
    method: str

@dataclass(frozen=True, kw_only=True)
class ClientInfoWire:
    name: str
    version: str

@dataclass(frozen=True, kw_only=True)
class InitializeParamsWire:
    client_info: ClientInfoWire

@dataclass(frozen=True, kw_only=True)
class ConfigWire:
    model_auto_compact_token_limit: int

@dataclass(frozen=True, kw_only=True)
class ThreadStartParamsWire:
    cwd: str
    approval_policy: ApprovalPolicy | None = None
    sandbox: SandboxMode | None = None
    model: str | None = None
    config: ConfigWire | None = None

@dataclass(frozen=True, kw_only=True)
class ThreadResumeParamsWire:
    thread_id: str
    cwd: str | None = None
    approval_policy: ApprovalPolicy | None = None
    sandbox: SandboxMode | None = None
    model: str | None = None
    config: ConfigWire | None = None
    exclude_turns: bool | None = None

@dataclass(frozen=True, kw_only=True)
class TextInputWire:
    type: str
    text: str

@dataclass(frozen=True, kw_only=True)
class ImageInputWire:
    type: str
    url: str

@dataclass(frozen=True, kw_only=True)
class TurnStartParamsWire:
    thread_id: str
    input: tuple[TextInputWire | ImageInputWire, ...]
    effort: str | None = None

@dataclass(frozen=True, kw_only=True)
class TurnSteerParamsWire:
    thread_id: str
    expected_turn_id: str
    input: tuple[TextInputWire | ImageInputWire, ...]

@dataclass(frozen=True, kw_only=True)
class TurnInterruptParamsWire:
    thread_id: str
    turn_id: str

def app_server_argv(command: tuple, disabled_features: tuple) -> _Program[list, object]:
    ...
RequestParams: TypeAlias = InitializeParamsWire | ThreadStartParamsWire | ThreadResumeParamsWire | TurnStartParamsWire | TurnSteerParamsWire | TurnInterruptParamsWire

def request_line(request_id: int, method: str, params: RequestParams) -> _Program[str, object]:
    ...

def initialize_request(request_id: int, client_name: str, client_version: str) -> _Program[str, object]:
    ...

def initialized_notification() -> _Program[str, object]:
    ...

def config_of(auto_compact_token_limit: int | None) -> _Program[ConfigWire | None, object]:
    ...

def thread_start_request(request_id: int, cwd: str, approval_policy: ApprovalPolicy | None=None, sandbox: SandboxMode | None=None, model: str | None=None, auto_compact_token_limit: int | None=None) -> _Program[str, object]:
    ...

def thread_resume_request(request_id: int, thread_id: str, cwd: str | None=None, approval_policy: ApprovalPolicy | None=None, sandbox: SandboxMode | None=None, model: str | None=None, auto_compact_token_limit: int | None=None) -> _Program[str, object]:
    ...

def input_wires_of(text: str, image_urls: tuple) -> _Program[tuple, object]:
    ...

def turn_start_request(request_id: int, thread_id: str, text: str, image_urls: tuple, effort: str | None=None) -> _Program[str, object]:
    ...

def turn_steer_request(request_id: int, thread_id: str, turn_id: str, text: str, image_urls: tuple) -> _Program[str, object]:
    ...

def turn_interrupt_request(request_id: int, thread_id: str, turn_id: str) -> _Program[str, object]:
    ...

def server_response_line(request_id: int | str, result: OpaqueJson) -> _Program[str, object]:
    ...
