from __future__ import annotations

from doeff import Program, default_handlers, run


# doeff: interpreter, default
def auth_interpreter(program: Program):
    result = run(program, handlers=default_handlers())
    return result.value


# doeff: default
auth_env: Program[dict] = Program.pure({"auth_method": "oauth2"})
