from doeff_core_effects import Ask
from doeff_core_effects.handlers import reader

from doeff import Pass, Resume, handler, run


@handler
def trace(effect: Ask, k):
    print("asked:", effect.key)
    yield Pass(effect, k)


@handler
def free(effect: Ask, k):
    if effect.key == "tax_rate":
        return (yield Resume(k, 0.0))
    yield Pass(effect, k)


env = {"tax_rate": 0.1, "currency": "EUR"}
print("traced:", run(reader(env=env)(trace(program))))
print("tax-free:", run(reader(env=env)(free(program))))
