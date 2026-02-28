
import builtins

from doeff.do import do


@do
def _list(*values):
    return builtins.list(values)


@do
def _tuple(*values):
    return builtins.tuple(values)


@do
def _set(*values):
    return builtins.set(values)


@do
def _dict(**kwargs):
    return kwargs
