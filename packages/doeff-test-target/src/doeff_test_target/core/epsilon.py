from doeff import do
from doeff import Ask


def helper_epsilon():
    return epsilon()


@do
def epsilon():
    yield Ask("epsilon")
    return "epsilon"
