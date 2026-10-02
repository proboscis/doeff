# doeff_hy.static_stub が作った型の宣言 — 手で直さない(元 = job_entry.hy・作り直し = python -m doeff_hy.static_stub --write <この .pyi の隣の .hy>)

from doeff import Program as _Program
import argparse as argparse
from doeff import run as run
from doeff import with_handlers as with_handlers
from doeff_core_effects.file_effects import FileFailed as FileFailed
from doeff_core_effects.file_effects import ReadText as ReadText
from doeff_core_effects.file_effects import WriteText as WriteText
from doeff_core_effects.file_effects import file_done as file_done
from doeff_core_effects.os_file import os_file_handler as os_file_handler
from doeff_cluster.shared.intent.remote_model import TaskSucceeded as TaskSucceeded
from doeff_cluster.shared.intent.remote_model import TaskFailed as TaskFailed
from doeff_cluster.shared.intent.remote_model import VersionMismatch as VersionMismatch
from doeff_cluster.shared.intent.remote_model import RemoteJobFailed as RemoteJobFailed
from doeff_cluster.shared.core.remote_rules import version_diffs as version_diffs
from doeff_cluster.shared.core.remote_rules import diffs_text as diffs_text
from doeff_cluster.shared.core.remote_rules import failed_from as failed_from
from doeff_cluster.shared.protocol.program_codec import decode_program as decode_program
from doeff_cluster.shared.protocol.program_codec import encode_outcome as encode_outcome
from doeff_cluster.foundation.process_versions import this_process_versions as this_process_versions
from doeff_cluster.shared.intent.run_context import RunContext as RunContext
from doeff_cluster.shared.core.run_context_rules import runtime_env_of_context as runtime_env_of_context
from doeff_cluster.shared.entry.run_context_env import context_from_env as context_from_env
from doeff_cluster.worker.entry.result_delivery import deliver_task_result as deliver_task_result

def parsed_program_row(path: str, text: str | FileFailed) -> _Program[dict | list | str | int | float | bool | None | RemoteJobFailed, object]:
    ...

def program_row(path: str) -> _Program[dict | RemoteJobFailed, object]:
    ...

def decoded_program(blob: str) -> tuple:
    ...

def read_program(path: str, env_key: str) -> tuple:
    ...

def run_service(args: argparse.Namespace) -> None:
    ...

def task_outcome(program_path: str, ctx: RunContext) -> TaskSucceeded | TaskFailed:
    ...

def run_probe(args: argparse.Namespace) -> None:
    ...

def run_task(args: argparse.Namespace) -> None:
    ...

def main() -> None:
    ...
