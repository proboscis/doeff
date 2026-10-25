# The closest default interpreter to login.py, and the env that overrides timeout again.
from doeff_core_effects.handlers import reader, state, writer
from doeff_core_effects.scheduler import scheduled

from doeff import Program, Pure, run


def auth_interpreter(prog: Program, *, env: Program[dict] = Pure({})):
    """# doeff: interpreter, default"""
    return run(scheduled(state()(writer(reader(env=run(env))(prog)))))


# doeff: default
auth_env: Program[dict] = Pure({"oauth_provider": "google", "timeout": 30})
