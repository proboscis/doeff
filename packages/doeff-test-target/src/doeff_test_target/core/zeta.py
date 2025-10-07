from doeff import do
from doeff.effects import ask


def helper_zeta():
    return zeta()


@do
def zeta():
    yield ask("zeta")
    return "zeta"
