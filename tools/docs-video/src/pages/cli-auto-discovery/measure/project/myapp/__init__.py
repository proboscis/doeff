# The root of the page's example project (docs/14-cli-auto-discovery.md, "Environment Accumulation"): a default
# interpreter and the base env.
from doeff_core_effects.handlers import reader, state, writer
from doeff_core_effects.scheduler import scheduled

from doeff import Program, Pure, run


def base_interpreter(prog: Program, *, env: Program[dict] = Pure({})):
    """# doeff: interpreter, default"""
    return run(scheduled(state()(writer(reader(env=run(env))(prog)))))


# doeff: default
base_env: Program[dict] = Pure({"db_host": "localhost", "timeout": 10, "debug": False})


# A marked env without a type annotation: the indexer skips it, so its timeout never reaches the program.
# doeff: default
plain_env = Pure({"timeout": 99})
