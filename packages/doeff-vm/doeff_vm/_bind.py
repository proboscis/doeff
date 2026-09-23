"""``yield from`` support for effects and program nodes.

``EffectBase.__iter__`` and every DoExpr node's ``__iter__`` return
``bind(self)``: a one-step generator that yields the node to the VM and returns
whatever the VM sends back. So inside a ``@do`` body

    rows = yield from ReadShared("turn/")     # same as: rows = yield ReadShared("turn/")

evaluates the same way as a plain ``yield``, and a type checker reads the result
type from the effect's ``EffectBase[T]`` (see doeff_vm/__init__.pyi).
"""


def bind(node):
    return (yield node)
