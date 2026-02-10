from doeff import do
from doeff.effects import ask, tell


def helper_gamma():
    return gamma()


@do
def gamma():
    value = yield ask("gamma")
    yield tell("gamma")
    return value
