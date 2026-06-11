from __future__ import annotations

from doeff import Program
from doeff import default_handlers as default_handlers
from doeff import run as run
from tests._run_helpers import run_with_defaults


# doeff: interpreter, default
def auth_interpreter(program: Program):
    result = run_with_defaults(program)
    return result.value


# doeff: default
auth_env: Program[dict] = Program.pure({"auth_method": "oauth2"})
