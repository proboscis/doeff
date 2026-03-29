from doeff import do
from doeff import Ask, Tell


def helper_beta():
    return beta()


@do
def beta():
    yield Ask("beta")
    yield Tell("beta")
    return "beta"
