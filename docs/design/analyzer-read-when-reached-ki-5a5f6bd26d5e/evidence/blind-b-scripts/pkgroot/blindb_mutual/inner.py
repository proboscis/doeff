from doeff import do

from blindb_mutual.effects import Y, Z


@do
def asks_y():
    yield Y()


@do
def asks_z():
    yield Z()
