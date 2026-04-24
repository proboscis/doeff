from __future__ import annotations

from doeff import Program, default_handlers, run
from tests._run_helpers import run_with_defaults


# doeff: interpreter, default
def auth_interpreter(program: Program):
    result = run_with_defaults(program)
    return result.value


# doeff: default
auth_env: Program[dict] = Program.pure({"auth_method": "oauth2"})
