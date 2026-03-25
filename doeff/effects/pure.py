"""Compat: doeff.effects.pure — PureEffect stub."""
from doeff.program import Pure

class PureEffect:
    """Compat: PureEffect wraps a value."""
    def __init__(self, value):
        self.value = value
