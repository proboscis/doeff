# doeff_hy.static_stub が作った型の宣言 — 手で直さない(元 = program_codec.hy・作り直し = python -m doeff_hy.static_stub --write <この .pyi の隣の .hy>)

from _typeshed import Incomplete
import base64 as base64
import collections as collections
import io as io
import cloudpickle as cloudpickle
from types import NotImplementedType as NotImplementedType
from doeff import Program as Program
from doeff_cluster.shared.intent.remote_model import RemoteJobFailed as RemoteJobFailed
from doeff_cluster.shared.intent.remote_model import UnsendableProgram as UnsendableProgram
from doeff_cluster.shared.intent.remote_model import TaskSucceeded as TaskSucceeded
from doeff_cluster.shared.intent.remote_model import TaskFailed as TaskFailed

def _refuse_file(value: Incomplete) -> Incomplete:
    ...
CLOUDPICKLE_DISPATCH: Incomplete

class StrictPickler(cloudpickle.CloudPickler):
    dispatch_table = ...

    def reducer_override(self, obj: object) -> tuple | NotImplementedType:
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
