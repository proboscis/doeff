from __future__ import annotations

from doeff import Program, default_handlers, run


# doeff: interpreter, default
def base_interpreter(program: Program):
    result = run(program, handlers=default_handlers())
    return result.value


# doeff: default
base_env: Program[dict] = Program.pure({"timeout": 10})
