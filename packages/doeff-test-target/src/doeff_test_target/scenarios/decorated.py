from functools import wraps

from doeff import do

from ..core.alpha import helper_alpha


def noop_decorator(func):
    @wraps(func)
    def wrapper(*args, **kwargs):
        return func(*args, **kwargs)

    return wrapper


@noop_decorator
@do
def decorated_alpha():
    result = yield helper_alpha()
    return result
