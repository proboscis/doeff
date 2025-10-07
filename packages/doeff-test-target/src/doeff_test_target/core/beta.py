from doeff import do
from doeff.effects import ask, emit


def helper_beta():
    return beta()


@do
def beta():
    yield ask("beta")
    yield emit("beta")
    return "beta"
