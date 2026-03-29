from doeff import do
from doeff import Ask


def helper_alpha():
    return alpha()


@do
def alpha():
    yield Ask("alpha")
    return "alpha"
