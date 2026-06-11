from doeff import Ask, Tell, do


def helper_beta():
    return beta()


@do
def beta():
    yield Ask("beta")
    yield Tell("beta")
    return "beta"
