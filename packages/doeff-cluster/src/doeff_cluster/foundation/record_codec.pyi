# doeff_hy.static_stub が作った型の宣言 — 手で直さない(元 = record_codec.hy・作り直し = python -m doeff_hy.static_stub --write <この .pyi の隣の .hy>)

from typing import TypeAlias
from _typeshed import Incomplete
from doeff import Program as _Program
from doeff_hy.macros import _install_guard_globals as _install_guard_globals
from doeff_hy.macros import _guard_performed as _guard_performed
import base64 as base64
from collections import OrderedDict as OrderedDict
from collections.abc import Callable as Callable
from typing import ClassVar as ClassVar
from typing import Protocol as Protocol
from typing import runtime_checkable as runtime_checkable
from datetime import datetime as datetime
import dataclasses as dataclasses
import hashlib as hashlib
import importlib as importlib
import json as json
import math as math
from doeff import EffectBase as EffectBase
from doeff_core_effects.effects import Ask as Ask
from doeff_core_effects.scheduler import Spawn as Spawn
from doeff_core_effects.scheduler import Wait as Wait
from doeff_core_effects.scheduler import Gather as Gather
from doeff_core_effects.scheduler import Race as Race
from doeff_core_effects.scheduler import Cancel as Cancel
from doeff_core_effects.scheduler import CreatePromise as CreatePromise
from doeff_core_effects.scheduler import CompletePromise as CompletePromise
from doeff_core_effects.scheduler import FailPromise as FailPromise
from doeff_core_effects.scheduler import CreateSemaphore as CreateSemaphore
from doeff_core_effects.scheduler import AcquireSemaphore as AcquireSemaphore
from doeff_core_effects.scheduler import ReleaseSemaphore as ReleaseSemaphore
from doeff_core_effects.scheduler import Task as Task
from doeff_core_effects.scheduler import Promise as Promise
from doeff_core_effects.scheduler import Future as Future
from doeff_core_effects.scheduler import Semaphore as Semaphore
from doeff_time import GetTimeEffect as GetTimeEffect
from doeff_time import GetMonotonicEffect as GetMonotonicEffect
from doeff_time import DelayEffect as DelayEffect
from doeff_hy.json_value import OpaqueJson as OpaqueJson
FORMAT_VERSION: int
READABLE_FORMATS: tuple[int, ...]
DELTA_MIN_CHARS: int
INTERN_MIN_CHARS: int
BLOB_MEMORY_MAX: int
JsonValue: TypeAlias = None | bool | int | float | str | list | dict
READ: str
LIVE: str
DECISION: str
OUTPUT: str
LOOSE: tuple[str, ...]

class UnencodableValue(TypeError):
    ...

class UnrecordableEffect(TypeError):
    ...

class MalformedRecordSpec(TypeError):
    ...

class _Diverge:

    def __repr__(self) -> str:
        ...
DIVERGE: _Diverge

class ReplayHandle:

    def __init__(self, kind: str, ref: str) -> None:
        ...

    def __eq__(self, other: object) -> bool:
        ...

    def __hash__(self) -> int:
        ...

    def __repr__(self) -> str:
        ...

class ReplaySemaphore(ReplayHandle, Semaphore):

    def __init__(self, kind: str, ref: str) -> None:
        ...

    def __eq__(self, other: object) -> bool:
        ...

    def __hash__(self) -> int:
        ...

def handle_for(kind: str, ref: str) -> ReplayHandle:
    ...

@runtime_checkable
class DataclassValue(Protocol):
    __dataclass_fields__: ClassVar[dict]
RestoredValue: TypeAlias = None | bool | int | float | str | bytes | datetime | ReplayHandle | list | tuple | dict | BaseException | DataclassValue

class RecordedError(Exception):

    def __init__(self, type_name: str, message: str) -> None:
        ...

def type_name(cls: Incomplete) -> str:
    ...
MOVED_MODULES: Incomplete
MOVED_TYPES: Incomplete

def resolve_type(name: str) -> type | None:
    ...

class HandleTable:

    def __init__(self) -> None:
        ...

    def bind(self, obj: object, kind: str, ref: str | None) -> None:
        ...

    def name_of(self, obj: object) -> tuple | None:
        ...

def _hyx_plain_keyXquestion_markX(k: Incomplete) -> Incomplete:
    ...

def encode_value(v: object, handles: HandleTable | None=None) -> JsonValue:
    ...

def encode_error(e: BaseException, handles: HandleTable | None=None) -> dict:
    ...

def decode_value(j: JsonValue) -> RestoredValue:
    ...

def decode_error(j: dict) -> BaseException:
    ...

def canonical(j: JsonValue) -> str:
    ...

def short_hash(text: str) -> str:
    ...

def _list_ops(prev: list, new: list) -> list:
    ...

def delta_of(prev: JsonValue, new: JsonValue) -> dict:
    ...

def apply_delta(prev: JsonValue, delta: dict) -> JsonValue:
    ...

def content_hash(text: str) -> str:
    ...

class BlobMemory:

    def __init__(self, max_size: int=...) -> None:
        ...

    def __contains__(self, h: str) -> bool:
        ...

    def add(self, h: str) -> None:
        ...

    def __len__(self) -> int:
        ...

def intern_json(j: JsonValue, seen: BlobMemory, emit: Callable, min_chars: int=...) -> JsonValue:
    ...

def resolve_refs(j: JsonValue, blobs: dict, memo: dict | None=None) -> JsonValue:
    ...

class EffectCodec:

    def __init__(self, cls: type, mode: str | Callable, args: Callable | None=None, subject: Callable | None=None, unexecuted: _Diverge | bool | None=..., binds: str | None=None, watch: bool=False, arg_names: tuple | None=None) -> None:
        ...

def _fields_args(asked: EffectBase, handles: HandleTable) -> _Program[dict, object]:
    ...

def _loose_value(v: Incomplete, handles: Incomplete) -> Incomplete:
    ...

def _handle_mode(attr: Incomplete) -> Incomplete:
    ...
SPEC_ATTRIBUTE: str
SPEC_FIELDS: tuple[str, ...]
_SPEC_MODES: tuple[str, ...]
_SPEC_BINDS: tuple[str, ...]
_SPEC_UNEXECUTED: dict[str, _Diverge | bool | None]
_REGISTRY: Incomplete
_DECLARED: Incomplete

def register(codec: EffectCodec) -> EffectCodec:
    ...

def _spec_subject(field: str) -> Callable:
    ...

def _codec_from_spec(cls: type) -> EffectCodec:
    ...

def _declared_codec(cls: type) -> EffectCodec | None:
    ...

def can_record(cls: type) -> bool:
    ...

def codec_of(effect: object) -> EffectCodec:
    ...

def mode_of(effect: object, handles: HandleTable) -> str:
    ...

def args_of(asked: EffectBase, handles: HandleTable) -> _Program[dict, object]:
    ...

def subject_of(effect: object, args: dict) -> str | None:
    ...

def registered_types() -> list:
    ...
