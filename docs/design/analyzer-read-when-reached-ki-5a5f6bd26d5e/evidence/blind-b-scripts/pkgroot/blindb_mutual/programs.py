from doeff import do
from doeff.program import with_handlers

from blindb_mutual.effects import X, Y
from blindb_mutual.handlers import handler_a, handler_b


@do
def asks_x():
    yield X()


@do
def asks_y_here():
    yield Y()


@do
def warm():
    # A の下で X: A の X の節 → B → B の Y の節 → A(本当の再帰)
    yield with_handlers([handler_a], asks_x())


@do
def job():
    # B の下で Y: B の Y の節 → A → A の Z の節(再帰は無い)
    yield with_handlers([handler_b], asks_y_here())
