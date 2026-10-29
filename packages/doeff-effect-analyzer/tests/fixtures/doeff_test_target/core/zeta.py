from doeff import Ask, do


def helper_zeta():
    return zeta()


@do
def zeta():
    yield Ask("zeta")
    return "zeta"
