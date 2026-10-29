# The root of the video's example project: a default interpreter and the base env. The interpreter runs the
# program under a reader holding the merged envs, so every Ask the program yields is answered from them.
from doeff_core_effects import reader, scheduled

from doeff import Program, Pure, run


def shop_interpreter(prog: Program, *, env: Program[dict] = Pure({})):
    """# doeff: interpreter, default"""
    return run(scheduled(reader(env=run(env))(prog)))


# doeff: default
shop_env: Program[dict] = Pure({"currency": "EUR", "tax_rate": 0.2})
