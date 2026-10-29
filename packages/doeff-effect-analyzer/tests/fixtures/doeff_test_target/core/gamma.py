from doeff import Ask, Tell, do


def helper_gamma():
    return gamma()


@do
def gamma():
    value = yield Ask("gamma")
    yield Tell("gamma")
    return value
