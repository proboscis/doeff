"""
Helper functions for doeff-traverse.
"""

from doeff import do
from doeff_traverse.effects import Fail


@do
def try_call(f, *args, **kwargs):
    """Wrap a plain Python function call as a yield site.

    If f raises, performs Fail(exception). Handler can Resume(k, value)
    to inject a substitute, or let it propagate as an exception.

    Usage:
        (<- obj (try-call parse-json raw))
    """
    try:
        return f(*args, **kwargs)
    except Exception as e:
        return (yield Fail(e))
