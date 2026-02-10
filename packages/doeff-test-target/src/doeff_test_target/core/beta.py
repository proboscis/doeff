from doeff import do
from doeff.effects import ask, tell


def helper_beta():
    return beta()


@do
def beta():
    yield ask("beta")
    yield tell("beta")
    return "beta"
