from doeff import Ask, do


def helper_epsilon():
    return epsilon()


@do
def epsilon():
    yield Ask("epsilon")
    return "epsilon"
