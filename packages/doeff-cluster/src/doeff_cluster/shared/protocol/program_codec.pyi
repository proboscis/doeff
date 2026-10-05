# doeff_hy.static_stub が作った型の宣言 — 手で直さない(元 = program_codec.hy・作り直し = python -m doeff_hy.static_stub --write <この .pyi の隣の .hy>)

from _typeshed import Incomplete
from doeff import Program as _Program
import cloudpickle as cloudpickle
from types import CodeType as CodeType
from types import FunctionType as FunctionType
from types import NotImplementedType as NotImplementedType
from doeff import Program as Program
from doeff import run as run
from doeff_cluster.shared.intent.remote_model import RemoteJobFailed as RemoteJobFailed
from doeff_cluster.shared.intent.remote_model import UnsendableProgram as UnsendableProgram
from doeff_cluster.shared.intent.remote_model import TaskSucceeded as TaskSucceeded
from doeff_cluster.shared.intent.remote_model import TaskFailed as TaskFailed

def _refuse_file(value: Incomplete) -> Incomplete:
    ...
CLOUDPICKLE_DISPATCH: Incomplete

def source_module_name(names: dict, file: str) -> _Program[str, object]:
    ...

def portable_source_path(module_name: str, file: str) -> _Program[str, object]:
    ...

def portable_code(code: CodeType, portable: str) -> _Program[CodeType, object]:
    ...

def portable_reduction(func: FunctionType, reduced: tuple) -> _Program[tuple, object]:
    ...

class StrictPickler(cloudpickle.CloudPickler):
    dispatch_table = ...

    def reducer_override(self, obj: object) -> tuple | NotImplementedType:
        ...
TEXT_OPCODES: frozenset[str]
BYTES_OPCODES: frozenset[str]
PUT_OPCODES: frozenset[str]
GET_OPCODES: frozenset[str]

def pickle_ops(data: bytes) -> _Program[list, object]:
    ...

def memo_indices(ops: list) -> _Program[list, object]:
    ...

def memo_aliases(ops: list, indices: list) -> _Program[dict, object]:
    ...

def canonical_pickle(data: bytes) -> _Program[bytes, object]:
    ...

def _dumps(program: Incomplete) -> bytes:
    ...

def encode_program(program: Program) -> str:
    ...

def decode_program(blob: str) -> Program:
    ...

def encode_outcome(outcome: TaskSucceeded | TaskFailed) -> str:
    ...

def decode_outcome(blob: str) -> TaskSucceeded | TaskFailed:
    ...
