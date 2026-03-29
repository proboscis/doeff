from doeff import do
from doeff import Ask


def helper_zeta():
    return zeta()


@do
def zeta():
    yield Ask("zeta")
    return "zeta"
