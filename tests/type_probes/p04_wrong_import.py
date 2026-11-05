# A way of importing that raises ImportError at runtime (handlers are not in doeff) -- type checks also flag the import line as an error (gap 3 -- fixed)
from doeff import state  # expect-error
from doeff import no_such_name  # expect-error

__all__ = ["no_such_name", "state"]  # noqa: DOEFF021 -- the type-sample file intentionally has a wrong __all__
