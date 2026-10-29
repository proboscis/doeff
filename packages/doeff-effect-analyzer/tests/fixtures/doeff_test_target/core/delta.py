from doeff import Ask, do


def helper_delta():
    return delta()


@do
def delta():
    yield Ask("delta")
    return "delta"
