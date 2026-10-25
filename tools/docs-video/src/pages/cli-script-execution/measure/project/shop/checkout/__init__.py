# The closest default interpreter to cart.py, and an env that overrides the base tax rate.
from doeff_core_effects.handlers import reader
from doeff_core_effects.scheduler import scheduled

from doeff import Program, Pure, run


def checkout_interpreter(prog: Program, *, env: Program[dict] = Pure({})):
    """# doeff: interpreter, default"""
    return run(scheduled(reader(env=run(env))(prog)))


# doeff: default
checkout_env: Program[dict] = Pure({"tax_rate": 0.1})
