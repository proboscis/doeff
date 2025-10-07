from doeff import do
from doeff.effects import ask


def helper_alpha():
    return alpha()


@do
def alpha():
    yield ask("alpha")
    return "alpha"
