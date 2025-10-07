from doeff import do
from doeff.effects import ask, log


def helper_gamma():
    return gamma()


@do
def gamma():
    value = yield ask("gamma")
    yield log("gamma")
    return value
