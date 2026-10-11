from doeff import Pass, Transfer
from doeff.program import with_handlers

from blindb_mutual.effects import X, Y, Z
from blindb_mutual.inner import asks_y, asks_z


def handler_a(effect, k):
    if isinstance(effect, X):
        yield with_handlers([handler_b], asks_y())  # A の X の節は B を入れる
        return (yield Transfer(k, None))
    if isinstance(effect, Z):
        return (yield Transfer(k, None))
    yield Pass(effect, k)


def handler_b(effect, k):
    if isinstance(effect, Y):
        yield with_handlers([handler_a], asks_z())  # B の Y の節は A を入れる
        return (yield Transfer(k, None))
    yield Pass(effect, k)
