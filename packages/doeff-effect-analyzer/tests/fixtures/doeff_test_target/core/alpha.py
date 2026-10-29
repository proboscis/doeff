from doeff import Ask, do


def helper_alpha():
    return alpha()


@do
def alpha():
    yield Ask("alpha")
    return "alpha"
