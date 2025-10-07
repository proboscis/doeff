from doeff import do
from doeff.effects import ask


def helper_delta():
    return delta()


@do
def delta():
    yield ask("delta")
    return "delta"
