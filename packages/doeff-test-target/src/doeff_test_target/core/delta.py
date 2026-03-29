from doeff import do
from doeff import Ask


def helper_delta():
    return delta()


@do
def delta():
    yield Ask("delta")
    return "delta"
