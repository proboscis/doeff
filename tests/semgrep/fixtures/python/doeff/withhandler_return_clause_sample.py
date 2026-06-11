from __future__ import annotations

import doeff_vm

from doeff import WithHandler
from doeff.program import _WithHandlerNode


def handler(effect: object, continuation: object) -> object:
    return effect


def post_process(value: object) -> object:
    return value


expr = object()
third_arg = post_process

bad_keyword = WithHandler(handler, expr, return_clause=third_arg)
bad_positional = WithHandler(handler, expr, third_arg)
bad_vm_keyword = doeff_vm.WithHandler(handler, expr, return_clause=third_arg)
bad_vm_positional = doeff_vm.WithHandler(handler, expr, third_arg)


def shim_forward(h: object, body: object, *args: object, **kwargs: object) -> object:
    return _WithHandlerNode(h, body, *args, **kwargs)
